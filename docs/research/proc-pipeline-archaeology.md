# Proc pipeline archaeology

## Scope and result

This pass reconstructs, from direct TrinityCore consumers and the checked-in
game-data snapshot, how a proc provider is defined, when it is eligible, how
its chance is computed, when randomness is drawn, what state it mutates and
what it triggers. It is research only: nothing here touches Core, the
executable catalogs, the production schemas or runtime combat, and nothing here
proposes a production proc architecture.

Deliverables:

| artefact | path |
|---|---|
| this report | `docs/research/proc-pipeline-archaeology.md` |
| research package | `scripts/research/procs/` (standard library only) |
| CLI | `scripts/research/proc_research.py` |
| Trinity world-DB overlay extractor | `scripts/research/tools/tdb_proc_overlay.py` |
| name-table generator | `scripts/research/tools/gen_proc_names.py` |
| differential C++ probe | `scripts/research/tools/tc_proc_probe/` |
| tests | `scripts/research/tests/test_procs_*.py`, `tests/proc_synthetic.py` |
| committed corpora | `docs/research/procs-corpora/` |

Headline results:

1. **Two separate layers define a proc.** The DB2 snapshot supplies the defaults
   (`SpellAuraOptions`, `SpellProcsPerMinute[Mod]`, `SpellEffect`, `SpellMisc`
   …). Trinity then builds a `SpellProcEntry` either from a `spell_proc`
   world-database row (merged with those defaults) or by *generating* one from
   the DB2 data. The generated path is a real algorithm with its own rules
   (§2.2), and it is part of the semantics. The world-database layer is not in
   `data/tables`. It was extracted from the TDB dump this checkout pins (§25).
2. **The pipeline has two phases per event.** For both sides of an event, every
   aura is visited, filtered, rolled and "prepared" (charge drop, cooldown,
   success time) *before any* triggered action runs. The actor's actions then
   execute, followed by the action target's (§2, §8).
3. **The immutable/mutable split holds.** Every chance, cooldown, charge, event
   and suppression policy in the generic path is decided by immutable data plus
   explicit event and actor facts. The only mutable proc state Trinity keeps is
   five members of the `Aura` object (§11, §26). Scripts, conditions and spell
   mods are the only other writers or readers, and each is surfaced explicitly.
4. **The chance models are fixed, classic PPM, RPPM (+ modifiers), and two
   legacy item/enchant paths.** RPPM is `SpellAuraOptions.SpellProcsPerMinuteID`
   → `BaseProcRate` > 0. Classic PPM exists only in `spell_proc`,
   `spell_enchant_proc_data` and `item_template_addon`, never in DB2.
   `SpellProcsPerMinute.Flags` has **no consumer** (§4, §5).
5. **Trinity's arithmetic is reproduced bit-exactly.** The Python oracle matches
   Trinity's own compiled code for default generation on all 14,176 real
   spells with DB2 ProcFlags, 20,000 random `CanSpellTriggerProcOnEvent` cases, every
   real RPPM modifier set, and the classic-PPM, RPPM and `CalcProcChance`
   expressions in binary32 (§20).
6. **Current player content is mostly not generic in Trinity.** Of 1,195
   providers reachable from current class trees, specialization spells and
   current raid/M+ gear, 280 fit a proved generic shape. 550 are
   DUMMY/PROC_TRIGGER_SPELL auras with no trigger spell, which Trinity cannot
   execute without a script, and 15 carry the unnamed `ProcFlags2` bit `0x20`
   (§15, §26).

Everything is fail-closed. Unknown flag bits, unported consumers (spell mods,
conditions, scripts, `IsPositive`), and unproved modifier semantics are
reported as unknowns and never given a default.

---

## 1. Proc source vocabulary

The typed inventory lives in `procs/vocabulary.py` (`FieldSpec`). With value
counts it is `proc_research.py vocabulary --census`, committed as
`procs-corpora/vocabulary.json`. Roles follow the brief: provider, event,
chance, cooldown, charge, target and suppression policy, action, acquisition,
plus three roles the evidence required: `downstream-cast` (read by the
*triggered* spell's cast, not by proc gating), `lifetime`, and `unconsumed`.

### 1.1 DB2 tables that participate

| table.field | DB2 type | direct consumer | role |
|---|---|---|---|
| SpellAuraOptions.ProcTypeMask_0/_1 | int32×2 | `SpellInfo::ProcFlags` → `SpellProcEntry::ProcFlags` | event matching |
| SpellAuraOptions.ProcChance | uint8 | `SpellInfo::ProcChance` → `SpellProcEntry::Chance`; legacy item path | chance |
| SpellAuraOptions.ProcCharges | int32 | `SpellInfo::ProcCharges` (uint32) → `Aura::CalcMaxCharges` (uint8) | charges |
| SpellAuraOptions.ProcCategoryRecovery | int32 | `SpellInfo::ProcCooldown` → `SpellProcEntry::Cooldown` | cooldown |
| SpellAuraOptions.SpellProcsPerMinuteID | uint16 | `SpellInfo::ProcBasePPM`, `ProcPPMMods` | chance (RPPM) |
| SpellAuraOptions.CumulativeAura | uint16 | `SpellInfo::StackAmount` | charges (only with `USE_STACKS_FOR_CHARGES`) |
| SpellAuraOptions.DifficultyID | int16 | `LoadSpellInfoStore` key + fallback | identity |
| SpellProcsPerMinute.BaseProcRate | float | `SpellInfo::CalcProcPPM` | chance |
| SpellProcsPerMinute.Flags | int32 | **none** | unconsumed |
| SpellProcsPerMinuteMod.Type/Param/Coeff | int32/int32/float | `CalcProcPPM`, `CalcPPM{Haste,Crit,ItemLevel}Mod` | chance |
| SpellEffect.Effect / EffectAura / EffectIndex | uint32/int16/int32 | generation (`isTriggerAura`), `CheckEffectProc`, `HandleProc`, `DisableEffectsMask` | provider policy / action |
| SpellEffect.EffectTriggerSpell | int32 | `HandleProcTriggerSpell[WithValue]AuraProc` | action |
| SpellEffect.EffectSpellClassMask_0..3 | flag128 | generated `SpellFamilyMask` | event matching |
| SpellEffect.EffectBasePointsF | float | generated `MOD_HIT_CHANCE` hit mask; `PROC_TRIGGER_DAMAGE` / `WITH_VALUE` / breakable-CC amounts | action |
| SpellEffect.EffectMiscValue_0 | int32 | `CheckEffectProc` mechanic/school gates | event matching |
| SpellEffect.EffectMechanic | int32 | `GetAllEffectsMechanicMask` (event spell) | event matching |
| SpellEffect.ImplicitTarget_0/1 | int32 | triggered spell target selection | downstream cast |
| SpellEffect.ScalingClass | int32 | `CalcBaseValue` (not ported) | action (amounts only) |
| SpellMisc.Attributes_0..16 | int32×17 | every attribute gate in §9 | suppression / cooldown / target |
| SpellMisc.SchoolMask | uint8 | `ProcEventInfo::GetSchoolMask` (spell events) | event matching |
| SpellMisc.DurationIndex | uint16 | `Aura::CalcMaxDuration` | lifetime |
| SpellClassOptions.SpellClassSet / SpellClassMask | uint8 / flag128 | `SpellInfo::IsAffected`; generated family name | event matching |
| SpellCategories.DefenseType | int8 | `Spell::prepareDataForTriggerSystem` (DmgClass) | event identity |
| SpellCategories.Mechanic | int8 | `GetAllEffectsMechanicMask` | event matching |
| SpellCategories.Category / ChargeCategory | int16 | `SpellHistory`, bypassed for triggered casts | downstream cast |
| SpellCooldowns.RecoveryTime / CategoryRecoveryTime | int32 | `SpellHistory::HandleCooldowns`, skipped when `IsIgnoringCooldowns()` | downstream cast |
| SpellCategory.ChargeRecoveryTime | int32 | `SpellHistory` charges | downstream cast |
| SpellEquippedItems.* | int32×3 | `GetProcEffectMask` passive equipment gate; wand event identity | provider policy |
| SpellPower.* | — | `Spell::GetPowerCost` → `PROC_ATTR_REQ_POWER_COST` (runtime) | event matching |
| SpellInterrupts.AuraInterruptFlags | int32×2 | `RemoveAurasWithInterruptFlags` | lifetime, never gates a proc |
| SpellAuraRestrictions.* | — | `Spell::CheckCast` of the triggered cast | downstream cast |
| SpellTargetRestrictions.* | — | triggered spell targeting | downstream cast |
| SpellShapeshift.* | — | `CheckCast`, ignored under `TRIGGERED_IGNORE_SHAPESHIFT` | downstream cast |
| SpellLabel.LabelID | uint32 | label spell mods, not proc gating | downstream cast |
| SkillLineAbility.SupercedesSpell | int32 | `LoadSpellRanks` (all-ranks `spell_proc` / script rows) | identity |
| Difficulty.FallbackDifficultyID | int16 | `GetSpellInfo`, `GetSpellProcEntry` | identity |
| RandPropPoints.SuperiorF_0 | float | `CalcPPMItemLevelMod` → `GetRandomPropertyPoints(RARE, CHEST)` | chance |
| ItemEffect / ItemXItemEffect / ItemSparse / SpellItemEnchantment / GemProperties / ItemSetSpell | — | gear acquisition (§12) | acquisition |
| ItemSparse.ItemDelay | uint16 | enchant classic PPM (`proto->GetDelay()`) | chance |
| SpellItemEnchantment.EffectPointsMin_n | int16 | enchant combat-spell chance | chance |
| TraitDefinition / TraitNode* / TraitTreeLoadout / SpecializationSpells / ChrSpecialization | — | acquisition roots (§13) | acquisition |
| TraitDefinitionEffectPoints.CurveID | int32 | `SpellEffectInfo::CalcValue` trait override | action (amount only) |
| SpellReplacement | — | **not loaded** (no `DB2Storage`) | unconsumed |

`SpellAuraOptions` has 32,162 rows. 14,220 have nonzero ProcFlags, and 37 of
those are at a non-zero difficulty. `ProcChance = 101` appears on 6,741
flagged rows, and `ProcCharges = -1` on 5.

### 1.2 World-database fields (not in `data/tables`)

| table | consumer | what it changes |
|---|---|---|
| `spell_proc` (1,280 rows after replay) | `SpellMgr::LoadSpellProcs` | the whole `SpellProcEntry`; zero fields fall back to DB2 |
| `spell_enchant_proc_data` (16) | `Player::CastItemCombatSpell` | enchant chance / PPM / hit mask / white-hit-only |
| `item_template_addon.SpellPPMChance` (625 rows, nonzero subset kept) | `Player::CastItemCombatSpell` | legacy item classic PPM |
| `conditions` source 24 (2 rows) | `Aura::GetProcEffectMask` | live actor/target predicates |
| `spell_script_names` (4,016) | `ObjectMgr::LoadSpellScriptNames` | binds AuraScript proc hooks |
| `spell_custom_attr` (141) | `SpellInfo::AttributesCu` | `CU_DONT_BREAK_STEALTH` is the only proc-relevant bit |

There is also an in-code layer. `SpellMgr::LoadSpellInfoCorrections` touches
373 spells, and 29 of those touch proc-relevant members. The overlay extractor
records every `spellInfo->X` member each fix lambda mentions. The one generic
correction, "disable proc for magnet auras", is ported. Individual fixes are
reported per provider, never applied.

---

## 2. Trinity's execution pipeline

### 2.1 Call graph (event → action)

```
combat event                                       (§3 lists all 25 call sites)
└─ Unit::ProcSkillsAndAuras(actor, actionTarget, typeMaskActor, typeMaskActionTarget,
   │                         spellTypeMask, spellPhaseMask, hitMask, spell, damageInfo, healInfo)
   │   Unit.cpp:5569
   ├─ [gate] spell && spell->GetProcChainLength() >= 10 → return      (whole event dropped)
   ├─ ProcSkillsAndReactives (aura-state side effects only; SUPPRESS_* attrs gate these)
   └─ actor->TriggerAurasProcOnEvent(...)                               Unit.cpp:10580
      ├─ PHASE A — collect, per side
      │   actor side (only if typeMaskActor != 0):
      │     GetProcAurasTriggeredOnEvent(actor auras, myProcEventInfo)   Unit.cpp:10535
      │     + mod-owner auras that modified the spell (pets; push_front order)
      │   target side (only if typeMaskActionTarget != 0 and actionTarget):
      │     actionTarget->GetProcAurasTriggeredOnEvent(...)
      │   for each AuraApplication in std::multimap<SpellID> order:
      │     mask = Aura::GetProcEffectMask(aurApp, eventInfo, now)       SpellAuras.cpp:1834
      │       ├─ pre-RNG checks (§2.3)
      │       ├─ chance = Aura::CalcProcChance(...)                      SpellAuras.cpp:1981
      │       ├─ success = roll_chance(chance)          ← RNG DRAW
      │       └─ SetLastProcAttemptTime(now)            ← always after the draw
      │     if mask: Aura::PrepareProcToTrigger                          SpellAuras.cpp:1793
      │         (script DoPrepareProc may veto the three writes below)
      │         PrepareProcChargeDrop  (--m_procCharges)
      │         AddProcCooldown        (m_procCooldown = now + cd)
      │         SetLastProcSuccessTime (now)
      │       → list.push_back({mask, aurApp})
      │     else:
      │       SPELL_ATTR0_PROC_FAILURE_BURNS_CHARGE  → PrepareProcChargeDrop; push_back({0, aurApp})
      │       SPELL_ATTR2_PROC_COOLDOWN_ON_FAILURE  → AddProcCooldown
      └─ PHASE B — execute: actor list, then target list
          Unit::TriggerAurasProcOnEvent(eventInfo, list)                 Unit.cpp:10619
            if triggering Spell has TRIGGERED_DISALLOW_PROC_EVENTS: SetCantProc(true)
            m_procChainLength = max(m_procChainLength + 1, spell chain length)
            for each {mask, aurApp}: skip if removed;
              Aura::TriggerProcOnEvent(mask, aurApp, eventInfo)           SpellAuras.cpp:2010
                if mask: OnProc scripts (may prevent) → for each effect in mask:
                         AuraEffect::HandleProc                           SpellAuraEffects.cpp:1460
                           OnEffectProc scripts (may prevent) → generic handler →
                           AfterEffectProc
                         AfterProc scripts
                ConsumeProcCharges: USE_STACKS → ModStackAmount(-1); else remove at 0 charges
            restore m_procChainLength; SetCantProc(false)
```

### 2.2 Where the `SpellProcEntry` comes from

`SpellMgr::LoadSpellProcs` (SpellMgr.cpp:1497), ported in
`procs/definition.py`:

1. **`spell_proc` rows** are keyed at `GetSpellInfo(id, DIFFICULTY_NONE)`. For
   `all ranks` (negative ID), the row applies down the `SkillLineAbility` rank
   chain. Zero fields take the DB2 default: ProcFlags, Charges, Cooldown, and
   Chance unless the row sets ProcsPerMinute. The validation *corrects* some
   values: negative Chance or PPM becomes 0, AttributesMask is masked to
   `PROC_ATTR_ALL_ALLOWED`, and a row with `2_CAST_SUCCESSFUL` and no phase-mask
   flags gets `SpellPhaseMask = CAST`. The rest are log lines, which the oracle
   reproduces as `load_log`.
2. **Default generation** runs for every SpellInfo (every difficulty) with
   ProcFlags that has no `spell_proc` entry on its difficulty fallback chain.
   * An effect with `IsEffect()` and `ApplyAuraName ∉ isTriggerAura[]` is added
     to `DisableEffectsMask` ("to avoid losing charges on self proc"). If *no*
     trigger aura exists, no entry is generated. That applies to 1,678
     flag-carrying spells, which need a `spell_proc` row.
   * `SpellTypeMask` is the union of `spellTypeMask[aura]` (default `MASK_ALL`).
   * `SpellFamilyMask` is the OR of trigger-aura `EffectSpellClassMask`, and the
     family name is copied only if that mask is nonzero.
   * `SpellPhaseMask` is `HIT`, or `CAST` for `2_CAST_SUCCESSFUL` without any
     phase-requiring flag.
   * `HitMask` comes from a switch over aura effects **that exits the effect loop
     at the first listed aura**. REFLECT gives REFLECT, WEAPON_CRIT gives
     CRITICAL, BLOCK_PERCENT gives BLOCK, and MOD_HIT_CHANCE ≤ -100 gives MISS.
     A PROC_TRIGGER_SPELL effect first means a later REFLECT effect never sets
     the mask.
   * `TRIGGERED_CAN_PROC` is set for "always triggered" auras, and for
     PROC_TRIGGER_SPELL/DAMAGE on providers with any taken-hit flag.
     `REQ_EXP_OR_HONOR` is set for `KILL`.
   * Chance, Cooldown and Charges are copied, and `ProcsPerMinute = 0`.
   * **Infinite-loop guard:** no entry if the spell has
     `CAN_PROC_FROM_PROCS`, no family mask, chance ≥ 100, no RPPM, no cooldown,
     no charges, a "deal" flag in the guard set, and a nonzero trigger spell.
     This applies to 49 spells, 18 of them in player scope.
   * Generated entries are merged *after* the loop, so only `spell_proc` rows
     suppress generation.
3. **Lookup** (`GetSpellProcEntry`) walks exact difficulty, then
   `FallbackDifficultyID` repeatedly.

The generated half is compared against Trinity's own compiled code for all
real spells (§20).

### 2.3 Check ordering relative to RNG

Consumer order inside `Aura::GetProcEffectMask`, reproduced step by step by
`procs.eligibility.Evaluator`:

| # | check | consumer | needs |
|---|---|---|---|
| — | chain length ≥ 10 (Spell events only) | `ProcSkillsAndAuras` | event |
| — | side has a nonzero mask; target side needs an action target | `TriggerAurasProcOnEvent` | event |
| 1 | entry exists | | definition |
| 2 | *if a Spell is attached:* not triggered by this aura | `Spell::IsTriggeredByAura` | event |
| 3 | triggered source allowed unless `CAN_PROC_FROM_PROCS` / `TRIGGERED_CAN_PROC` / auto-attack flags / event `NOT_A_PROC` | | event + definition |
| 4 | `CANT_PROC_FROM_ITEM_CAST` vs cast item | | event |
| 5 | event `SUPPRESS_WEAPON_PROCS` × provider `AURA_IS_WEAPON_PROC` | | both |
| 6 | provider `ONLY_PROC_FROM_CLASS_ABILITIES` needs event `ALLOW_CLASS_ABILITY_PROCS` | | both |
| 7 | `SUPPRESS_TARGET_PROCS` (taken) / `SUPPRESS_CASTER_PROCS` (done) with their overrides | | both |
| 8 | stealth aura vs event `CU_DONT_BREAK_STEALTH` | | both |
| 9 | has charges (if using charges) | | **state** |
| 10 | `REQ_SPELLMOD`: event `m_appliedMods` contains this aura | | event |
| 11 | proc cooldown `m_procCooldown > now` | `IsProcOnCooldown` | **state** |
| 12 | `CanSpellTriggerProcOnEvent` (§3.3) | SpellMgr.cpp:524 | event + definition |
| 13 | conditions (source 24) | ConditionMgr | actor/target |
| 14 | `DoCheckProc` scripts | | script |
| 15 | per effect: `DisableEffectsMask`, `DoCheckEffectProc`, `CheckEffectProc` gates | SpellAuraEffects.cpp:1365 | event |
| 16 | at least one effect left | | |
| 17 | passive equipment requirement (weapon by attack type, or shield) | | actor |
| 18 | `ONLY_PROC_OUTDOORS`, `ONLY_PROC_ON_CASTER`, standing (unless `ALLOW_PROC_WHILE_SITTING`) | | actor |
| ⎯ | **RNG boundary:** `roll_chance(CalcProcChance())` | | |
| A | `SetLastProcAttemptTime(now)` | | success or failure |
| B | on success: `PrepareProcToTrigger` (charge drop, cooldown, success time) | | |
| B' | on failure: burn-charge / cooldown-on-failure attributes | | also after *pre-RNG* rejection |
| C | after **all** auras of both sides: `HandleProc`, then `ConsumeProcCharges` | | trigger phase |

There are three consequences a flat predicate would lose:

* **Chance is not a filter.** A 100% (or 101%) provider still consumes a
  `rand_chance()` draw.
* **Failure side effects fire on pre-RNG rejections too.**
  `PROC_FAILURE_BURNS_CHARGE` and `PROC_COOLDOWN_ON_FAILURE` trigger whenever
  `GetProcEffectMask` returns 0, including flag mismatches, for any event that
  reaches the holder with a nonzero mask. Three and six real providers carry
  these attributes, respectively. `test_cooldown_on_failure_attribute` pins this.
* **The equipment/outdoors/caster/standing checks come after the effect-mask
  work**, and the script `DoCheckProc` runs before them.

---

## 3. Proc event identity

### 3.1 Flags, producers and consumers

`EVENT_PRODUCERS` in `procs/events.py` lists all 25 `ProcSkillsAndAuras` call
sites (a test counts them against the checkout). Summary:

| event | actor mask | action-target mask | type | phase | hit | Spell attached |
|---|---|---|---|---|---|---|
| white swing (`AttackerStateUpdate`) | `DEAL_MELEE_SWING` + `MAIN/OFF_HAND_WEAPON_SWING` | `TAKE_MELEE_SWING` (+`TAKE_ANY_DAMAGE` if damage) | NONE | NONE | melee outcome | no (DamageInfo) |
| spell hit (`DoDamageAndTriggers`) | by DmgClass / positivity (below) | matching `TAKE_*` (+`TAKE_ANY_DAMAGE`) | HEAL / DAMAGE / NO_DMG_HEAL | HIT | per-target | yes |
| cast success (`Spell::_cast`) | as above + `2_CAST_SUCCESSFUL` | none | MASK_ALL | CAST | NORMAL unless CRIT | yes |
| cast finish | as above | none | accumulated | FINISH | accumulated (NORMAL if no targets) | yes |
| cast ended (`Spell::finish`) | `CAST_ENDED` | none | MASK_ALL | NONE | NONE | yes |
| periodic damage | `DEAL_HARMFUL_PERIODIC` | `TAKE_HARMFUL_PERIODIC` (+any-damage) | DAMAGE | HIT | NORMAL/CRIT (+ABSORB) | **no** |
| periodic heal | `DEAL_HELPFUL_PERIODIC` | `TAKE_HELPFUL_PERIODIC` | HEAL | HIT | NORMAL/CRIT | **no** |
| leech (damage half) / funnel / power burn | `DEAL_HARMFUL_PERIODIC` | `TAKE_HARMFUL_PERIODIC` | DAMAGE / HEAL / NO_DMG_HEAL(+DAMAGE) | HIT | … | no |
| split damage | none | `TAKE_HARMFUL_SPELL` | DAMAGE | HIT | NONE | no |
| reflect | none | `TAKE_HARMFUL_SPELL\|ABILITY` | DAMAGE\|NO_DMG_HEAL | NONE | REFLECT | no |
| dispel | `2_SUCCESSFUL_DISPEL` | none | MASK_ALL | HIT | NONE | no |
| knockback | none | `2_KNOCKBACK` | MASK_ALL | HIT | NONE | no |
| heartbeat, enter combat, encounter start, jump, looted | the named flag | none | MASK_ALL | NONE | NONE | no |
| kill / assist / death | `KILL` (killer and owner); `2_TARGET_DIES` (tappers) | victim gets `DEATH` as its own action target | MASK_ALL | NONE | NONE | no |

`PROC_CLONE_SPELL` and `2_DO_EMOTE` have **no producer**. `ProcFlags2` bits
`0x8` and `0x20` have **no name** in Trinity. `0x20` is set on 177 current
spells (15 in player scope), `0x8` on one. The oracle reports them and refuses
to call those providers generic.

**Do not trust enum comments.** `DEAL_HELPFUL_PERIODIC` is commented "On trap
activation", but its only producer is the periodic heal tick. The
"periodic health funnel" tick reports `DEAL_HARMFUL_PERIODIC` with `HEAL`.

### 3.2 Spell-event identity (`prepareDataForTriggerSystem` / `FinalizeDataForTriggerSystem`)

* DmgClass MELEE gives `DEAL_MELEE_ABILITY` + `MAIN_HAND`/`OFF_HAND_WEAPON_SWING`
  (by attack type), and `TAKE_MELEE_ABILITY`.
* DmgClass RANGED gives `*_RANGED_ATTACK` if `AUTO_REPEAT`, else
  `*_RANGED_ABILITY`.
* Wand auto-repeat (class 2, subclass 19) gives `*_RANGED_ATTACK`.
* Otherwise the flags are decided **per target at hit time** by *positivity*.
  Damage > 0 means harmful. Healing > 0 means helpful. Otherwise every hit
  effect must be `IsPositiveEffect` for helpful. Then `TREAT_AS_PERIODIC` gives
  `*_PERIODIC`, `IS_ABILITY` gives `*_ABILITY`, and anything else gives
  `*_SPELL`.

`SpellInfo::IsPositiveEffect` is **not ported**. The builder
(`events.spell_hit`) requires the caller to supply positivity for a
no-damage/no-heal hit, and raises otherwise.

Hit masks come from `createProcHitMask` (spell) and the `DamageInfo(CalcDamageInfo)`
constructor (melee):

* A miss result is exclusive.
* Block and absorb add bits.
* NORMAL/CRITICAL are omitted when the damage is fully absorbed, resisted or
  blocked.
* A damage-immune spell hit *replaces* the mask with `IMMUNE`.
* `DISPEL` and `INTERRUPT` are OR-ed in by effect handlers.

### 3.3 Matching semantics (`CanSpellTriggerProcOnEvent`)

* **Flags:** *any* bit in common (`event & entry != 0`). There is no all/exact
  mode.
* **Always-trigger:** if the event mask contains HEARTBEAT, KILL or DEATH,
  school, family, type, phase and hit are **skipped**. The earlier
  `REQ_EXP_OR_HONOR` and `REQ_POWER_COST` checks still apply.
* **School:** any-match, only when the entry sets a mask. For events with a
  Spell attached, the spell's static school is used, otherwise the
  damage/heal school.
* **Family:** only when the event mask intersects `SPELL_PROC_FLAG_MASK`
  (which excludes the white-swing flags), and only when the event has a
  SpellInfo. The rule is `IsAffected`: family must equal, and the flag128 masks
  intersect if the entry has one.
* **Type:** any-match, same guard.
* **Phase:** any-match, required whenever the **event** mask (not the
  intersection) contains a phase-requiring flag. An entry with phase 0 never
  matches such events. A `MAIN_HAND_WEAPON_SWING`-only listener is still
  phase-checked on melee ability events, because the same event also carries
  `DEAL_MELEE_ABILITY`.
* **Hit:** applied for taken events, and for done events outside the CAST
  phase. Explicit masks are any-match. The defaults are `NORMAL|CRITICAL`
  (taken) and `NORMAL|CRITICAL|ABSORB` (done).
* **Negative/exclusion semantics:** there is no negative mask. Exclusion only
  exists through the attribute gates in §2.3 and §9.

The machine-readable report is `proc_research.py vocabulary`
(`procs-corpora/vocabulary.json`: flags, producers, attributes, fields).

---

## 4. Chance models

| model | selection (in `Aura::CalcProcChance`) | formula | population (all / player) |
|---|---|---|---|
| fixed | `SpellProcEntry::Chance` | `chance` | 1,874 / 34 |
| fixed ≥ 100 | same | roll always succeeds but **is still drawn** | 8,572 / 931 |
| fixed 0 | same | never succeeds without a spell mod | 73 / 20 |
| classic PPM | caster exists **and event has DamageInfo** and `ProcsPerMinute != 0` (only from `spell_proc`) | `floor(WeaponSpeed * PPM / 600)` | 39 / 1 |
| RPPM | caster exists and `SpellInfo::ProcBasePPM > 0` (overrides classic PPM) | below | 1,942 / 68 |
| legacy item on-proc | `ItemEffect` trigger 2 | triggered spell's own `ProcChance`; `SpellPPMChance` → classic PPM on the attack type's speed; `> 100` → `GetWeaponProcChance` | 184 `ItemEffect` rows |
| legacy enchant | `SpellItemEnchantment` effect 1 | `EffectPointsMin` (0 → `GetWeaponProcChance`), overridden by `spell_enchant_proc_data` PPM (on **item** delay) or Chance | 35 enchant effects |

The other paths in `CalcProcChance`:

* Without a caster (`GetCaster()` null) only the fixed chance applies.
  Classic PPM, RPPM and spell mods are all skipped.
* `SpellModOp::ProcChance` applies after PPM/RPPM.
* `PROC_ATTR_REDUCE_PROC_60` then scales by
  `max(0, 1 − (actorLevel − 60)/30)` (one real provider).

**Classic PPM** (`Unit::GetPPMProcChance`):

* `SpellModOp::ProcFrequency` applies to the PPM first.
* The result is `floor((uint32 speed * float PPM) / 600.0f)`, which is a
  **whole percent**.
* It returns 0 for PPM ≤ 0.
* It is independent of haste and elapsed time.
* It uses `GetBaseAttackTime(eventAttackType)` on the aura's caster.

**RPPM** (`Aura::CalcPPMProcChance`, SpellAuras.cpp:2034):

```
ppm       = SpellInfo::CalcProcPPM(caster, aura cast item level)       (§5)
interval  = 60.0f / ppm
sinceTry  = min(seconds(now − m_lastProcAttemptTime), 10.0)   double → float
sinceProc = min(seconds(now − m_lastProcSuccessTime), 1000.0) double → float
chance    = max(1, 1 + (sinceProc / interval − 1.5) * 3) * ppm * sinceTry / 60
chance    = clamp(chance, 0, 1) * 100
```

* **Haste dependence** exists only through a HASTE modifier. `Flags` is never
  read.
* **Elapsed time:** the `sinceTry` term is capped at 10 s.
* **Bad-luck protection:** the `max(1, …)` term, capped by `sinceProc ≤ 1000 s`.
* **First proc:** a new `Aura` starts with `attempt = now − 10 s` and
  `success = now − 120 s`. The first eligible event therefore sees the full
  10 s window and a bad-luck multiplier of `1 + (120/interval − 1.5)·3`.
* **Reset:** re-creating the Aura resets both timers. A refresh does not.
* **Precision:** every product and quotient is binary32. The elapsed times are
  double-precision `FloatSeconds`, clamped in double and then narrowed.
* **RNG:** `roll_chance<float>(chance)` is `chance > uniform_real<float>(0,100)`
  (`Random.h/.cpp`), so a chance of exactly 0 never succeeds and any chance
  ≥ 100 always does.

"Chance derived from another source" exists only as spell mods (external) and
the legacy item path, which reads the *triggered* spell's `ProcChance`.

Calculators: `procs/chance.py` (`classic_ppm_chance`, `rppm_rate`,
`rppm_chance`, `aura_proc_chance`, `item_on_proc_chance`,
`enchant_combat_spell_chance`, `weapon_proc_chance`, `roll_succeeds`). None of
them draws randomness, and each returns every stage.

---

## 5. SpellProcsPerMinuteMod

Snapshot: 295 `SpellProcsPerMinute` rows (Flags: 0×5, 1×285, 3×5) and 821
modifiers on 209 of them, with at most 21 per rate. 241 rates are referenced,
with no dangling IDs.

| Type | name | rows | params seen | formula (`ppm *= 1 + term`) | inputs |
|---:|---|---:|---|---|---|
| 1 | HASTE | 159 | 1, 3, 4, 5 | `(1/h − 1) * Coeff`, where h = `ModHaste`, `ModRangedHaste`, `ModSpellHaste`, `ModHasteRegen`, or (5) their min; other params give 0 | caster haste multipliers |
| 2 | CRIT | 9 | 3, 4 | `crit% * Coeff * 0.01`, where 1/2/3 = melee/ranged/spell and 4 = min of the three; **players only** | caster crit % |
| 3 | CLASS | 58 | 17 masks | `Coeff` if `(1 << (class−1)) & Param` | caster class |
| 4 | SPEC | 573 | 37 specs | `Coeff` if the player's **primary** spec == Param | primary spec |
| 5 | RACE | 1 | 1 | `Coeff` if `RaceMask<int32>{Param}.HasRace(race)` (bit table, not `race−1`) | race |
| 6 | ITEM_LEVEL | 2 | 528 | 0 if ilvl == Param or equal points; else `(RPP(ilvl)/RPP(Param) − 1) * Coeff`, with RPP = `RandPropPoints[..].SuperiorF_0` | aura cast item level |
| 7 | BATTLEGROUND | 6 | 0 | `Coeff` if the caster's map is BG/arena | map type |
| 8 | AURA | 13 | 13 | `Coeff` if `caster->HasAura(Param)` | caster auras |

Rules shared by every modifier:

* Modifiers compose **multiplicatively, in `SpellProcsPerMinuteMod` store
  order** (ascending ID).
* With no caster, the base rate is returned.
* An unknown type is **silently ignored** by Trinity (`default: break`). The
  oracle does the same but reports it.
* Missing actor facts for CLASS, SPEC, RACE, BATTLEGROUND or AURA raise
  `UnsupportedSource`.
* All modifier inputs are **caster** facts (`GetCaster()`), not event-actor
  facts. They are re-read on every attempt.
* The real modifier sets are compared against Trinity's compiled `CalcProcPPM`
  under a fixed synthetic caster (§20).

Unproved: the semantics of `SpellProcsPerMinute.Flags`, and whether a negative
final PPM (possible with `Coeff ≤ −1`) was intended. Trinity simply computes
`60/ppm` and clamps the chance.

---

## 6. Cooldowns and ownership

| family | immutable source | timer owner | starts | failed roll | pre-RNG rejection | reset |
|---|---|---|---|---|---|---|
| proc internal cooldown | `SpellProcEntry::Cooldown` (DB2 `ProcCategoryRecovery` or `spell_proc.Cooldown`) + `SpellModOp::ProcCooldown` | `Aura::m_procCooldown` (one per Aura, shared by all applications; "see 51698 area aura") | `PrepareProcToTrigger` at `now` (vetoable by `DoPrepareProc`) | no, unless `PROC_COOLDOWN_ON_FAILURE` | no, unless `PROC_COOLDOWN_ON_FAILURE` (then yes) | new Aura (`TimePoint::min()`); `ResetProcCooldown` (script-only); equip cooldown sets it to `now + cd` on equip (`Player::ApplyEquipCooldown`) |
| RPPM timing | `ProcBasePPM` > 0 | `m_lastProcAttemptTime`, `m_lastProcSuccessTime` | attempt: after every roll; success: in `PrepareProcToTrigger` | stamps attempt | no stamp | new Aura (now−10 s / now−120 s) |
| triggered spell cooldown / category | `SpellCooldowns`, `SpellCategories` | `SpellHistory` of the caster | **never** for proc triggers: `TRIGGERED_FULL_MASK` includes `IGNORE_SPELL_AND_CATEGORY_CD`, so `CheckCast` and `HandleCooldowns` both skip | — | — | — |
| spell charges (`SpellCategory.MaxCharges`) | `ChargeCategory` | `SpellHistory` | not consumed by ignoring-cooldown casts (`HandleCooldowns` returns before `ConsumeCharge`) | — | — | — |
| item cooldown | `ItemEffect.CoolDownMSec`, `CategoryCoolDownMSec` | `SpellHistory` (on-use only) | on-use casts | — | — | — |
| enchant cooldown | none exists | — | legacy enchant and item procs are **stateless** | — | — | — |

The proc cooldown is compared as `m_procCooldown > now`, so an event at
exactly the expiry time is allowed.

---

## 7. Charges and consumption

* **Source:** `SpellProcEntry::Charges`. That is `spell_proc.Charges` (uint8)
  or the DB2 `ProcCharges` (int32 → uint32), plus `SpellModOp::ProcCharges`,
  truncated to **uint8** by `CalcMaxCharges`. `-1` therefore becomes **255**.
* **Owner:** the `Aura` (`m_procCharges`, `m_isUsingCharges = charges != 0`).
* **Consumption is two-step:**
  1. *Decrement* happens in `PrepareProcChargeDrop` during collection, **before
     any trigger**. It is skipped if `USE_STACKS_FOR_CHARGES`, if charges are
     not in use, or if the event spell has `DO_NOT_CONSUME_RESOURCES`.
  2. *Removal* happens in `ConsumeProcCharges` after the effect handlers: remove
     at 0, or with `USE_STACKS_FOR_CHARGES` call `ModStackAmount(-1)` (remove at
     0 stacks), regardless of the charge counter.
* **A failed trigger still consumes.** The decrement already happened, and
  script prevention (`OnProc` / `OnEffectProc` + `PreventDefaultAction`) does
  not restore it. A `DoPrepareProc` veto is the only pre-trigger escape.
* **Pre-trigger removal:** a provider removed during the trigger phase by an
  earlier provider's action is skipped entirely (`GetRemoveMode()`), including
  its `ConsumeProcCharges`.
* **Refresh/reapplication:** `ModStackAmount` with a non-decreasing stack count
  on a stackable or non-unique aura calls `RefreshTimers` and
  `SetCharges(CalcMaxCharges())`. Charges refill. Proc cooldown and RPPM timers
  are untouched. A new application creates a new Aura, which resets
  everything.
* **Stacks vs charges:** separate members. Only `USE_STACKS_FOR_CHARGES`
  (34 providers, 11 in player scope) links them.
* **Failure burns charge:** `PROC_FAILURE_BURNS_CHARGE` decrements on any
  non-proc outcome and queues `{0, aurApp}`, so removal still happens.

Population: 957 providers with charges (54 player).

---

## 8. RNG identity and ordering

* **One `rand_chance()` draw per aura that passes every pre-RNG check**,
  including 100% chances. Rejected, cooldown-blocked, charge-less,
  chain-limited and unreached auras draw nothing.
* **Per-candidate, independent draws** from a thread-local SFMT engine through
  `std::uniform_real_distribution<float>(0, 100)`. The engine and the
  distribution are implementation details that must not be copied.
* **Deterministic iteration order inside one event:** all actor-side auras in
  `std::multimap<SpellID, AuraApplication*>` order (ascending SpellID; equal
  SpellIDs keep insertion order), then the mod-owner auras (pets,
  `push_front`, i.e. **reversed** relative to the owner's map order), then all
  action-target auras in their map order.
* **Every draw for the event precedes every triggered action.** A triggered
  spell cannot change which auras roll for the event that triggered it. It can
  only create new events (nested `ProcSkillsAndAuras`), which roll later.
* **RPPM stamps the attempt after the draw** (success or failure) and the
  success in `PrepareProcToTrigger`. A failed roll therefore mutates RPPM state.
  A rejected check does not.
* **Suppression happens before RNG.** The triggered/self/suppress gates are
  pre-RNG. The chain-length limit drops the whole event before any aura is
  visited. `CanProc()` (`m_procDeep`) makes `DoDamageAndTriggers` skip event
  creation entirely for the unit.
* **Hidden ordering hazards:**
  * The **enchant combat-spell path draws twice per enchant effect**
    (`Player::CastItemCombatSpell`, two consecutive `roll_chance(chance)`
    blocks, each able to cast). This looks accidental and must not be copied
    blindly, but it is observable RNG consumption.
  * `ProcEventInfo` for the actor and target are distinct objects sharing all
    fields but the type mask.
  * `CalcPPMProcChance` reads `GameTime::Now()` rather than the `now` captured
    by the caller. Both are the same world-tick time.

The state oracle (`procs/state.py`, `Timeline`) consumes caller-supplied rolls
in exactly this order. It fails if an event needs a roll it was not given, or
leaves one unused. `test_all_rolls_precede_all_triggers` and
`test_ordering_actor_before_target_and_spell_id_order` pin the order.

---

## 9. Recursion and suppression

| mechanism | where | effect |
|---|---|---|
| `TRIGGERED_FULL_MASK` on proc triggers (minus `IGNORE_POWER/REAGENT_COST`) | `HandleProcTriggerSpell*AuraProc` | the child is `IsTriggered()`, **and** `IsProcDisabled()` because `DISALLOW_PROC_EVENTS` is in the mask |
| triggered-source gate | `GetProcEffectMask` | providers without `CAN_PROC_FROM_PROCS` / `TRIGGERED_CAN_PROC` / auto-attack flags ignore events from triggered Spells, unless the event spell has `NOT_A_PROC` |
| self-trigger gate | `Spell::IsTriggeredByAura` | an aura never procs from a Spell it triggered, **even with** `CAN_PROC_FROM_PROCS` |
| `SetCantProc` (`m_procDeep`) | `TriggerAurasProcOnEvent` when the event spell is proc-disabled | while the handlers run, spells *hitting that unit* (`unitTarget->CanProc()`) generate **no** events at all |
| proc chain length | `ProcSkillsAndAuras`, `Spell` ctor | children inherit `max(parent + 1, spell chain)`; at ≥ 10 a Spell event is dropped |
| `SUPPRESS_CASTER_PROCS` / `SUPPRESS_TARGET_PROCS` (event) with `ENABLE_…`, `CAN_PROC_FROM_SUPPRESSED_…` overrides | `GetProcEffectMask` (+ reactives in `ProcSkillsAndAuras`) | side-specific suppression |
| `SUPPRESS_WEAPON_PROCS` × `AURA_IS_WEAPON_PROC` | `GetProcEffectMask`; `DoDamageAndTriggers` skips `CastItemCombatSpell` | weapon-proc suppression |
| `ONLY_PROC_FROM_CLASS_ABILITIES` × `ALLOW_CLASS_ABILITY_PROCS` | `GetProcEffectMask` | 1,662 providers (57 player) require it |
| extra-attack guard | `CheckEffectProc` | a trigger spell with `ADD_EXTRA_ATTACKS` cannot proc while it is the actor's last extra-attack spell |
| generation infinite-loop guard | `LoadSpellProcs` | no default entry (§2.2) |
| `ONLY_PROC_ON_CASTER`, stealth `CU_DONT_BREAK_STEALTH` | `GetProcEffectMask` | holder/event gates |

**Periodic ticks carry no Spell object.** Every Spell-based gate above is
skipped for DoT/HoT ticks, even when the aura was applied by a triggered spell.
Suppression is therefore *not* "triggered spells cannot proc": a proc can apply
an aura whose ticks proc anything (`test_triggered_child_spell`).

Witnesses (real data):

* **Proc triggers an ordinary spell:** 1295884 *Hex Lord's Dooming Idol* →
  1307470 (MOD_STAT, no ProcFlags).
* **Proc triggers a spell that can proc again:** 1250564 *Xathuux's Last Roar* →
  1254180, which applies a PROC_TRIGGER_SPELL aura → 1254331.
  1254180's own events are proc-disabled, but its aura is an independent
  provider.
* **Self-recursive-looking provider:** 3439 *Wandering Plague* triggers 3439.
  It is suppressed by `IsTriggeredByAura`; see the witness test.
* **Triggered spell marked as a proc source:** 370455 *Charged Blast* →
  370454 has `NOT_A_PROC` (349 such triggered spells overall, 5 in player
  scope).
* **Intentional chain:** 59057 *Rime* (`2_CAST_SUCCESSFUL`, CAST phase) →
  59052 (1 charge, spell-mod consumer).
* **Authored cycles:** 382 SCCs contain a proc edge (13 self-loops), for example
  38334 ↔ 38346. The longest acyclic authored chain has 3 proc edges
  (56443 → 56444 → 56445 → {30080}).

---

## 10. Target selection

`HandleProcTriggerSpellAuraProc` / `…WithValueAuraProc` (SpellAuraEffects.cpp:6155):

* **caster** = the aura's **holder** (`aurApp->GetTarget()`), not the aura
  caster and not the item owner;
* **explicit target** = the *other party*: if the holder is the event actor,
  the action target; otherwise the actor.
  With `SPELL_ATTR8_TARGET_PROCS_ON_CASTER` on a taken event, it is the actor
  (7 providers);
* the cast carries `TriggeringAura` (sets `m_triggeredByAuraSpell` and the cast
  item level), `TriggeringSpell = event Spell` (original cast ID and item
  level), and for WITH_VALUE, `BasePoint0 = aura amount`;
* the triggered spell's **implicit targets** then run with that explicit target
  as context. `TARGET_UNIT_CASTER` effects still land on the holder. The
  explicit target is a contextual input, not the final answer.

`HandleProcTriggerDamageAuraProc` deals the aura's own amount from the holder to
the other party, with no triggered spell. The breakable-CC handler reduces the
aura amount by the event damage and removes the aura at ≤ 0.

The legacy item path casts on the **damage victim**. The legacy enchant path
casts on **self if the triggered spell `IsPositive()`**, else on the victim
(positivity not ported → unknown).

Roles needed downstream: proc actor = event actor; action target = event
action target; holder = aura target; aura caster; item owner = holder for equip
auras; damage victim / heal recipient = `DamageInfo` / `HealInfo` targets.

---

## 11. Provider lifetime and ownership

| carrier | how the Aura exists | mutable state owner | population (all / player) |
|---|---|---|---|
| passive learned spell (trait / spec) | `LearnSpell` → passive auto-cast | the passive Aura on the player | class-trait 971, spec 60 |
| item equip effect | `ApplyItemEquipSpell` → `CastSpell(this, spell, item)` | Aura (cast item GUID/level); equip cooldown seeds `m_procCooldown` | 2,435 / 31 (+34 current gear) |
| enchant / gem equip spell | `ApplyEnchantment` → same | Aura | 212 / — ; gem 75 |
| set bonus | `ApplyEquipSpell` (no item) | Aura | 806 / — |
| active buff / debuff | a spell's APPLY_AURA effect | that Aura (on self or on the target) | timed 4,486 / 192 |
| target-held aura (CC, debuffs with TAKE flags) | same, on the enemy | the Aura on the enemy; the holder is the proc caster for triggers | 2,537 with taken flags |
| legacy item on-proc / enchant combat spell | no aura | **none** (stateless, no ICD/charges/RPPM) | 184 ItemEffect rows / 35 enchant effects |
| pets | mod-owner auras are evaluated for pet-caused events only if the owner's aura modified the Spell | the owner's Aura | — |

The ownership model **is** separable as

```
immutable proc definition  (SpellProcEntry + effect roles + RPPM rate/mods)
  + provider binding       (which Aura instance on which holder, cast by whom, cast item level)
  + mutable Aura state     (charges, stack, proc cooldown, last attempt, last success)
```

Two facts constrain it:

* The state belongs to the **Aura**, not the application or the actor, so area
  auras share it.
* RPPM inputs are read from the **aura caster**, while eligibility facts come
  from the event actor and the holder.

`proc_research.py provider <id>` prints `lifecycle`, `bindings` and
`mutable_state` for each provider.

---

## 12. Items, trinkets, enchants

This builds on the gearing acquisition roots (`gearing.effects`,
`gearing.enchants`, `gearing.items`).

| witness | carrier | shape | notes |
|---|---|---|---|
| 250228 → 1250564 *Xathuux's Last Roar* | OnEquip | RPPM 1.0 + 30 s ICD, trigger 1254180 (+ inert DUMMY) | damage/ability flags; the trigger is itself a provider |
| 270169 → 1295884 *Hex Lord's Dooming Idol* | OnEquip | fixed 101 + 6 s ICD → 1307470 (stack 30) | stacking stat proc |
| 270174 → 1295643 *Idol of the Howling Nexus* | OnEquip | RPPM 2.0 → 1295649 (MOD_STAT/MOD_RATING) | taken flags ⇒ `TRIGGERED_CAN_PROC` |
| 270163 → 1307356 *Sszorak's Ferocity* | OnEquip | fixed + 10 s ICD on `KILL`/`2_TARGET_DIES` | `REQ_EXP_OR_HONOR` |
| 250225 → 1250557 *Void Execution Mandate* | OnUse buff | unknown `ProcFlags2` 0x20 | fails closed |
| 250224 → 1250546 *Mindpiercer's Sigil* | OnEquip | inert (PROC_TRIGGER_SPELL with trigger 0 + DUMMY) | needs a bespoke consumer |
| enchant 8614 → 1262337 *Farstrider's Hawkeye* | Enchant EquipSpell | RPPM 3.0 → 1262336 | stat enchant proc |
| enchant 8053 → 1237014 *Oil of Dawn* | Enchant EquipSpell (temporary) | RPPM 6.0 + HASTE(param 4) | weapon oil |
| enchant 803 *Fiery Weapon* | Enchant CombatSpell | `spell_enchant_proc_data` PPM 6 on item delay; **two rolls** | legacy path |
| item 647 → 17152 | ItemEffect OnProc | `SpellPPMChance` 1.3 classic PPM | legacy path |
| 17619 *Alchemist Stone* | OnEquip, `spell_proc` + script | `DoCheckProc` + `OnEffectProc` | unusual/scripted |

No healing-proc trinket in the current gear corpus reaches a generic trigger.
The healing examples are class traits (§13).

**How much gear reduces to "acquire → ordinary provider → ordinary triggered
spell"?** Among the 34 current-gear providers:

| category | count |
|---|---:|
| fully generic trigger shapes | 4 |
| generic trigger + inert DUMMY effect | 5 |
| inert-only | 17 |
| unknown flag bit | 7 |
| no entry | 1 |

`ItemEffect` OnProc and `SpellItemEnchantment` CombatSpell are the only
item-specific semantics: a stateless chance with a different formula, a
different target rule, and (for enchants) a double roll. Current content does
not use them. Everything else is ordinary aura procs once the equip spell is
applied.

---

## 13. Traits and specialization

Current class trees are `TraitTreeLoadout` → `TraitNode` →
`TraitNodeXTraitNodeEntry` → `TraitNodeEntry` → `TraitDefinition.SpellID`.
Specialization spells come from `SpecializationSpells` and
`ChrSpecialization.MasterySpellID`.

| provider | class | shape |
|---|---|---|
| 16166 *Master of the Elements* | Shaman | fixed → 260734; spell-mod effect disabled |
| 207104 *Runic Attenuation* | Death Knight | RPPM 10.8 + HASTE → 221322 |
| 205148 *Reverse Entropy* | Warlock | RPPM 2.5 → 266030 |
| 382549 *Pain and Gain* | Warrior | `spell_proc`, 10 s ICD, taken flags → heal 382551 |
| 5301 *Revenge Trigger* | Warrior (spec) | RPPM 3.0 + AURA mod (202560, +20%) + 3 s ICD → 5302 |
| 203555 *Demon Blades* | Demon Hunter (spec) | fixed → 203796 on white swings |
| 2094 *Blind* | Rogue | target-held breakable CC with 1 charge |
| 53576 *Infusion of Light* | Paladin | `spell_proc`, DUMMY **with** trigger → 54149 (scripted, 1 charge) |

`TraitDefinitionEffectPoints` changes effect **amounts** through `CalcValue`,
never proc policy. It touches a proccing effect on 76 providers: 60 DUMMY,
10 PROC_TRIGGER_SPELL (amount unused by the handler), and 6
PROC_TRIGGER_SPELL_WITH_VALUE, where the trait changes the triggered spell's
BasePoint0.

---

## 14. Trigger topology census

From `procs-corpora/census.json`:

| | all | player scope |
|---|---:|---:|
| proc providers with an entry | 12,500 | 1,054 |
| proc edges (provider effect → trigger spell) | 7,003 | 295 |
| providers with ≥ 1 trigger | 6,816 | 289 |
| providers with no trigger | 5,684 | 765 |
| providers with > 1 distinct trigger | 131 | 3 |
| unique triggered spells | 6,152 | 289 |
| triggered spells that do not exist | 55 | 2 |
| triggered spells without ProcFlags (ordinary) | 5,507 | 198 |
| triggered spells that are providers themselves | 318 | 45 |
| triggered spells with `NOT_A_PROC` | 349 | 5 |
| cycles containing a proc edge | 382 (13 self-loops) | 2 |
| longest acyclic proc chain | 3 edges | — |

Action kinds over all providers (a provider can have several):

| action kind | count |
|---|---:|
| trigger-spell | 6,493 |
| DUMMY without trigger | 4,335 |
| charge/cooldown consumer (aura without a handler) | 1,423 |
| PROC_TRIGGER_SPELL without trigger | 626 |
| breakable CC | 537 |
| missing trigger spell | 329 |
| direct damage | 141 |

Origin roots over all providers:

| origin | count |
|---|---:|
| none (NPC/legacy) | 7,574 |
| item-equip | 2,435 |
| triggered-by-spell | 2,362 |
| class-trait | 971 |
| set-bonus | 806 |
| item-use | 308 |
| enchant-equip | 212 |
| other trait | 166 |
| gem | 75 |
| spec | 60 |
| current gear | 34 |

"Player scope" is every provider reachable within 8 trigger edges from a
current class-tree trait, a specialization spell, or a current raid/M+ item.
That is 4,197 root spells.

Edges are authored relationships. An `effect` edge from a DUMMY *effect* (for
example 1258223 → 1250567) is data, not an executed cast.

---

## 15. Semantic shape census

`generic` means all of the following hold:

* an entry exists;
* no unknown flag bits;
* no script proc hook;
* no condition row;
* no in-code correction;
* every proccing effect lands in a generic `HandleProc` branch (trigger an
  existing spell, direct damage, breakable CC, or pure charge/cooldown
  consumption).

| | all | player |
|---|---:|---:|
| providers | 14,227 | 1,195 |
| with entry | 12,500 (spell_proc 1,260) | 1,054 (spell_proc 202) |
| **generic** | **7,172** | **280** |
| inert-only DUMMY/no-trigger | 3,768 | 550 |
| mixed generic + inert effect | 706 | 78 |
| script proc hook | 377 providers (478 script-bound; hook uses: OnEffectProc 265, DoCheckProc 106, DoCheckEffectProc 75, OnProc 33) | 130 (151 script-bound) |
| no entry (non-trigger auras only) | 1,678 | 123 |
| no entry (infinite-loop guard) | 49 | 18 |
| unknown flag bits | 178 | 15 |
| missing trigger spell | 297 | 2 |

The largest generic shapes:

| shape | all | player |
|---|---:|---:|
| fixed ≥ 100 / no ICD / trigger | 2,033 | 114 |
| fixed ≥ 100 / ICD / trigger | 791 | 46 |
| fixed / ICD / trigger | 785 | 4 |
| fixed / no ICD / trigger | 494 | 9 |
| RPPM / no ICD / trigger | 490 | 5 |
| RPPM + haste / no ICD / trigger | 440 | 8 |
| fixed ≥ 100 / no ICD / charge-or-cooldown consumer (± charges) | 631 | 39 |
| fixed ≥ 100 / breakable CC | 279 | 10 |

Overlaps (all providers with an entry):

| combination | count |
|---|---:|
| ICD | 3,278 |
| charges | 957 |
| haste-modified RPPM | 888 |
| taken-side flags | 2,537 |
| accepts triggered sources | 5,139 |

The top combined buckets are in `census.json` → `overlaps`.

**Leverage:** a small set of generic foundations covers about half of all
authored providers (7,172 / 14,227): fixed or RPPM (± haste and the other seven
modifier types) × optional per-Aura ICD × optional charges × {trigger spell,
direct damage, CC break, charge consumer}. The same foundations cover only
**23%** of current player-reachable providers. Most of the remainder is inert
DUMMY carriers whose behavior Trinity does not implement generically.

---

## 16–19. The Python oracle

```
scripts/research/procs/
  enums.py        flags, masks, attributes, aura/effect tables (resolved by Trinity name)
  _trinity_names  generated SharedDefines names (tools/gen_proc_names.py)
  source.py       projected CSV loading + consumed-field ledger
  spells.py       SpellInfo-lite: difficulty fallback, ranks, CU bits, magnet correction
  trinity.py      world-DB overlay: spell_proc, enchant data, scripts+hooks, conditions, corrections
  definition.py   LoadSpellProcs (merge + generation), GetSpellProcEntry, ProcDefinition
  events.py       event producers + builders (mask arithmetic of every call site)
  eligibility.py  CanSpellTriggerProcOnEvent (pure) + GetProcEffectMask pre-RNG evaluator
  chance.py       binary32 calculators for every chance model
  state.py        Aura proc state + ordered replay with predetermined rolls
  providers.py    acquisition roots, lifecycle, item and enchant descriptions
  graph.py        trigger topology, SCCs, depth
  census.py       population, shapes, overlaps
  vocabulary.py   field inventory + census
  cli.py          proc_research.py
```

CLI:

```
proc_research.py provider <SpellID> [--difficulty N]
proc_research.py effect <SpellID:Effect>
proc_research.py graph <SpellID> [--depth N]
proc_research.py census [--with-ids]
proc_research.py item <ItemID>
proc_research.py enchant <EnchantID>
proc_research.py event <SpellID> '<json>' [--holder actor|target]
proc_research.py rppm <SpellID> [--haste H --crit C --class-id --spec --race --item-level --battleground --aura]
proc_research.py ppm <weapon_speed_ms> <ppm>
proc_research.py timeline <spec.json>
proc_research.py vocabulary [--census]
proc_research.py sources
Global: --tables DIR, --overlay FILE, --no-overlay (DB2 defaults only), --output FILE
```

`provider` prints:

* identity and status;
* the resolved entry with decoded masks;
* unknown bits;
* the load log;
* per-effect roles (aura, trigger aura, disabled, `CheckEffectProc` gate,
  handler, trigger spell and whether it exists, script bindings);
* the chance model with RPPM rate/modifiers and unconsumed flags;
* provider attribute roles;
* scripts and conditions;
* code corrections;
* the equipment requirement;
* external inputs;
* origins, lifecycle and bindings;
* mutable state;
* per-table provenance (row ID and the difficulty it came from).

The evaluator (§17) stops at the RNG boundary. It never mutates state and
reports `unknown` whenever a needed fact (condition, script result, equipment,
positivity, …) is not supplied. The calculators (§18) never draw.

The state oracle (§19) tracks only `m_procCharges`, `m_stackAmount`,
`m_procCooldown`, `m_lastProcAttemptTime`, `m_lastProcSuccessTime` and
removal. For each provider it reports:

* whether the event reached it, and the verdict;
* the chance with its stages;
* the roll consumed, and success;
* state before and after;
* failure side effects;
* triggered identities (caster role, explicit target role, triggering aura and
  spell).

`procs-corpora/timeline-example.json` → `timeline-example.out.json` replays
Xathuux's Last Roar:

| event | outcome |
|---|---|
| melee | rejected, no roll |
| hit | 42.92% (roll 60): fail, attempt stamped |
| hit at +2.5 s | 11.25% (roll 5): success, ICD to 34 s |
| hit at 9 s | ICD, no roll |
| hit at 34 s | 16.67% (roll 5): success |

---

## 20. Differential verification

`tools/tc_proc_probe/extract.py` copies these pieces **verbatim** from the
sibling checkout into generated headers:

* the proc enums, `ProcFlagsInit`, `SpellProcEntry` and `RoundToInterval`;
* `SpellEffectInfo::IsEffect`, `IsAura`, `IsAreaAuraEffect`,
  `IsUnitOwnedAuraEffect`;
* `SpellInfo::IsAffected`;
* `CalcPPMHasteMod`, `CalcPPMCritMod`, `CalcPPMItemLevelMod`,
  `SpellInfo::CalcProcPPM`;
* `SpellMgr::CanSpellTriggerProcOnEvent`;
* **the default-generation block of `SpellMgr::LoadSpellProcs`**;
* `Aura::CalcProcChance`, `Aura::CalcPPMProcChance`;
* `Unit::GetPPMProcChance`, `Unit::GetWeaponProcChance`;
* `Unit::GetClassMask`.

`EnumFlag.h`, `FlagsArray.h`, `Duration.h` and `RaceMask.h` are included
directly. `probe.cpp` supplies only stub classes with Trinity's member names.
Floats are exchanged as hex, so every comparison is exact.

`tests/test_procs_differential.py` (547 cases, all passing) covers:

| comparison | scope |
|---|---|
| default generation | **all 14,176 real spells with DB2 ProcFlags** (the census's 14,227 adds 51 `spell_proc`-only providers, which are not generated) + 399 random synthetic spells: entry identical field for field, or both absent |
| `CanSpellTriggerProcOnEvent` | 20,000 random entry × event cases (flags, both words, school, family flag128, type, phase, hit, attributes, spell/no spell, player/non-player, honor, action target) |
| `CalcProcPPM` | 3,000 random modifier stacks (all 8 types, edge params) + every real `SpellProcsPerMinuteMod` set under a fixed caster with real `RandPropPoints` |
| `CalcPPMProcChance` via `CalcProcChance` | 8 rates × 8 attempt ages × 7 success ages |
| `CalcProcChance` branches | caster / no caster, DamageInfo / none, classic PPM, `REDUCE_PROC_60` levels |
| `GetPPMProcChance`, `GetWeaponProcChance` | speed × PPM grid incl. uint32 extremes; ready/offhand permutations |

Not extracted, because it is too coupled to live objects:

* `Aura::GetProcEffectMask`, since it mixes Spell, AuraApplication,
  ConditionMgr, scripts and Player equipment;
* the trigger handlers;
* `PrepareProc*`, `ConsumeProcCharges`, and the collection/trigger loops.

These are independently ported with `Mirrors:` coordinates and pinned by the
synthetic tests in §21. The `MOD_HIT_CHANCE` generation input
(`CalcValueAsInt`) is passed to both sides as data. Spell mods are absent on
both sides.

---

## 21. Tests

`uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/ -q`
gives **1,377 passed** (the whole research suite, including the pre-existing
gearing and charstats tests).

| file | what it pins |
|---|---|
| `test_procs_definition.py` (39) | every generation rule and guard, `spell_proc` merge/corrections, CAST default, ranks, difficulty fallback, chance model selection, unknown bits, charge truncation |
| `test_procs_eligibility.py` (93) | every named flag against every other; overlapping flags; whole-mask phase rule; every hit bit (defaults and explicit); cast-phase hit skip; always-trigger short-circuit; school/type/family/power; builders' masks; direct spell crit vs periodic heal; white vs special; absorbed incoming spell; done vs taken; damage vs healing; triggered child and `NOT_A_PROC`; periodic ticks bypass Spell gates; self-trigger; suppression and overrides; charges/cooldown/chain limit; class-ability gate; scripts and conditions fail closed; disabled and CC-gated effects; weapon requirement; a flag × event × side matrix |
| `test_procs_chance.py` (30) | binary32 rounding (property), literal rounding, classic PPM floors, RPPM first attempt/clamps/bad-luck floor/zero/100% (property: bounds and monotonicity), each modifier type and param, composition order, no caster, fail-closed modifiers, unknown type reporting, race bit table, `CalcProcChance` branches, `REDUCE_PROC_60`, roll boundary, legacy item/enchant chance sources |
| `test_procs_state.py` (23) | roll boundary and consumption; 100% still draws; rejected draws nothing; ICD start/expiry; cooldown-on-failure (also on flag mismatch); charge drop/removal; failed roll keeps charge; failure-burns-charge; `DO_NOT_CONSUME_RESOURCES`; stack charges; refresh semantics; prepare veto; RPPM stamps on failure/success/rejection; no-caster RPPM; ordering (sides, SpellID, insertion); rolls-before-triggers; target identity incl. `TARGET_PROCS_ON_CASTER`; chain limit; unknown facts; replay determinism (property) |
| `test_procs_differential.py` (547) | §20 |
| `test_procs_witnesses.py` (26) | the real witnesses in §9, §12, §13, a real RPPM+ICD timeline, and census headline numbers |
| `test_procs_overlay_cli.py` (13) | the SQL replay (comments, strings, DNF, variables, hex, bitwise OR, duplicate keys, unsupported shapes), overlay completeness, header parity (flag/hit/attribute enums, trigger aura tables, PPM mod enum, call-site count), CLI provider/event/rppm/timeline |

---

## 22. Fail-closed inventory

These are reported by the oracle and never given semantics:

| unknown or unported item | how it surfaces | reopen condition |
|---|---|---|
| `ProcFlags2` bits `0x8`, `0x20` | `unknown_proc_flag_bits`, shape `unsupported:…` | a Trinity producer/consumer or a client consumer names them |
| `SpellProcsPerMinute.Flags` (0/1/3) | `rppm_flags_unconsumed` | a consumer appears (Trinity has none) |
| RPPM modifier type outside 1–8 | reported stage; Trinity ignores it | new type in data |
| DUMMY / PROC_TRIGGER_SPELL without trigger | shape `inert-only` / `mixed` | a script or client consumer is identified per spell |
| AuraScript hooks | `script_hooks`; evaluator `unknown` for check hooks | per-script port |
| conditions (source 24) | evaluator `unknown` until `conditions_met` is supplied | ConditionMgr port |
| `SpellInfo::IsPositive` | builders require positivity; enchant target reported unknown | port `_IsPositiveEffect` |
| spell mods (`ProcChance`/`ProcFrequency`/`ProcCharges`/`ProcCooldown`) | `external_inputs`; overrides in `Provider` | spell-mod archaeology |
| `LoadSpellInfoCorrections` fix bodies | `trinity_code_corrections` members | per-spell review |
| `CalcValue` scaling (ScalingClass, trait points) | amounts not computed; `MOD_HIT_CHANCE` with scaling flagged | spell-value archaeology |
| `serverside_spell` rows | not loaded | if a provider depends on one |
| build skew: Trinity ≤ 12.0.7.68453, data 12.1.0.69497 | 20 `spell_proc` rows reference spells absent from the snapshot (`spell_proc_rows_skipped`); 12.1 spells have no Trinity overlay | Trinity catches up to 12.1 |

---

## 23. Interaction with existing research

* Reuses `gearing.tables` (loader, `coerce`, `SourceError`) and
  `gearing.effects`, `gearing.enchants`, `gearing.items` for acquisition roots.
* Reuses `charstats._aura_names` for aura names, and the gearing current-gear
  corpus for "current gear".
* The oracle takes character identity, talents and gear as *roots*
  (`OriginIndex`). It prepares proc definitions, and emits triggered SpellIDs
  as ordinary spell roots. It never models Core's architecture.

---

## 24. DBC source transition

Every table and column the tooling reads goes through `procs.source.Source`,
which records it. `proc_research.py sources` (committed as
`procs-corpora/sources.json`) lists 44 tables with their columns and sha256.
To rerun against dbc-resolver output:

* provide those columns under the DB2 field names (array fields as
  `Name_<i>`);
* keep the rule that empty cells are 0;
* nothing depends on Wago row order except where Trinity also depends on store
  order: `SpellProcsPerMinuteMod` (application order), `SpellEffect` (later row
  wins per index), and `SkillLineAbility` (later row wins per
  `SupercedesSpell`).

---

## 25. Reproducibility

| item | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`changes/metadata/12.1.0.69497.json`) |
| wowlab-data commit (base of this work) | `cf2fde097039c0091d5e472300bcfe58a30f7901` |
| TrinityCore commit | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (master), supports client builds ≤ 12.0.7.68453 |
| Trinity world DB | `TDB_full_world_1200.26021_2026_02_06.sql` (pinned by `revision_data.h.in.cmake`), from release `TDB1200.26021`, archive sha256 `48f0e2af7620ca70ec7054ff19d356255bc50e11d7829932015f28918f195588`, dump sha256 `54ddf4c12d6034a3c61e9b5683c2578de82d826d9090041a5e0477a164f5172d` |
| world updates replayed | all 522 files in `sql/updates/world/master` (file-name order, as `UpdateFetcher::PathCompare`); 1 unparsed statement, provably irrelevant (`conditions` source 15) |
| overlay corpus | `procs-corpora/trinity-world-overlay.json`, sha256 `c0839c5facd75cf324dbbfcc88e7c3e5998760acf8ec49679a204f3a17032179` |
| Python | 3.12.3; package standard library only |
| test dependencies | `pytest==8.4.2`, `hypothesis==6.140.3` (via `uv 0.12.4`) |
| probe toolchain | `g++ 13.3.0`, `-std=c++20 -O0 -ffp-contract=off` |

### Source table hashes (sha256)

```
ChrSpecialization.csv            4e67c0772fca8cfc857e8a7fc9952538c35018e91f2734284f71089087d60c69
Difficulty.csv                   5b5909ea3319a9596ba4472256ef8901e24c67d2b10638259bfabc652268a23e
GemProperties.csv                8985ac1145749b62d4933903bee5755b9965066db5a78506630ed83f504fcb9b
ItemEffect.csv                   0c67af8e153238e45f20ac4f8a576966b1655bef8179ac8e77b47c61a7be6efb
ItemSetSpell.csv                 5f81a5ee83788a17314f610de58654363a1a285eef7843ab4a6da600a078903e
ItemSparse.csv                   fd952738df48e8ff35ddb36f84348b2f8e6f0c03c108c32b224d3ba275b9e89a
ItemXItemEffect.csv              41d812c51bd8d31b10712a83bddc83832072426e6c266400c9ad0f956dba512e
RandPropPoints.csv               970a37886081b86dbc4a460481e2e1325f7f2bf6b5f39a557de2a40a5ebfd30a
SkillLineAbility.csv             b96e163a5eeef135f1674db67109e538f44b15d144d6eb6a1de592494c8596e8
SpecializationSpells.csv         91c46e3b75aee668250e8c27b29c76e6210152d1c5fd93e996c06886b2c51a3c
SpellAuraOptions.csv             6063ce2a3b46ac886316a5b7b983895ae283c9eed5b32592484dc306427c1780
SpellAuraRestrictions.csv        b6597468f87e99112ba08a98d537b4287ab9f2c134b4c133776ca2e17ba9ad91
SpellCastingRequirements.csv     92405214bbbe2c515fca6ff6659a5668594217aae447fde2ec139ead471f46a4
SpellCategories.csv              b09bdd203766cffb4af55013a8cad8104113634807ac6ff85b5475ba7d861193
SpellCategory.csv                a9cc5f1dbc514e1723189d0fd2519de443f19dd16e7f7a371183986037d2b2c0
SpellClassOptions.csv            4f00bcbd6ea2678d37dd75e54ad4d2d9b988c8e51773e460f49d70236f917619
SpellCooldowns.csv               11107ea6c1cc2edb8d279741de3883fcfd344be1d37cac9c11f89eda06cb46f9
SpellDuration.csv                ba8dd9dfdbd9866523db2c5d467af3849ead709d7df2e9322829d7751b02b2c5
SpellEffect.csv                  157f4d949964b05531f75dca8b5e9c45a91637104125a7f9e86474b4a3ec196f
SpellEquippedItems.csv           c741bb56267dbe0d4578f56d5cf5d77485c8a1f9db5e9c983316eb0c50fe870e
SpellInterrupts.csv              e262165ddcfc132ea30a174dd5968948bb015ed77eea4d6c18ddae5fec12d226
SpellItemEnchantment.csv         109ec309bc966e421cc79a0d425bef58044cb4fef0061fbcccd1c7516b490d18
SpellLabel.csv                   1f4772d3f12dac5d8483b27068118117423e21cff001fd10c3c3c139b21ebe0c
SpellLevels.csv                  b779655706f50bec0fe130f220f537e2440d35f340e86822acfd23b0541aa196
SpellMisc.csv                    3ef1315badafd00ceec0640b6859831893cba47a428bdbf869f2ecf0682ce4fa
SpellName.csv                    d715dbf11027d5baa981e833099014ea19e38eaf1b27815af69c079bc94dbee7
SpellPower.csv                   cbec84e0a5f1023817f4c8f8230c637c714c50bf64e9054eafc68087f5aeeb81
SpellPowerDifficulty.csv         5c8eaba4755a86146cd4a004652b162ae0b00e02cd6eec1a00fbabe237e07bd1
SpellProcsPerMinute.csv          ddc178f42532798c25ec7fe3a515724e1013325ad39f6658a5d331fd053b6692
SpellProcsPerMinuteMod.csv       d59b0d7efcb04bb83938225407b694dc7d24dc5113fdb4acd3df30fd94e28ad9
SpellReagents.csv                148475311fd4ce1d12490b85ccb7e8eaafd0cc7178497a7f17f9c3b622b7361a
SpellReagentsCurrency.csv        fa8975a14ba6825357afe9d56818f732e151cddb764727b81abd5a5378975fdb
SpellReplacement.csv             022fdbad40b2690056e4790b7899e7d1e2d1f22a0abaf62fd92b4615b4376659
SpellScaling.csv                 9dd5f7ef425353ae9bcc862930b2d6927db2a89f510122c67877043f190064a4
SpellShapeshift.csv              e66b6b5bc19aef280492f9a29e1ce3e55fb1f6916639b1ad01e41904bbac2de0
SpellTargetRestrictions.csv      d661b991ebc1f833cddc741db1ba5cfd510f71ade6494b0171f6db7da8340761
SpellTotems.csv                  638969d659311bd2ece74193a3f6c1d0f8abb49d423a37b38f1d0fff89c58bbc
SpellXSpellVisual.csv            e975fdd8c566d612a28e261c1b1df82ca18f2b23be43c65ae30b52521f2a4f76
TraitDefinition.csv              2f4b7c4cc137e73a764a5c006d2bbc3f397a56baba78037c010ea06c1fb81cd1
TraitDefinitionEffectPoints.csv  8f1fc7337614ab7d773d8fdea34c14b3fb08fa68eee500e2218bf20301a1ca4a
TraitNode.csv                    af4977323c023c2bf5b64bcfeb8bd456c67cd417c566d28cf7a622fc1de9f916
TraitNodeEntry.csv               c317b6af61012241519e504f1c6281081a2802c6ab1b1305fcf7f926a052e7c7
TraitNodeXTraitNodeEntry.csv     273a1b5017e14a94b9d3c44f62ff3b9108441b914ebd16b2070e9d26ec7ad59b
TraitTreeLoadout.csv             978c8fb28903e3dc47cd22c6f48569aae4fd3afaebe606a4674c9ac6c86ae9bc
```

### Direct consumer coordinates (TrinityCore `7f3d43b`)

```
src/server/game/Spells/SpellMgr.h        ProcFlags, ProcFlags2, ProcFlagsSpellType/Phase/Hit,
                                         ProcAttributes, PROC_ATTR_ALL_ALLOWED, SpellProcEntry,
                                         EnchantProcAttributes, SpellEnchantProcEntry
src/server/game/Spells/SpellMgr.cpp      :503  GetSpellProcEntry      :524  CanSpellTriggerProcOnEvent
                                         :692  GetSpellInfo           :823  LoadSpellRanks
                                         :1497 LoadSpellProcs         :2031 LoadSpellEnchantProcData
                                         :2496 LoadSpellInfoStore     :3390 LoadSpellInfoCorrections
                                         (magnet ProcFlags clear, CU_CAN_CRIT in custom attributes)
src/server/game/Spells/SpellInfo.cpp     :455-497 SpellEffectInfo::IsEffect/IsAura/IsAreaAuraEffect/IsUnitOwnedAuraEffect
                                         :1346-1395 SpellInfo ctor (Attributes, AuraOptions, PPM)
                                         :1970 IsAffected  :2641 GetAllEffectsMechanicMask
                                         :4289 CalcPPMHasteMod :4315 CalcPPMCritMod :4342 CalcPPMItemLevelMod
                                         :4355 CalcProcPPM
src/server/game/Spells/Auras/SpellAuras.cpp
                                         :476  Aura::Aura (timers, charges)  :1004 CalcMaxCharges
                                         :1017 ModCharges  :1093 ModStackAmount (refresh resets charges)
                                         :1772 IsProcOnCooldown  :1777 AddProcCooldown
                                         :1793 PrepareProcToTrigger  :1810 PrepareProcChargeDrop
                                         :1820 ConsumeProcCharges  :1834 GetProcEffectMask
                                         :1981 CalcProcChance  :2010 TriggerProcOnEvent
                                         :2034 CalcPPMProcChance
src/server/game/Spells/Auras/SpellAuraEffects.cpp
                                         :1365 CheckEffectProc  :1460 HandleProc
                                         :5740-6127 periodic tick producers
                                         :6145 HandleBreakableCCAuraProc  :6155 HandleProcTriggerSpellAuraProc
                                         :6180 HandleProcTriggerSpellWithValueAuraProc
                                         :6207 HandleProcTriggerDamageAuraProc
src/server/game/Spells/Spell.cpp         :520-569 Spell ctor (trigger flags, chain length)
                                         :2339 prepareDataForTriggerSystem  :2384 FinalizeDataForTriggerSystem
                                         :2417 ProcReflectDelayed  :2825 TargetInfo::DoDamageAndTriggers
                                         :3901 cast procs  :4209 _handle_finish_phase  :4401 CAST_ENDED
                                         :5790 CheckCast cooldown skip  :8284 IsTriggered  :8300 IsProcDisabled
src/server/game/Spells/SpellEffects.cpp  :2231 / :4265 dispel  :4073 knockback  :3013 interrupt hit bit
src/server/game/Spells/SpellHistory.cpp  :244  HandleCooldowns (ignoring-cooldown early return)
src/server/game/Spells/SpellDefines.h    TriggerCastFlags, CastSpellExtraArgs, ProcFlagsInit, SpellModOp
src/server/game/Entities/Unit/Unit.cpp   :142/:195 DamageInfo hit masks  :281 ProcEventInfo::GetSpellInfo/GetSchoolMask
                                         :1344-1510 CalculateMeleeDamage flags  :2347 white swing proc
                                         :5569 ProcSkillsAndAuras  :8258 GetWeaponProcChance
                                         :8269 GetPPMProcChance  :10419 createProcHitMask
                                         :10535 GetProcAurasTriggeredOnEvent  :10580/:10619 TriggerAurasProcOnEvent
                                         :11040 SetCantProc  :11387-11400 kill/death
src/server/game/Entities/Unit/Unit.h     :644 AuraApplicationMap (std::multimap)  :765 GetClassMask
src/server/game/Entities/Player/Player.cpp
                                         :8596/:8646 CastItemCombatSpell  :25146 ApplyEquipCooldown
src/server/game/DataStores/DB2Stores.cpp GetSpellProcsPerMinuteMods (store order)
src/server/game/DataStores/DBCEnums.h    SpellProcsPerMinuteModType
src/server/game/Entities/Item/ItemEnchantmentMgr.cpp  GetRandomPropertyPoints
src/server/game/Miscellaneous/RaceMask.h RaceMask::GetRaceBit / HasRace
src/common/Utilities/Random.h/.cpp       roll_chance<float>, rand_chance
src/server/database/Updater/UpdateFetcher.* PathCompare (update order)
revision_data.h.in.cmake                 DATABASE_FULL_DATABASE
```

### Commands

```bash
# from the wowlab-data root; bag/ is the workspace scratch dir next to pallet/

# world-DB overlay (only after a Trinity bump; needs the pinned TDB dump)
gh release download TDB1200.26021 -R TrinityCore/TrinityCore -p 'TDB_full_1200.26021_2026_02_06.7z' -D ../../bag/tdb
7z e ../../bag/tdb/TDB_full_1200.26021_2026_02_06.7z TDB_full_world_1200.26021_2026_02_06.sql -o../../bag/tdb
python3 scripts/research/tools/tdb_proc_overlay.py --tdb ../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql
python3 scripts/research/tools/gen_proc_names.py

# corpora
P=scripts/research/proc_research.py; O=docs/research/procs-corpora
python3 $P --output $O/census.json census --with-ids
python3 $P --output $O/vocabulary.json vocabulary --census
python3 $P --output $O/sources.json sources
python3 $P --output $O/timeline-example.out.json timeline $O/timeline-example.json

# tests
cd scripts/research
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/ -q
make -C tools/tc_proc_probe
uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_procs_differential.py -q

# exploration
python3 proc_research.py provider 1250564
python3 proc_research.py event 1250564 '{"kind":"spell-hit","spell":133,"damage":10}'
python3 proc_research.py rppm 1237014 --haste 0.25 --since-attempt 3 --since-proc 40
python3 proc_research.py item 250228
python3 proc_research.py enchant 803
```

Witness IDs are listed in §9, §12, §13 and the full dumps are in
`procs-corpora/witnesses.json`.

External documentation was not used as semantic authority. Spell and item names
are navigation aids from the snapshot's `*_lang` columns. The sibling `simc`
checkout was not consulted.

---

## 26. Final report

### Immutable proc definition facts

These can be prepared cold, per (SpellID, difficulty), with the fallback chain
applied.

**The resolved `SpellProcEntry`:**

* ProcFlags as a 64-bit mask (both words);
* SchoolMask;
* family name and flag128 mask;
* SpellTypeMask;
* SpellPhaseMask (including the CAST default);
* HitMask, where 0 means "use the side default";
* AttributesMask (already masked to the allowed bits);
* DisableEffectsMask;
* classic PPM;
* Chance (possibly ≥ 100);
* Cooldown ms;
* Charges (uint8 after truncation);
* origin (`spell_proc` or generated), or "no entry" with its reason.

**Per aura effect:**

* is-aura;
* whether it is disabled;
* the `CheckEffectProc` gate kind and its MiscValue;
* the `HandleProc` branch;
* the trigger SpellID and whether it resolves at the provider's difficulty;
* whether the trigger adds extra attacks.

**RPPM:** base rate, and the ordered modifier list (type, param, coeff).

**Provider attributes:**

* burn-charge-on-failure, cooldown-on-failure;
* can-proc-from-procs;
* weapon-proc, only-class-abilities, suppressed-proc overrides;
* only-outdoors, only-on-caster, allow-while-sitting;
* no-equip-requirement, target-procs-on-caster.

**Other provider facts:**

* passive flag and equipped-item class/subclass/inventory mask;
* stealth aura presence;
* `DO_NOT_CONSUME_RESOURCES` and `CU_DONT_BREAK_STEALTH` (read on the event
  spell);
* acquisition roots, lifecycle and bindings.

**Event-spell static facts:**

* DmgClass;
* IS_ABILITY, TREAT_AS_PERIODIC, AUTO_REPEAT;
* school;
* family flags;
* mechanic mask;
* CU_CAN_CRIT;
* NOT_A_PROC;
* SUPPRESS_* and ALLOW_CLASS_ABILITY_PROCS;
* SUPPRESS_WEAPON_PROCS;
* CANCELS_AUTO_ATTACK_COMBAT.

**Explicitly marked, never defaulted:** unknown flag bits, script bindings with
their hook kinds, condition presence, and in-code corrections.

### Mutable proc state

Everything lives in the **`Aura` object** and is shared by all its
applications:

* `m_procCharges` / `m_isUsingCharges`;
* `m_stackAmount`, which is proc-relevant only with `USE_STACKS_FOR_CHARGES`;
* `m_procCooldown`, starting at `TimePoint::min()`;
* `m_lastProcAttemptTime`, starting at creation − 10 s;
* `m_lastProcSuccessTime`, starting at creation − 120 s;
* the removed/remove-mode flag.

Unit-level state is also read:

* `m_procDeep` (CanProc);
* `m_procChainLength`;
* `lastExtraAttackSpell`;
* the swing-timer "ready" flags (legacy chance > 100 and enchant chance 0
  only).

Legacy item and enchant procs keep **no** state.

### Event facts required from combat

Per event:

* **Masks and phase:** actor mask and action-target mask (64-bit),
  SpellTypeMask, SpellPhaseMask, HitMask.
* **Identity:** the event SpellInfo identity; whether a `Spell` object is
  attached; DamageInfo/HealInfo presence, damage amount, attack type, and the
  damage/heal school for non-Spell events; action-target presence.
* **For Spell events:**
  * `IsTriggered()` and the triggering aura SpellID;
  * whether a cast item was used;
  * whether any power cost is positive;
  * whether the cast time is nonzero;
  * the set of auras whose spell mods were applied;
  * proc-disabled (`TRIGGERED_DISALLOW_PROC_EVENTS`);
  * the proc chain length.
* **Positivity** of a no-damage/no-heal hit, which decides the helpful/harmful
  flags.

Per holder and actor:

* player or not;
* actor level (`REDUCE_PROC_60`);
* whether the action target gives XP/honor;
* holder == aura caster, and event actor == aura caster;
* outdoors, standing, feral form;
* whether the equipped weapon/shield fits and is unbroken;
* the actor's last extra-attack spell;
* condition results and script check results;
* the application's effect mask.

Per aura caster (chance): base attack time per attack type, haste multipliers,
crit percentages, class, primary spec, race, map type, aura set, the aura's
cast item level, and spell-mod results.

### RNG boundaries

* Exactly one `rand_chance()` ∈ [0,100) per aura that passes all pre-RNG checks,
  including 100% chances. The test is success iff `chance > roll`.
* Draws happen in actor-map order, then mod-owner (reversed), then target-map
  order, and all of them precede every triggered action of that event.
* Rejections, cooldown blocks, charge-less auras, the chain limit and
  `CanProc()` suppression consume nothing.
* RPPM's attempt stamp follows every draw. Its success stamp follows a
  successful, non-vetoed prepare.
* The legacy enchant combat-spell path draws **twice** per enchant effect.
  The legacy item path draws once per OnProc effect before the script hook.
* `SPELL_AURA_ADD_TARGET_TRIGGER` (`Spell::DoTriggersOnSpellHit`) is a separate,
  non-aura-proc roll per hit target and is outside this pipeline.

### Trigger boundary

A successful proc produces, per effect in the mask, one of these:

* `CastSpell(explicitTarget = other party (or actor for TARGET_PROCS_ON_CASTER),
  EffectTriggerSpell, TriggeringAura = this effect, TriggeringSpell = event
  Spell, flags = TRIGGERED_FULL_MASK & ~(IGNORE_POWER_COST | IGNORE_REAGENT_COST))`
  with the holder as caster. This covers DUMMY with a nonzero trigger,
  PROC_TRIGGER_SPELL, and PROC_TRIGGER_SPELL_WITH_VALUE (+BasePoint0 = aura
  amount).
* Direct spell damage of the aura's own amount (PROC_TRIGGER_DAMAGE).
* An amount decrement / removal (breakable CC).
* Nothing, beyond charge and cooldown bookkeeping, for the other trigger auras.

In every case, `ConsumeProcCharges` runs afterwards. The triggered spell is an
ordinary spell cast. It ignores its own spell and category cooldowns, still
pays power, cannot proc the aura that triggered it, and its own direct events
are proc-disabled for non-`CAN_PROC_FROM_PROCS` / non-`TRIGGERED_CAN_PROC`
providers.

### Generic population

| population | providers | generic |
|---|---:|---:|
| all authored | 14,227 | **7,172 (50%)** |
| current player scope | 1,195 | **280 (23%)** |

The generic shapes are fixed-or-RPPM (including all 8 modifier types) ×
optional per-Aura ICD × optional charges/stack-charges × {trigger an existing
spell, direct damage, CC break, charge/cooldown consumer}. Everything is
proved from source and differentially checked against Trinity's code.

In player scope, the largest generic buckets are:

| shape | providers |
|---|---:|
| fixed ≥ 100, no ICD, trigger | 114 |
| fixed ≥ 100, ICD, trigger | 46 |
| pure charge/cooldown consumers | 46 |
| RPPM (± haste, ± other modifiers) triggers | 16 |
| breakable CC | 16 |

### Script and exception population

Player scope:

| category | providers |
|---|---:|
| inert-only DUMMY/PROC_TRIGGER_SPELL without trigger | 550 |
| mixed (generic action + inert effect) | 78 |
| AuraScript proc hooks | 130 (151 script-bound) |
| no entry (non-trigger auras only; Trinity needs a `spell_proc` row it does not have) | 123 |
| no entry (infinite-loop guard) | 18 |
| unknown `ProcFlags2` bits | 15 |
| missing trigger spells | 2 |

All authored content:

| category | providers |
|---|---:|
| inert-only | 3,768 |
| script proc hooks | 377 |
| no entry | 1,727 |
| unknown bits | 178 |
| missing trigger | 297 |

Trinity-specific item behavior (legacy OnProc and CombatSpell enchants,
including the double roll) is confined to 184 `ItemEffect` OnProc rows and 35
`CombatSpell` enchant effects. Current gear does not use it: 17 of its 34
providers are inert, 7 carry the unknown bit, and 4 are fully generic.

### Open questions

1. **What do `ProcFlags2` 0x8 and 0x20 mean?** 0x20 is on 177 spells, including
   current trinkets and weapons. Reopen when a producer/consumer is found in a
   newer Trinity or a client consumer.
2. **Is `SpellProcsPerMinute.Flags` live?** Values 1 and 3 dominate. Trinity has
   no consumer. Reopen with a consumer, never from simulator convention.
3. **Who implements the 550 inert current providers?** Each needs a per-spell
   consumer: a script, client logic, or another spell's effect. Reopen per spell
   with a direct consumer.
4. **`IsPositive`:** needed for no-damage/no-heal hit flags and enchant
   targets. Reopen by porting `SpellInfo::_IsPositiveEffect`.
5. **Spell-mod contributions** to ProcChance, ProcFrequency, ProcCharges and
   ProcCooldown. Reopen with spell-mod archaeology (`SpellModOp` 4/18/26/38).
6. **The 1,678 flag-carrying spells without trigger auras** only proc in Trinity
   with a `spell_proc` row. For 12.1 spells there is none (build skew). Reopen
   when Trinity supports ≥ 12.1.
7. **The enchant double roll** looks accidental (`Player::CastItemCombatSpell`).
   Reopen if upstream changes it. Do not copy it without a decision.
8. **Negative RPPM** (modifier coeff ≤ −1) is computed without a guard.
   Reopen if real data combines such a modifier with another type.
9. **Per-spell `LoadSpellInfoCorrections`** on 29 proc-relevant spells are
   reported, not applied. Reopen per spell.
10. **Conditions (2 rows) and `serverside_spell`** are not evaluated or loaded.
    Reopen if a current provider depends on them.
