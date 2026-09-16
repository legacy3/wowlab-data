# Dummy / server-side spell semantics archaeology

## Scope and result

This pass asks one question of the checked-in game-data snapshot and the sibling
TrinityCore checkout: when the client data is incomplete or says *Dummy*, which
server-side mechanism supplies the behaviour, and how much of current player
content reduces to reusable semantic families versus genuinely spell-specific code.

It is research only. Nothing here touches Core, its catalogs, production
schemas, runtime combat or milestone selection. No production Dummy executor is
designed. Names and tooltips were used for navigation only; every claim below is
tied to a consumer read in TrinityCore `7f3d43b` or a row in the snapshot / the
pinned world database.

Deliverables:

| artefact | path |
|---|---|
| this report | `docs/research/dummy-server-semantics-archaeology.md` |
| research package | `scripts/research/dummy_semantics/` (standard library at runtime) |
| CLI | `scripts/research/dummy_semantics.py` |
| world-DB replay + server overlay extractor | `scripts/research/tools/tdb_replay.py`, `tools/tdb_server_overlay.py` |
| structural script index (tree-sitter) | `scripts/research/tools/tc_script_index.py` |
| dispatch-table / hook call-site extractor | `scripts/research/tools/tc_dispatch_tables.py` |
| build-skew marker | `scripts/research/tools/gen_build_skew.py` |
| differential C++ probe | `scripts/research/tools/tc_dummy_probe/` |
| tests | `scripts/research/tests/test_dummy_*.py` |
| committed corpora | `docs/research/dummy-corpora/` |

Headline results (current player scope = class trees, spec spells, current
raid/M+ gear, sets, gems, enchants and their authored trigger edges; 40 specs,
4,128 root spells, 4,836 reachable spells):

1. **The population is dominated by Dummy auras that Trinity never reads.**
   4,325 population effects on 2,750 owner spells are reachable. 2,978 of them
   are `SPELL_AURA_DUMMY` on 2,069 owners. For 2,215 owners (81% of the population
   owners) no server-side layer at all names the spell: no script, no observer, no
   world-DB row, no hardcoded case. A further 69 owners are newer than the last
   client build Trinity supports and 38 carry a script whose hooks no longer match
   the 12.1 effect layout. These are *unimplemented*, not *unique* (§20, §24).
2. **Everything Trinity does implement for current players reduces to 15
   structural families.** 385 reachable spells carry 625 executing script hooks.
   All 625 fall into one of 15 code shapes (`cast-child`, `cast-child-with-amount`,
   `amount-adapter`, `proc-filter-adapter`, `linked-aura-mutation`, `target-adapter`,
   `cooldown-mutation`, `choose-among-children`, `random-child`, `suppress-default`,
   `cast-gate`, `delayed-child`, `pet-owner-forward-cast`, `resource-mutation`,
   `consume-and-cast`) plus a `state-only` bookkeeping shape. **Zero executing
   hooks in player scope are unclassified** (§5, §20). Across all authored
   content the structural tail is 48 hooks on 46 spells, all legacy content (§6).
3. **The ordinary-action boundary is almost universal.** 316 of the 625 hooks end
   in `CastSpell` of an authored child SpellID (204 plain, 72 with forwarded
   BasePoints, 17 choose, 15 random, 4 delayed, 4 pet-forwarded); 92 rewrite an
   amount inside an ordinary damage/heal/absorb pipeline; 39 mutate another
   aura; 21 touch cooldowns; 4 touch power. Only 8 hooks suppress a default with
   no other action (§8, §24 "Ordinary-action boundary").
4. **The world-database layers are marginal for current players.** Of the world
   tables that supply spell behaviour, only `spell_script_names` matters in
   player scope (523 bindings). `spell_linked_spell` contributes 6 rows,
   `spell_group` 7, `spell_threat` 10, `conditions` 1 row (Shadowstep), `spell_custom_attr` 2,
   `spell_area` / `spell_target_position` / `spell_pet_auras` / `spell_required` 0.
   `serverside_spell` (4,400 server-only spells) is loaded and is reached by
   exactly 2 player effects; `spell_scripts` has no loader and no rows (§10).
5. **Hardcoded engine IDs are not the mechanism.** 334 literal-SpellID sites exist
   in the engine; 120 are gameplay semantics and they normalise to 30 body
   shapes. In player scope 26 sites survive, 17 of them load-time classification
   (`_LoadSpellSpecific`, DR groups, immunities); the 2 gameplay branches
   (Kill Command 34026, Bestial Wrath 19574) are legacy dead code for current data (§6).
6. **Marker/state auras are real but small.** 157 of the 2,069 Dummy-aura owners
   are observed by a strong consumer (137 by script `HasAura/GetAuraEffect`
   queries, 15 by `SpellAuraRestrictions.*AuraSpell` in the client data itself,
   9 by engine cases). 132 of the 523 bindings gate the entire script in `Load()`
   on `HasAura(<talent>)`: the talent Dummy aura is a boolean marker (§9).
7. **The proc census resolves as follows.** Of the 550 inert-only, 78 mixed and
   130 script-bound current providers: 591 have no consumer at all, 15 are build
   skew, 14 are marker-only, 49 are ordinary triggers supplied by code, 38 fall in
   other reusable families, 35 are amount adapters, 1 is a target adapter, 13 are
   bound to a script whose hooks no longer match the 12.1 effect layout, and 2 are
   state-only scripts (§16).

Everything unknown fails closed: unresolved scripts, hooks whose effect masks
are 0, condition types without an evaluator, unnamed flag bits, and every
unobserved Dummy aura are reported as unknown, never given a default.

---

## 1. Population

Effect identity is the `SpellEffect` row (`ID`), kept distinct from the owner
`SpellID`. The population is defined from direct dispatch consumers
(`SpellEffectHandlers[]`, `AuraEffectHandler[]`, `AuraEffect::PeriodicTick`,
`AuraEffect::HandleProc`; corpus `dispatch-tables.json`):

| class | generic consumer | what it does without a script |
|---|---|---|
| `dummy-effect` (`SPELL_EFFECT_DUMMY`) | `Spell::EffectDummy` (SpellEffects.cpp:571) | `spell_pet_auras` lookup, otherwise nothing |
| `script-effect` (`SPELL_EFFECT_SCRIPT_EFFECT`) | `Spell::EffectScriptEffect` (:3069) | hardcoded `switch (SpellFamilyName) / switch (Id)`, otherwise nothing |
| `dummy-aura` (`SPELL_AURA_DUMMY` on any aura effect) | `AuraEffect::HandleAuraDummy` (SpellAuraEffects.cpp:4861); `HandleProc` (:1460) | pet auras + hardcoded switch; on proc, behaves as PROC_TRIGGER_SPELL (casts `TriggerSpell` if nonzero) |
| `periodic-dummy` (`SPELL_AURA_PERIODIC_DUMMY`) | `AuraEffect::PeriodicTick` (:1301) | `// handled via scripts` -- nothing |
| `trigger-no-trigger` | every `EffectTriggerSpell`-casting handler | logs `does not have triggered spell`, returns |
| `trigger-missing` | same | logs `tried to trigger unknown spell`, returns |
| `unimplemented-effect` | `Spell::EffectNULL` / `EffectUnused` (125 + 33 table slots) | nothing (aura-carrying effects excluded: Aura creation consumes them) |
| `unimplemented-aura` | `AuraEffect::HandleNULL` / `HandleUnused` (251 + 18 slots) without an "implemented in" note | nothing |

Counts (`DifficultyID = 0` rows; other difficulties carry 1,784 more population
rows and are counted separately in `population.json`):

| class | all effects | all owners | player effects | player owners |
|---|---:|---:|---:|---:|
| dummy-effect | 72,411 | 65,533 | 678 | 504 |
| script-effect | 9,867 | 9,668 | 11 | 10 |
| dummy-aura | 79,008 | 73,395 | 2,978 | 2,069 |
| periodic-dummy | 8,747 | 8,684 | 119 | 116 |
| trigger-no-trigger | 2,089 | 2,045 | 54 | 53 |
| trigger-missing | 3,113 | 2,972 | 2 | 2 |
| unimplemented-effect | 18,383 | 17,300 | 32 | 32 |
| unimplemented-aura | 3,774 | 2,774 | 451 | 173 |
| **total** | **197,392** | **173,643** | **4,325** | **2,750** |

Player-scope unimplemented aura types are dominated by PvP modifiers
(`ADD_PCT_PVP_MODIFIER` 181, `..._BY_SPELL_LABEL` 38+18), `MOD_SUMMON_DAMAGE` 65,
`SPELL_AURA_531` 55, `MOD_LEECH` 20, `MOD_PET_STAT_PCT` 16; unimplemented effects
are `SPELL_EFFECT_324` (20) and crafting/loot effects. None of those is a Dummy in
the client's sense; they are aura types Trinity has not implemented.

Scope construction (`dummy_semantics/scope.py`): roots are
`TraitTreeLoadout → TraitNode → TraitNodeXTraitNodeEntry → TraitNodeEntry → TraitDefinition.SpellID`
(3,545), `SpecializationSpells` + mastery (463), `ItemEffect` spells of the 378
current raid/M+ items (111), `ItemSetSpell` of their sets (1), gems of the
newest `ItemSparse.ExpansionID` (5) and enchant scrolls (`SPELL_EFFECT_ENCHANT_ITEM*`
OnUse effects, 19). 122 trait definitions point at SpellIDs absent from the
snapshot and are dropped. Authored edges: `EffectTriggerSpell` (any effect/aura
type), `SpellLearnSpell`, `OVERRIDE_ACTIONBAR_SPELLS` misc values,
`OVERRIDE_SPELLS → OverrideSpellData`. Max depth 3. Class skill lines
(`SkillLine.CategoryID = 7`) are available as an extended scope
(`Scope(include_class_skills=True)`) but were excluded: those lines also hold
Mounts (1,824 rows), Companions (1,707) and pet families.

---

## 2. Server-side semantic layers

Read from the consumers, bottom up:

```
Blizzard DB2 facts (SpellEffect, SpellAuraRestrictions, ...)
  + Trinity world-DB overlays   spell_script_names, spell_linked_spell, spell_area, spell_target_position,
                                spell_pet_auras, spell_group(+stack_rules), spell_threat, spell_required,
                                spell_learn_spell, spell_custom_attr, conditions(13/17/24/18/21/35),
                                serverside_spell(+_effect), areatrigger_create_properties(.ScriptName)
  + load-time code overlays     SpellMgr::LoadSpellInfoCorrections (191 ApplySpellFix blocks, 373 ids),
                                LoadSpellInfoCustomAttributes (AttributesCu derivation + 3 hardcoded ids),
                                SpellInfo::_Load{SpellSpecific,AuraState,DiminishInfo,ImmunityInfo}, _IsPositiveEffect
  + generic engine consumers    SpellEffectHandlers[356], AuraEffectHandler[662], PeriodicTick, HandleProc,
                                CheckEffectProc, Aura::HandleAuraSpecificMods, Unit::GetCastSpellInfo
  + script hook system          ScriptMgr::CreateSpell/AuraScripts -> 23 SpellScript + 29 AuraScript hook types,
                                59 call sites (dispatch-tables.json), PreventDefaultAction / PreventHitDefaultEffect
  + reusable script families    15 structural families, 92 sub-kinds in player scope (§5)
  + shared helpers              95 free functions in scripts/Spells (e.g. ApplyWhirlwindCleaveAura,
                                SetBonusValueForEffect hub with 77 users), same-class helper methods,
                                nested BasicEvent classes (delayed casts)
  + genuinely spell-specific    hardcoded id tables inside scripts (Elemental Overload's 5 pairs),
                                cross-script state (GetScript<T>()), engine case labels (§6)
    -> runtime semantic actions cast / add-aura / remove-aura / mod-stacks / mod-cooldown / mod-power /
                                set-amount / select-targets / gate-proc / gate-cast / schedule / marker
```

Direct consumer coordinates are listed in §23. Two facts about the generic
layer decide most of the census:

* `Spell::EffectDummy` and `AuraEffect::HandleAuraDummy` are **not** extension
  points. They consult `spell_pet_auras` (2 rows in the whole database, none
  current) and a hardcoded switch of 3.x-era ids (34 case labels). A Dummy effect
  without a script therefore does nothing.
* `AuraEffect::HandleProc` treats `SPELL_AURA_DUMMY` exactly like
  `SPELL_AURA_PROC_TRIGGER_SPELL`: if `EffectTriggerSpell` is nonzero it casts it
  (`HandleProcTriggerSpellAuraProc`, :6155), otherwise it logs and returns. This
  is why the proc research found 550 inert providers: the aura is a valid proc
  carrier whose payload is supplied only by an `OnEffectProc` hook.

---

## 3. Script-binding map

`ObjectMgr::LoadSpellScriptNames` (ObjectMgr.cpp:5965) binds `spell_script_names`
rows to SpellIDs; a negative id binds every rank from the first
(`SkillLineAbility.SupercedesSpell` chains; 3 such rows). `ScriptMgr::CreateSpellScripts`
/ `CreateAuraScripts` (ScriptMgr.cpp:1425-1462) instantiate whatever the
registration produces: `RegisterSpellScript(cls)` (2,477 uses), `RegisterSpellScriptWithArgs`
(166, constructor-parameterised), `RegisterSpellAndAuraScriptPair` (52+4), legacy
`SpellScriptLoader` subclasses (172). Binding is per SpellID regardless of
difficulty (`Aura::LoadScripts` / `Spell::LoadScripts` pass `m_spellInfo->Id`).

Every effect-matched hook is validated by `SpellScriptBase::EffectHook::GetAffectedEffectsMask`
(SpellScript.cpp:120-146) against the loaded `SpellInfo`: `EFFECT_n` must exist
and match `Effect` / `ApplyAuraName` / `TargetA|B`; `EFFECT_ALL` collects every
match, `EFFECT_FIRST_FOUND` the lowest. A mask of 0 means the hook is registered
but **never executes** (`_Validate` logs, the server continues). The binding map
(`bindings.json`) records this per hook.

| | all | player |
|---|---:|---:|
| bound spells / bindings / distinct scripts | 3,640 / 3,812 / 2,763 | 432 / 523 / 456 |
| bindings whose ScriptName has no registration | 223 | 6 |
| SpellScript / AuraScript / both | 2,069 / 1,468 / 52 | 278 / 223 / 16 |
| hooks registered / never executing (mask 0) / executing | 4,886 / 258 / 4,579 | 717 / 74 / 625 |
| bindings that call PreventDefaultAction / PreventHitDefaultEffect | 418 | 35 |
| bindings with constructor arguments | 136 | 13 |
| bindings with mutable script fields | 398 | 51 |
| bindings gated in `Load()` (HasAura 103, HasAuraEffect 12, HasSpell 7, ...) | -- | 132 |

The 6 unresolved player bindings are `spell_dru_incapacitating_roar`,
`spell_warr_meat_cleaver_damage_bonus_thunder_clap` (×2), `spell_pri_prayer_of_mending_dummy`,
`spell_dru_berserk`, `spell_dru_stampeding_roar`: rows exist in the world DB but no
class registers those names in this checkout (database/source drift).

The 74 never-executing player hooks are **effect-layout drift** between the data
Trinity's authors saw (≤ 12.0.7) and the 12.1 snapshot. Witness: `spell_warr_avatar`
registers `OnEffectHitTarget(EFFECT_5, SPELL_EFFECT_SCRIPT_EFFECT)` for Avatar 107574; in
the snapshot effect 5 is `MOD_AOE_DAMAGE_AVOIDANCE` and no SCRIPT_EFFECT exists.
13 proc providers are affected (§16).

Hook-list usage in player scope (executing): `OnEffectProc` 95, `OnEffectHitTarget` 92,
`AfterCast` 51, `DoCheckEffectProc` 44, `CalcDamage` 36, `DoCheckProc` 33,
`OnEffectPeriodic` 31, `AfterEffectRemove` 26, `AfterHit` 25, `OnObjectTargetSelect` 20,
`DoEffectCalcAmount` 18, `OnProc` 16, `AfterEffectApply` 14, `OnEffectHit` 14,
`OnEffectLaunch(Target)` 13+13, `OnHit` 11, `OnObjectAreaTargetSelect` 10,
`CalcHealing` 9, `OnCast` 8, `OnCalcCritChance` 7, `OnCheckCast` 7, then ≤ 6 each.

---

## 4. Hook vocabulary and ordering

`dummy_semantics/hooks.py` (corpus `hooks.json`) models all 23 `SpellScriptHookType`
and 29 `AuraScriptHookType` values with their call site, phase, default-prevention
rule and mutability, cross-checked against the header's 51 `HookList<>` members
(test `test_hook_vocabulary_covers_every_header_hook_list`). The verified order of
one cast (Spell.cpp) and one aura (SpellAuras.cpp / SpellAuraEffects.cpp):

```
Spell::prepare    CalcCastTime(virtual) -> OnPrecast(virtual) -> CheckCast: generic rules, conditions(17), OnCheckCast
Spell::_cast      BeforeCast -> SelectSpellTargets [OnObject{Area,}TargetSelect, OnDestinationTargetSelect per effect,
                  after generic search + implicit-target conditions(13)] -> OnCast -> HandleLaunchPhase
                  [HandleEffects LAUNCH / LAUNCH_TARGET: OnEffectLaunch(Target), damage/heal amounts computed]
handle_immediate  _handle_immediate_phase: HandleEffects HIT (OnEffectHit) per effect, no unit
                  per unit target:  PreprocessTarget -> PreprocessSpellHit -> BeforeHit -> OnHit
                                    DoTargetSpellHit per effect -> DoSpellEffectHit: Aura::TryRefreshStackOrCreate,
                                      HandleEffects HIT_TARGET (OnEffectHitTarget; PreventHitDefaultEffect skips the handler)
                                    DoDamageAndTriggers: heal/damage (CalcDamage / CalcHealing / OnCalcCritChance /
                                      OnCalculateResistAbsorb inside Unit::*BonusDone / CalcAbsorbResist), aura apply,
                                      ProcSkillsAndAuras, DoTriggersOnSpellHit (spell_linked_spell HIT), AfterHit
                  _handle_finish_phase: finish-phase procs
Spell::_cast      AfterCast -> spell_linked_spell CAST -> cooldown/charges
```

```
AuraEffect::CalculateAmount    generic CalcValue -> per-aura-type hardcoded amounts -> DoEffectCalcAmount -> × stacks
AuraEffect::HandleEffect       _RegisterAuraEffect, ApplySpellMod -> OnEffectApply/OnEffectRemove (PreventDefaultAction)
                               -> AuraEffectHandler[type] -> AfterEffectApply/AfterEffectRemove
Aura::HandleAuraSpecificMods   spell_area autocast/autoremove, spell_linked_spell AURA/REMOVE, family switch
AuraEffect::PeriodicTick       OnEffectPeriodic (PreventDefaultAction) -> switch(aura type): PERIODIC_DUMMY nothing
Aura::GetProcEffectMask        generic flags, conditions(24) -> DoCheckProc -> per effect CheckEffectProc
                               [DoCheckEffectProc then aura-type gates] -> roll (CalcProcChance)
Aura::PrepareProcToTrigger     DoPrepareProc (PreventDefaultAction) -> charge drop / cooldown
Aura::TriggerProcOnEvent       OnProc (PreventDefaultAction) -> per effect HandleProc
                               [OnEffectProc (PreventDefaultAction) -> switch: DUMMY/PROC_TRIGGER_SPELL cast TriggerSpell,
                               WITH_VALUE, PROC_TRIGGER_DAMAGE, breakable CC -> AfterEffectProc] -> AfterProc -> ConsumeProcCharges
```

Default prevention is exact: `SpellScript::PreventHitDefaultEffect` is honoured
only in effect hooks and hit hooks (`IsInHitPhase() || IsInEffectHook()`),
`AuraScript::PreventDefaultAction` only in EFFECT_APPLY/REMOVE/PERIODIC/ABSORB/SPLIT
/PREPARE_PROC/PROC/EFFECT_PROC (SpellScript.cpp:998-1035). `SetHitDamage/SetHitHeal`
have effect only in EFFECT_LAUNCH_TARGET, EFFECT_HIT_TARGET, BEFORE_HIT and HIT
(`IsInModifiableHook`). Scripts on the same spell run in `spell_script_names`
order; a `preventDefault` from any of them wins.

---

## 5. Reusable server-side families

Structural clustering (`dummy_semantics/families.py`) assigns each executing hook
a family from its code shape with a fixed precedence (hook role first, then the
action it performs). Every family below was **verified by reading the witness code**
(coordinates in `witnesses.py`; structural cross-check in
`test_witnesses_agree_with_structural_index`). Counts are current player scope.

| family (proved) | hooks | spells | scripts | source facts / consumer | immutable inputs | explicit runtime inputs | mutable state | RNG | output action | negative near-match |
|---|---:|---:|---:|---|---|---|---|---|---|---|
| cast-child | 204 | 171 | 171 | `Unit::CastSpell(target, id, args)` from Effect/Hit/Cast/Apply/Remove/Proc hooks | child SpellID (script constant or `SpellAuraRestrictions.*AuraSpell`), trigger flags | caster, hit unit / aura target / proc target | none | none | ordinary cast of an authored spell | `suppress-default` (cast replaced by nothing) |
| cast-child-with-amount | 72 | 66 | 64 | same + `CastSpellExtraArgs::AddSpellMod(SPELLVALUE_BASE_POINTn, x)` / `SpellValueOverrides` | child SpellID, effect index, percentage (talent amount) | damage/heal info amount, aura amount, health/power, AP/SP | none | none | ordinary cast with fixed BasePoints | `amount-adapter` (no cast, rewrites in place) |
| amount-adapter | 92 | 80 | 57 | `CalcDamage/CalcHealing/DoEffectCalcAmount/OnCalcCritChance/OnEffectAbsorb` writes, `SetHitDamage/SetHitHeal/SetEffectValue/SetSpellValue` | percentage from a talent aura amount or another spell's effect value | caster aura amounts, victim identity, health pct | occasionally a float member (`_pctMod`) | none | scalar into the ordinary damage/heal/absorb/duration pipeline | `state-only` (reads but writes nothing) |
| proc-filter-adapter | 77 | 64 | 64 | `DoCheckProc` / `DoCheckEffectProc` predicates before the roll | spell family masks, effect indices | proc spell info, `m_appliedMods`, damage/heal info, target auras | none | 23 hooks roll `roll_chance(aurEff->GetAmount())` (script RNG replaces the generic chance) | accept/reject proc | generic `ProcFlags`/`spell_proc` gating |
| linked-aura-mutation | 39 | 38 | 37 | `RemoveAurasDueToSpell`, `RefreshDuration`, `SetDuration`, `ModStackAmount`, `ChangeAmount` on another aura | linked SpellID | aura target / caster, remove mode | none | none | modify-aura | `spell_linked_spell` REMOVE rows (data-driven twin) |
| target-adapter | 31 | 21 | 22 | `OnObject{Area,}TargetSelect` / `OnDestinationTargetSelect` list edits | target type, effect index | candidate list, explicit target, caster auras | none | 3 hooks (RandomResize) | select-targets | `DoCheckAreaTarget` (predicate only) |
| cooldown-mutation | 21 | 19 | 17 | `SpellHistory::ModifyCooldown/ResetCooldown/RestoreCharge` | SpellID, amount from another spell's effect value | caster history, spec | none | none | modify-cooldown | `SPELL_AURA_MOD_SPELL_CATEGORY_COOLDOWN` (generic) |
| choose-among-children | 17 | 15 | 15 | ≥ 2 child casts selected by branch (`IsFriendlyTo`, switch on id) or all cast | child SpellIDs | reaction, proc spell id | none | none | one or several casts | `random-child` |
| random-child | 15 | 14 | 15 | `roll_chance(x)` guarding a cast | chance (talent amount) | caster aura amount | none | script roll | cast or nothing | generic proc chance |
| suppress-default | 8 | 4 | 4 | `PreventHitDefaultEffect`/`PreventDefaultAction` and nothing else | effect index, gating aura ids | caster auras | none | none | no-action | -- |
| cast-gate | 7 | 7 | 7 | `OnCheckCast` returning `SpellCastResult` | -- | explicit target reaction / facing / path | none | none | gate-cast | `conditions` source 17 |
| delayed-child | 4 | 4 | 4 | `m_Events.AddEventAtOffset(lambda/BasicEvent)` casting later | child SpellID, delay | GUID-captured targets | scheduled event | none | schedule + cast | `SPELL_EFFECT_TRIGGER_SPELL` `MiscValue` delay (generic) |
| pet-owner-forward-cast | 4 | 4 | 3 | cast issued by a controlled summon / owner | creature entries, child SpellID | `m_Controlled` list | summon timer | none | cast (other caster) | `spell_pet_auras` (generic twin, unused) |
| resource-mutation | 4 | 4 | 4 | `ModifyPower` | power type, percentage | duration ratio, power cost, proc spell | none | 1 hook rolls | modify-power | `SPELL_EFFECT_ENERGIZE` (generic) |
| consume-and-cast | 2 | 2 | 2 | own `DropCharge/Remove` then cast | child SpellID | aura target, damage info | none | none | modify-aura + cast | `linked-aura-mutation` |
| state-only (not a family) | 28 | 23 | 23 | bookkeeping: GUID lists, primary-target marks, cosmetic visuals | -- | hit unit identity | script fields | 2 | marker | -- |

Sub-kinds (92 in player scope, `families.json → sub_kinds`) pin the scalar source
and the cast target: `cast-child/{caster 51, aura-target 46, hit-unit 32, aura-spell-field 9,
proc-target 6, destination 4}`, `cast-child-with-amount/{aura-amount 23, damage-copy+aura-amount 10,
computed 7, heal-copy+aura-amount 4, effect-value 5, pct-of-health 2, pct-of-power 1, combo 1}`,
`amount-adapter/{aura-amount 36, effect-value 8, pct-math 8, hit-amount:damage-copy 7, pct-of-health 4, ...}`.

Two families are **parameterised by client data rather than by code**:
`spell_gen_trigger_exclude_caster_aura_spell` / `..._target_aura_spell` (18 + 22
bindings) cast `GetSpellInfo()->ExcludeCasterAuraSpell` / `ExcludeTargetAuraSpell`
from `SpellAuraRestrictions`. `spell_warr_improved_whirlwind_cleave` (31 bindings)
is one `CalcDamage` adapter whose parameter is `SpellClassMask` of the Whirlwind
cleave aura.

Representative witnesses per family (code read, chain in `witnesses.json`):
Shadow Bolt 686 → 194192 (cast-child/caster, AfterCast); Immolate 348 → 157736
(cast-child/hit-unit, OnEffectHitTarget, default not prevented); Rejuvenation /
Cultivation 774 → 200389 (conditional cast on tick, talent amount as threshold);
Prayer of Healing / Prayerful Litany 596 (`AddPct(pctMod, talentAmount)` for the
explicit target, `Load()` gate); Stormstrike / Stormblast 32175 → 390287
(`CalculatePct(GetHitDamage(), pct)`, cross-script `GetScript<>()` state); Divine Aegis
47515 → 47753 and Light's Beacon 53651 → 53652 (heal copy into BasePoints,
Beacon prevents the default and picks the beacon target); Mind Blast / Dark
Indulgence 8092 → 198069 (`roll_chance(amount)`); Stealth 1784 → 98877/158185/158188
(three children on apply, two removed on remove); Holy Prism 114165 (friend/foe
choice); Infusion of Light 54149 (per-effect proc filters: `m_appliedMods` and
`IsAffected`); Holy Word: Serenity → Salvation cooldown (−EFFECT_2 seconds);
Empyreal Blaze (duration override via `SetSpellValue`); Cauterize 86949
(suppress TRIGGER_SPELL effect 2); Elemental Overload 168534 (rolled filter,
`PreventDefaultAction`, 400 ms delayed cast of a 5-entry id table); Inescapable
Torment (cast by the controlled Shadowfiend/Mindbender/Voidwraith, summon timer
extended); Rupture → Venomous Wounds (energy refund on death, duration ratio).

---

## 6. Genuinely bespoke code

Hardcoded literal SpellIDs in the engine (`hardcoded.json`; sites with a
spell-typed switch subject or call and an id that exists in the snapshot):

| | all | player |
|---|---:|---:|
| raw SpellID sites / distinct ids | 334 / 297 | 26 / 22 |
| source classification (`_LoadSpellSpecific`, `_LoadAuraState`, DR groups, immunities, positivity) | 115 | 17 |
| gameplay semantic (Dummy/ScriptEffect/ForceCast/TriggerSpell/Transform/HandleAuraSpecificMods ...) | 120 | 3 |
| legacy content policy (`SpellInfo::CheckLocation`, `SpellArea::IsFitToRequirements`) | 12 | 0 |
| navigation / validation / cast-validation / amount adapters | 18 | 1 |
| unlisted functions (heal prediction, GCD, player repop, gossip, duel, AFK) | 69 | 5 |

Normalising the 69 gameplay `case` bodies gives 30 shapes: 14 `EffectScriptEffect`
cases that do nothing but guard (Shadow Flame family), 8 Dummy-aura cases that
only set flags/factions, 5 Dummy-aura `CastSpell(child)` (one family, 5
parameters), 4 ScriptEffect `CastSpell(child)`, 2 `CastSpell + RemoveAurasDueToSpell`,
and singletons (AddThreat, MoveFall, SetEntry, ApplySpellImmune, sounds,
LeaveBattleground, item destruction). None is current content: the two player-
reachable gameplay branches, Kill Command 34026 (`HandleAuraDummy`, casts 34027 /
sets stacks on pet aura 58914) and Bestial Wrath 19574 (`HandleAuraSpecificMods`,
The Beast Within), reference 3.x child ids and are dead for 12.1 data.

Genuinely unique behaviour among *scripts*: in player scope none (0 unclassified
hooks). Across all content 48 hooks on 46 spells are unclassified, all legacy
(Muisek Vessel item family 11885-11889, Kel'Thuzad chains, Illidan/Akama,
Headless Horseman, Oscillating Field). Spell-specific *parameterisation* inside
otherwise reusable families does exist and is where the real tail lives:
hardcoded id tables (`spell_sha_mastery_elemental_overload::GetTriggeredSpellId`,
5 pairs), creature-entry lists (`spell_pri_inescapable_torment`), cross-script
state (`GetScript<spell_sha_stormblast>()->AllowedOriginalCastId`), and script
member state (51 player bindings: `_appliedAtonements` GUID list,
`_procTarget`, `_wasStealth`, `_pctMod`, ...).

---

## 7. Structural script index

`tools/tc_script_index.py` parses `src/server/scripts/**/*.cpp` (836 files, 6
recoverable parse errors, none in Spells/ except a member-pointer call in
spell_priest.cpp) and 34 engine files with tree-sitter-cpp 0.23.4. It extracts
4,679 script classes (1,504 SpellScript, 1,126 AuraScript, 137 AreaTriggerAI, 172
legacy SpellScriptLoader, plus creature/GO/instance scripts), 4,160 registrations,
95 free helper functions, per-class `Register()` hooks (list, Fn macro, handler,
effect index, effect/aura/target token), `Validate()` id lists, constructor
names/arguments, mutable fields, nested `BasicEvent` classes, and per-method facts:
categorised calls (cast, prevent, amount, aura, cooldown, power, rng, delay,
summon, target, state), referenced constants resolved through per-file enums,
`case` labels and `==` comparisons, loops and lambdas. For the engine it records
every `case <int>` with its switch subject and body calls, every id-carrying call,
and every `ApplySpellFix` block with the members its lambda writes. Constants that
conflict across files (997 names, mostly boss `ACTION_*/EVENT_*`) are never
resolved globally.

Handler facts are merged with same-class helper methods, same-file free helpers
and nested event classes (`ScriptIndex.merged_facts`) so that `OnProcArms → HandleProc`
or `EffectHit → new FlurryEvent(...)::Execute` carry their real actions.
The index is navigation: families were verified against consumers (§5).

---

## 8. Representative lifecycles

The eight lifecycle shapes from the brief, with witnesses (all traced in
`witnesses.json`):

| shape | witness | trace |
|---|---|---|
| no consumer | 2,215 player owners (e.g. talent 200390 Cultivation *aura* itself) | Dummy aura applied by the talent's passive cast → `HandleAuraDummy` REAL: no pet aura, no case → nothing; observed only as a value holder by another spell's script |
| full replacement | Light's Beacon 53651, Elemental Overload 168534 | proc → `OnEffectProc` → `PreventDefaultAction()` → script cast; `HandleProcTriggerSpellAuraProc` skipped |
| augmentation | Immolate 348, Shadow Bolt 686, Stealth 1784 | generic effect/aura handler runs; hook adds a cast before/after it |
| proc carrier | Divine Aegis 47515 | DUMMY aura with ProcFlags → generic eligibility + roll → `OnEffectProc` cast; default (TriggerSpell 0) logs and returns |
| marker/state only | Avenging Wrath 31884, Pillar of Frost 51271, Bone Shield 195181 | aura executes nothing; read by other scripts' `HasAura/GetAuraEffect` and/or by `SpellAuraRestrictions` on other spells |
| meaningful amount / misc fields | Prayerful Litany, Cultivation, Dark Indulgence | `aurEff->GetAmount()` of the talent DUMMY effect is the percentage / threshold / chance |
| relationship encoded elsewhere | `spell_gen_trigger_exclude_*_aura_spell` (40 bindings), `spell_linked_spell` (6 player rows) | child SpellID comes from `SpellAuraRestrictions` or a world row, not from the script |
| hardcoded non-script consumer | Kill Command 34026, Bestial Wrath 19574 | engine `case` in `HandleAuraDummy` / `HandleAuraSpecificMods`; legacy ids, dead for current data |

---

## 9. Marker / state-only Dummies

Observers of a Dummy aura that the corpora can prove (`markers.json`):

| observer | player Dummy-aura owners |
|---|---:|
| `trait-node` (a TraitDefinition points at it; acquisition only) | 1,772 |
| `proc-provider` (carries ProcFlags; see proc research) | 756 |
| `script-aura-query` (another script's `HasAura/GetAura/GetAuraEffect(<id>)`) | 137 |
| `script-validate` (named in some `ValidateSpellInfo`; navigation only) | 105 |
| `script-own-amount` (its own script reads `aurEff->GetAmount()`) | 59 |
| `db2-aura-restriction` (`SpellAuraRestrictions.{Caster,Target,Exclude*}AuraSpell` of another spell) | 15 |
| `engine-hardcoded` | 9 |
| `spell-linked`, `script-aura-remove` | 2, 2 |

157 of 2,069 owners have a strong observer (script query, DB2 restriction,
engine, linked table); 1,792 have none and no script of their own; 41 of those are
newer than Trinity's supported build. The strong-observer combinations are
`script-aura-query` alone (131), `db2-aura-restriction` alone (11),
`engine-hardcoded` alone (7), mixed (8). The DB2 restriction observers are notable
because they are *client* facts: Stealth 1784, Blade Flurry 13877, Avenging Wrath
31884, Killing Spree 51690, Water Shield 52127, Vampiric Blood 55233, Thunder
Focus Tea 116680, Bone Shield 195181 gate other spells' `CheckCast` without any
server code.

Marker lifetime and stacks follow the ordinary aura (duration/stacks from
`SpellMisc.DurationIndex` / `SpellAuraOptions.CumulativeAura`); no marker in player
scope has its amount, stacks or duration mutated by a consumer other than its
own script (`linked-aura-mutation` edges in `graph.json`). 132 bindings use the
marker as a **load gate**: `Load()` returns `GetCaster()->HasAura(TALENT)`, so the
whole script is inert unless the talent aura is present. A marker aura therefore
needs no independent execution; it needs to *exist* on the right unit with the
right amount.

---

## 10. World-DB / server data

Pinned `TDB_full_world_1200.26021_2026_02_06.sql` (sha256 `54ddf4c1…5172d`) plus
all 522 `sql/updates/world/master` files replayed with the schema-driven
interpreter (`tools/tdb_replay.py`: INSERT/REPLACE/DELETE/UPDATE with literal or
self-referential bit/arithmetic SET expressions, case-insensitive columns,
`SET @var`); 0 unparsed statements (the proc overlay had 1).

| table | loader | consumer | base → final rows | player-relevant | precedence / fallback |
|---|---|---|---:|---:|---|
| spell_script_names | ObjectMgr::LoadSpellScriptNames | ScriptMgr::Create*Scripts | 3,612 → 4,016 | 523 bindings / 432 spells | per SpellID; negative id = all ranks; disabled if the script fails `_Validate` shape (never happens: only logs) |
| serverside_spell / _effect | SpellMgr::LoadSpellInfoServerside | SpellInfo store | 4,397/3,187 → 4,400/3,197 | 2 player effects trigger a server-only id (200174:1 → 41967, 49028:1 → 1206) | refused if the id exists in `SpellName.db2` (10 ids collide with the 12.1 snapshot and would be rejected at load) |
| spell_linked_spell | LoadSpellLinked | Spell::finish (CAST), DoTriggersOnSpellHit (HIT), Aura::HandleAuraSpecificMods (AURA/REMOVE) | 339 → 223 | 6 rows | trigger < 0 ⇒ type REMOVE; self-loops skipped |
| spell_area | LoadSpellAreas | SpellInfo::CheckLocation, Player::UpdateAreaDependentAuras, HandleAuraSpecificMods | 954 → 952 | 0 | hardcoded Battlefield branches for 6 ids |
| spell_target_position | LoadSpellTargetPositions | Spell::SelectImplicitDestTargets (TARGET_DEST_DB) | 2,473 → 2,513 | 0 | -- |
| spell_group / _stack_rules | LoadSpellGroups | Unit::_IsNoStackAuraDueToAura | 297 / 13 | 7 rows (groups 1500-1502: flasks/elixirs) | -- |
| spell_threat | LoadSpellThreats | ThreatManager / HandleThreatSpells | 21 | 10 (legacy ids that are still current spells) | -- |
| spell_pet_auras | LoadSpellPetAuras | Spell::EffectDummy, HandleAuraDummy → Player::AddPetAura → Pet::CastPetAura | 2 | 0 | entry-specific row before `pet = 0` row |
| spell_required / spell_learn_spell | LoadSpellRequired / LoadSpellLearnSpells | Player::LearnSpell | 21 / 5 | 0 | -- |
| spell_custom_attr | LoadSpellInfoCustomAttributes | AttributesCu | 140 → 141 | 2 (32375, 32592: CU 0x40 dont-break-stealth) | OR-ed onto derived bits |
| conditions (spell sources 13/17/18/21/24/35) | ConditionMgr::LoadConditions | see §12 | 16,951 → 17,344 (4,233 spell-sourced) | 1 row (source 17, Shadowstep 36554) | ElseGroup OR of ANDs |
| areatrigger_create_properties (.ScriptName) | AreaTriggerDataStore | AreaTrigger::CreateAreaTrigger → AreaTriggerAI | 360 → 375 (146 scripted) | 53 player CREATE_AREATRIGGER effects: 36 have a row, 21 a script | id from `EffectMiscValue_0` |
| creature_template (ScriptName/AIName) | ObjectMgr | pet/guardian AI | 222,001 → 222,359 | 53 player summon entries, 3 with a template row (Shadowfiend/Mindbender, Risen Ghoul) | -- |
| spell_scripts | **none in 7f3d43b** | -- | 93 → 0 (deleted by updates) | -- | obsolete |

`serverside_spell` creates real `SpellInfo` objects (all 82 columns of the
SpellInfo constructor are populated; `SpellInfo::Id` values 19 … 1,217,758,
98% below 100,000) with effects (`APPLY_AURA` 1,076, `DUMMY` 488, `SUMMON_OBJECT_WILD`
312, `ENERGIZE` 303, `SCRIPT_EFFECT` 263; auras `PERIODIC_TRIGGER_SPELL` 239,
`MOD_INVISIBILITY_DETECT` 153, `DUMMY` 134, `PROC_TRIGGER_SPELL` 75). They are
encounter/quest machinery: 443 script references to server-only ids come from
boss and zone scripts (npc_arthas, boss_lady_vashj, zone_mardum, ...), none from
`scripts/Spells/spell_<class>.cpp`. Current player content does not depend on
them beyond the two trigger references above.

---

## 11. Corrections versus semantics

`SpellMgr::LoadSpellInfoCorrections` holds 191 `ApplySpellFix` blocks over 373
SpellIDs (all exist in the snapshot). Classified by the member each lambda writes
(`corrections.json`):

| class | blocks | player |
|---|---:|---:|
| source-correction (attributes, range, radius, duration, MaxAffectedTargets, mechanics, interrupt flags ...) | 119 | 2 |
| implementation-workaround (`Effect = NONE/DUMMY`, `TargetA/B` rewritten, `ApplyAuraName`) | 49 | 2 |
| missing-client-fact (`TriggerSpell` supplied, `ApplyAuraPeriod` supplied, proc fields) | 15 | 0 |
| server-authored-policy (`AttributesCu`) | 1 | 0 |
| unknown member (`SpellClassMask |=`, `NegativeEffects = true`) | 16 | 3 |

175 writes are literal data patches; 45 call code or read other members. Player-
reachable fixes: MaxAffectedTargets 4 for 38310/53385, Fingers of Frost 44544
`SpellClassMask |= 0x20000`, Inescapable Torment 373427 `EFFECT_3 → DUMMY`,
Earthquake 61882 and Eradicate/Reap/Cull `NegativeEffects = true`, Sundering 197214
`TargetB` cleared, Burning Rush 111400 `AttributesEx4 |= AURA_IS_BUFF`. The
data-patch writes (field = constant) are the shape a future era/hotfix JSON patch
would carry; the `NegativeEffects` / `AttributesCu` / target-info rewrites are
runtime policy. Nothing is applied here.

---

## 12. Conditions

Spell-keyed condition sources and their consumers: 13 `SPELL_IMPLICIT_TARGET`
(SourceGroup = effect mask, attached to `SpellEffectInfo::ImplicitTargetConditions`,
evaluated per candidate in `WorldObjectSpellTargetCheck`, Spell.cpp:9395); 17 `SPELL`
(`Spell::CheckCast`, :5920, caster = target0, explicit target = target1;
failure → `ErrorType` or `CASTER_AURASTATE`/`BAD_TARGETS`); 24 `SPELL_PROC`
(`Aura::CanProc`, SpellAuras.cpp:1903); 18 spellclick, 21 vehicle, 35 skill-line.
List semantics (`ConditionMgr::IsObjectMeetToConditionList`, :1021): rows grouped
by `ElseGroup`, AND within a group, OR across groups, `NegativeCondition` inverts,
`ConditionTarget` selects the object, negative `SourceTypeOrReferenceId` rows are
references.

Population: 1,801 spells with implicit-target rows (2,970 of 3,282 rows are
`OBJECT_ENTRY_GUID_LEGACY` creature filters), 339 with cast conditions, 2 with proc
conditions. In player scope: **one** row, Shadowstep 36554, source 17,
`UNIT_STATE 0x400 (UNIT_STATE_ROOT)` negated with error 103. Evaluators are
implemented for exactly the types that Trinity decides from unit facts and that
occur on spell sources (AURA, CLASS, UNIT_STATE, SPELL, LEVEL, OBJECT_ENTRY_GUID,
TYPE_MASK, RELATION_TO, DISTANCE_TO, ALIVE, HP_VAL, HP_PCT, CREATURE_TYPE,
STRING_ID, LABEL, NONE); everything else raises `FailClosed`. ConditionMgr was
not ported wholesale: player content does not justify it.

---

## 13. Targeting adapters

31 executing target-select hooks on 21 player spells (22 scripts). Policies seen:
replace the object with `nullptr` to suppress an effect for the caster
(`spell_pri_translucent_image`, `spell_dru_inner_peace`: 13 "inspect-only" hooks are
this pattern), `remove_if` filters (`Trinity::UnitAuraCheck`, primary-target
exclusion, friend/foe), `resize`/`RandomResize` caps (3 random), `clear +
push_back` replacements (Storm Bolt, Curious Bramblepatch), destination edits.
Scripts whose *only* executing hooks are target adapters: 10 spells. Party/raid
fan-out and lowest-health policies appear in all-content scripts but not in the
player-scope set; role/spec filters appear only as `Load()` gates
(`GetPrimarySpecialization`, 2 bindings). `DoCheckAreaTarget` area-aura predicates: 0
in player scope (17 all-content).

---

## 14. Amount / value adapters

164 executing hooks compute a scalar for an ordinary action (92 `amount-adapter`
+ 72 `cast-child-with-amount`). Scalar sources in player scope: talent aura amount
(59), damage copy (13), effect value (13), pct math on the hit amount (8),
percent of health (6), heal copy (4), power (1), combo points (1), stat/ticks
(1 each). Rounding and snapshot timing, from the consumers:

* `CalculatePct(base, pct) = T(base * float(pct) / 100.0f)` (Util.h:72): binary32
  arithmetic, truncation toward zero for integer `T`; `AddPct`/`ApplyPct` reuse it
  (probe-verified in `test_dummy_oracles.py`).
* `GetHitDamage()` in `OnHit`/`AfterHit`/`OnEffectHitTarget` is the post-bonus,
  pre-mitigation `m_damage` of the current target (Spell::TargetInfo::PreprocessTarget
  resets it per target); `AfterHit` sees the value after absorbs were applied to
  the dealt damage (`DoDamageAndTriggers`).
* `eventInfo.GetDamageInfo()->GetDamage()` / `GetHealInfo()->GetHeal()` in proc hooks
  are the amounts after mitigation/absorb (`Unit::DealDamage`/`HealBySpell` build them).
* `aurEff->GetAmount()` is the stack-multiplied, spell-mod-applied amount computed
  at `CalculateAmount` (apply/refresh time), not re-read on each proc unless the
  aura is recalculated.
* `CalcValue(caster)` inside scripts re-evaluates the effect's BasePoints with the
  caster's spell mods at call time.

The final boundary is `computed scalar → SPELLVALUE_BASE_POINTn of an ordinary
child spell` in 72 hooks and `computed scalar → pctMod/flatMod/damage of the
current ordinary pipeline` in 92 hooks: 164 of 164.

---

## 15. Per-spec and gear census

Status precedence per owner: script-family → script-state-only → marker-only →
engine-hardcoded → world-data → build-skew → unresolved (`specs.json`).

| spec | reachable spells | owners with population effects | scripted | marker-only | engine/world | build-skew | unresolved |
|---|---:|---:|---:|---:|---:|---:|---:|
| Death Knight / Blood | 337 | 201 | 10 | 13 | 0 | 55 | 123 |
| Death Knight / Frost | 348 | 204 | 7 | 8 | 0 | 55 | 134 |
| Death Knight / Unholy | 338 | 206 | 7 | 7 | 0 | 55 | 137 |
| Demon Hunter / Devourer | 331 | 186 | 10 | 8 | 0 | 55 | 113 |
| Demon Hunter / Havoc | 364 | 215 | 20 | 13 | 0 | 55 | 127 |
| Demon Hunter / Vengeance | 344 | 204 | 15 | 12 | 0 | 55 | 122 |
| Druid / Balance | 358 | 196 | 14 | 0 | 0 | 56 | 126 |
| Druid / Feral | 366 | 197 | 10 | 1 | 0 | 55 | 131 |
| Druid / Guardian | 360 | 200 | 14 | 4 | 0 | 55 | 127 |
| Druid / Restoration | 356 | 203 | 12 | 4 | 0 | 55 | 132 |
| Evoker / Augmentation | 296 | 179 | 3 | 2 | 0 | 55 | 119 |
| Evoker / Devastation | 322 | 189 | 4 | 4 | 0 | 55 | 126 |
| Evoker / Preservation | 330 | 198 | 3 | 2 | 0 | 55 | 138 |
| Hunter / Beast Mastery | 320 | 192 | 5 | 5 | 0 | 55 | 127 |
| Hunter / Marksmanship | 326 | 191 | 12 | 5 | 0 | 55 | 119 |
| Hunter / Survival | 321 | 184 | 3 | 3 | 0 | 55 | 123 |
| Mage / Arcane | 321 | 193 | 6 | 4 | 0 | 58 | 125 |
| Mage / Fire | 320 | 187 | 12 | 4 | 0 | 55 | 116 |
| Mage / Frost | 324 | 193 | 7 | 0 | 0 | 55 | 131 |
| Monk / Brewmaster | 411 | 239 | 4 | 3 | 0 | 55 | 177 |
| Monk / Mistweaver | 411 | 251 | 4 | 6 | 0 | 55 | 186 |
| Monk / Windwalker | 430 | 245 | 3 | 5 | 0 | 55 | 182 |
| Paladin / Holy | 384 | 244 | 12 | 5 | 0 | 55 | 172 |
| Paladin / Protection | 371 | 227 | 6 | 6 | 0 | 56 | 159 |
| Paladin / Retribution | 376 | 223 | 11 | 5 | 0 | 55 | 152 |
| Priest / Discipline | 333 | 191 | 21 | 15 | 0 | 56 | 99 |
| Priest / Holy | 332 | 189 | 19 | 10 | 0 | 55 | 105 |
| Priest / Shadow | 347 | 213 | 16 | 9 | 0 | 55 | 133 |
| Rogue / Assassination | 338 | 198 | 10 | 4 | 0 | 55 | 129 |
| Rogue / Outlaw | 329 | 182 | 10 | 4 | 0 | 55 | 113 |
| Rogue / Subtlety | 342 | 195 | 8 | 9 | 0 | 56 | 122 |
| Shaman / Elemental | 389 | 210 | 18 | 3 | 0 | 55 | 134 |
| Shaman / Enhancement | 388 | 226 | 26 | 8 | 0 | 55 | 137 |
| Shaman / Restoration | 374 | 216 | 13 | 4 | 0 | 56 | 143 |
| Warlock / Affliction | 376 | 215 | 9 | 3 | 0 | 57 | 146 |
| Warlock / Demonology | 384 | 226 | 3 | 3 | 0 | 56 | 164 |
| Warlock / Destruction | 379 | 219 | 9 | 5 | 0 | 57 | 148 |
| Warrior / Arms | 395 | 203 | 16 | 7 | 0 | 55 | 125 |
| Warrior / Fury | 416 | 200 | 19 | 14 | 0 | 58 | 109 |
| Warrior / Protection | 388 | 191 | 19 | 4 | 0 | 55 | 113 |

Gear (spells reachable from the acquisition kind):

| acquisition | root spells | reachable | owners with population effects | scripted | marker-only | build-skew | unresolved |
|---|---:|---:|---:|---:|---:|---:|---:|
| current-gear | 111 | 131 | 96 | 0 | 0 | 54 | 42 |
| current-set | 1 | 1 | 1 | 0 | 0 | 1 | 0 |
| current-gem | 5 | 6 | 5 | 0 | 0 | 0 | 5 |
| current-enchant | 19 | 33 | 4 | 0 | 0 | 0 | 4 |

The gear picture is the proc research's finding restated: 96 owners of population
effects, 54 newer than Trinity's supported build, 42 unresolved, 0 scripted. Gems
and enchants of the newest expansion are unresolved (5 + 4 owners).

---

## 16. Proc cross-reference

`proc-xref.json` re-reads each of the 758 current providers the proc census
left as inert-only (550), mixed (78) or script-bound (130), keyed by provider and
by `spell:effectIndex`:

| resolution | inert-only | mixed | script | total |
|---|---:|---:|---:|---:|
| unresolved-unbound (no script, no observer) | 524 | 67 | -- | 591 |
| build-skew (newer than 12.0.7.68453, no consumer) | 13 | 2 | -- | 15 |
| marker-only (observed, executes nothing) | 10 | 4 | -- | 14 |
| ordinary-trigger (script casts fixed children on proc) | 1 | -- | 48 | 49 |
| reusable-family (other families) | 1 | 3 | 34 | 38 |
| amount-adapter | -- | 1 | 34 | 35 |
| target-adapter | -- | -- | 1 | 1 |
| unresolved-bound-no-executing-hook (script exists, masks 0) | 1 | 1 | 11 | 13 |
| state-only-script | -- | -- | 2 | 2 |
| genuinely-unique | 0 | 0 | 0 | 0 |

The 13 drifted providers: Prayer of Mending 33076, Misdirection 34477, Rime 59057,
Obliteration 207256, Stormblast 319930, Dream of Cenarius 372119, Mental Decay
375994, Kingsbane 385627, Cycle of Binding 389718, Ashen Catalyst 390370, Inner
Focus 390693, Power Surge 453109, Acrobatic Strikes 455143. The 14 marker-only
providers include Avenging Wrath, Pillar of Frost, Water Shield, Havoc, Thunder
Focus Tea, Bone Shield, Obliteration 281238, Inner Demon, Void Metamorphosis.
"Generic HandleProc does nothing" is therefore *not* equated with gameplay-inert:
14 of the 550 are consumed as state, 3 are executed by scripts, 524 simply have
no Trinity implementation.

---

## 17. Server-semantic graph

`graph.json`: 28,610 nodes, 34,206 edges (kinds: db2-marker 11,669, family/emits
9,176, implemented-by 3,831, bound 3,812, condition 2,114, casts 1,955, queries 769,
removes 318, uses 226, engine 123, linked 211, pet-aura 2). 7,425 weakly connected
components; the largest (11,387 nodes) is held together by `SpellAuraRestrictions`
marker edges and the shared generic scripts. Hubs: `SetBonusValueForEffect`
helper (77 users, Mixology), `spell_gen_mixology_bonus` (77 spells),
`spell_gen_tournament_pennant` (36), `spell_warr_improved_whirlwind_cleave` (31),
`spell_gen_trigger_exclude_*_aura_spell` (22 + 18). 112 non-trivial SCCs (spell ↔
class cast/query cycles, e.g. aura scripts that re-cast their own owner). The
player subgraph from the 4,836 reachable spells has 2,057 nodes (954 spells, 527
classes, 521 scripts, 24 helpers), max depth 9, and reaches 351 child SpellIDs
that no authored edge reaches: the client data does not encode those
relationships at all.

---

## 18. Family oracles and probes

`dummy_semantics/oracles.py` declares actions from immutable policy + explicit facts:
`trigger_spell_with_value` (EffectTriggerSpell tail), `proc_trigger_spell`,
`periodic_trigger_spell`, `aura_linked` (LINKED/LINKED_2 apply/remove/reapply
stack sync), `linked_spell_actions` (all four `spell_linked_spell` consumers
including negative ids and the death-remove exception), `pet_aura`,
`spell_area_fits` (generic part; the 6 hardcoded Battlefield ids fail closed),
`calculate_pct`/`add_pct`/`apply_pct`, `compare_values`, `count_pct_from_max_hp`,
`forward_amount_as_basepoints`; `conditions.meets`/`meets_list` for the supported
condition types. `tools/tc_dummy_probe` extracts `CalculatePct`, `AddPct`, `ApplyPct`,
`RoundToInterval`, `CompareValues` and the three `EffectHook` mask functions
verbatim from the checkout, compiles them against a minimal SpellInfo shape and
emits 187 + 30 + 80 cases; the Python side reproduces every defined case
(undefined C++ conversions -- float→int32 overflow, uint64 of a negative float --
are excluded and documented in the test). Script families are too coupled to
`Unit`/`Spell` state to extract; they are covered by the synthetic classifier
tests and the witness cross-check.

---

## 19. Tests

`scripts/research/tests/test_dummy_*.py` (48 tests): oracle arithmetic and probe
differential; hypothesis properties for the two source-proven invariants
(`CalculatePct(b,100) == b` for exactly representable products;
`EFFECT_FIRST_FOUND` is the lowest bit of `EFFECT_ALL`); linked-spell, pet-aura,
spell_area, condition ElseGroup evaluators; fail-closed on unknown condition and
comparison types; the schema-driven replay (SET expressions, case-insensitive
columns, INSERT IGNORE/REPLACE, ALTER rejection); binding resolution (rank
expansion mirror, Holy Prism executes, Avatar never executes, unresolved names
reported, load gates); hook vocabulary equals the header; every family on
synthetic facts; witnesses agree with the index; population/scope nesting; marker
classification; proc cross-reference partition (1,195 / 758 / 550+78+130);
census partition; per-spec partition; reproducibility of the committed census
and population against a fresh run; provenance pinning (one Trinity commit across
all corpora, 0 unparsed statements, `spell_scripts` empty).

---

## 20. Shared infrastructure versus exceptions

Final census (`census.json`), current player scope. Owners = spells with a
population effect **or** a script binding (2,880; the 130 bound spells without a
Dummy-class effect are server-side behaviour on ordinary effects). One primary
bucket per owner; tags record overlaps (`tag_overlaps`).

| bucket | owners | population effects |
|---|---:|---:|
| generic-engine (data-decided consumer: DB2 marker read by CheckCast, spell_pet_auras, linked rows) | 32 | 63 |
| script-family (reusable script shapes other than the three below) | 229 | 263 |
| target-adapter | 11 | 6 |
| amount-adapter | 102 | 100 |
| proc-adapter (proc filter + ordinary trigger cast) | 43 | 60 |
| marker-state | 136 | 188 |
| world-data-policy | 3 | 5 |
| source-correction | 2 | 4 |
| **unique** | **0** | **0** |
| script-drift (bound, but no hook matches the 12.1 effect layout) | 38 | 27 |
| unresolved / build-skew | 69 | 108 |
| unresolved / no consumer at all | 2,215 | 3,501 |

Overlaps (`tag_overlaps`): 340 owners are script-only, 133 marker-only, 38
script-drift, 32 DB2-marker + marker, 31 script + marker, 6 script + DB2 marker +
marker, 4 script + correction, 2,284 carry no tag at all. Distinct reusable
primitives in use: **15 families, 92 named sub-kinds** (100 family/sub-kind keys
counting families without a sub-kind), explaining all 625 executing hooks.
Unresolved profile (2,284 owners incl. build skew): 1,828 passive, 1,801
class-trait roots, 200 spec-spell roots, 96 gear roots, 1,668 Dummy-aura-only
owners, 1,613 with a nonzero Dummy BasePoints (tooltip value holders), 705 proc
carriers, 76 periodic dummies, 42 trigger-without-trigger.

All-content comparison (174,143 owners): generic-engine 3,581, script-family 2,686,
amount 386, target 192, proc 130, marker 463, world-data 1,095, correction 55,
**unique 46**, script-drift 132, build-skew 4,995, unresolved 160,382; 17 families in use.

**Answer to the central question.** After normalising repeated consumers, the
server side of current player behaviour that Trinity implements is explained by
15 reusable primitives (with ≈ 90 parameterisations of scalar source and cast
target) plus three data-driven generic consumers (`SpellAuraRestrictions` marker
reads, `spell_linked_spell`, `EffectTriggerSpell`-with-value). The truly unique
tail among implemented current content is empty at hook granularity; spell
specificity lives in parameters (child ids, id tables, percentages, gating auras)
and in a handful of runtime-state idioms (§24). The dominant fact is not
uniqueness but absence: 2,284 of 2,880 owners (79%) have no server-side consumer
in this Trinity revision (a further 38 have only a drifted script), and nothing in
this evidence base says what they do.

---

## 21. Fail-closed inventory

* 2,215 unresolved + 69 build-skew owners: no semantics inferred.
* 74 registered-but-never-executing hooks (effect-layout drift) reported per binding;
  38 owners have no other consumer and are bucketed `script-drift`.
* 6 unresolved ScriptNames, 223 all-content.
* Condition types without an evaluator (NEAR_CREATURE, QUEST*, AREAID, ACTIVE_EVENT,
  INSTANCE_INFO, ...) raise `FailClosed`; none occurs in player scope.
* `TargetHook::CheckEffect` selection-category refinement is not ported: target-select
  masks are computed on TargetA/TargetB equality and flagged `target-hook-approximate`.
* `spell_area` rows for the 6 Battlefield ids, `SpellArea` autocast side effects,
  and `Spell::EffectDummy` pet auras are declared, never executed.
* Unnamed aura types (`SPELL_AURA_531`, `_320`, `_380`, `_493`) and effect
  `SPELL_EFFECT_324` are counted as unimplemented, not interpreted.
* No universal Dummy callback exists or is proposed.

---

## 22. DBC transition

Tables/columns read (from the ledger, `sources.json`, 49 tables):
`SpellEffect` (21 cols: ID, SpellID, DifficultyID, EffectIndex, Effect, EffectAura,
EffectTriggerSpell, EffectBasePointsF, EffectMiscValue_0/1, EffectSpellClassMask_0-3,
ImplicitTarget_0/1, EffectAuraPeriod, EffectMechanic, EffectAmplitude, EffectAttributes,
ScalingClass), `SpellName`, `SpellMisc` (Attributes_0-16, SchoolMask, DurationIndex),
`SpellAuraOptions`, `SpellCategories`, `SpellCooldowns`, `SpellClassOptions`,
`SpellEquippedItems`, `SpellLabel`, `SpellDuration`, `SpellProcsPerMinute[Mod]`,
`SpellAuraRestrictions` (Caster/Target/Exclude*AuraSpell), `SpellLearnSpell`,
`OverrideSpellData`, `SpellPower[Difficulty]`, key-only rows of `SpellAuraRestrictions`,
`SpellInterrupts`, `SpellLevels`, `SpellTargetRestrictions`, `SpellXSpellVisual`,
`SpellCastingRequirements`, `SpellReagents[Currency]`, `SpellScaling`, `SpellShapeshift`,
`SpellTotems`; `Difficulty`; `SkillLineAbility` (Spell, SupercedesSpell, SkillLine,
ClassMask), `SkillRaceClassInfo`, `SkillLine`; `TraitTreeLoadout`, `TraitNode`,
`TraitNodeEntry`, `TraitDefinition`, `TraitNodeXTraitNodeEntry`, `TraitCond`,
`TraitNodeXTraitCond`, `TraitNodeGroupXTraitCond`, `TraitNodeGroupXTraitNode`,
`SpecSetMember`, `ChrSpecialization`, `SpecializationSpells`, `ChrClasses`;
`ItemEffect`, `ItemXItemEffect`, `ItemSparse` (ItemSet, ExpansionID, Gem_properties),
`ItemSetSpell`, `SpellItemEnchantment`, `GemProperties`. Wago column order and
formatting are not semantics: readers are header-named and coerce empty cells to
zero (`gearing.tables.coerce`).

---

## 23. Reproducibility

| item | value |
|---|---|
| data snapshot | Wago `12.1.0.69497` (`changes/metadata/12.1.0.69497.json`) |
| wowlab-data commit (base of this work) | `676291db7e582f5520763e378cdcbb0fdbf51885` |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (2026-07-14), client builds ≤ 12.0.7.68453 |
| world DB | `TDB_full_world_1200.26021_2026_02_06.sql`, sha256 `54ddf4c12d6034a3c61e9b5683c2578de82d826d9090041a5e0477a164f5172d`, release TDB1200.26021 archive sha256 `48f0e2af7620ca70ec7054ff19d356255bc50e11d7829932015f28918f195588`; 522 updates replayed, 0 unparsed |
| build-skew reference | `SpellName.csv` at commit `63326dd` (snapshot 12.0.7.68367): 9,650 added / 54 removed ids |
| toolchain | Python 3.12.3; `uv 0.12.4`; `tree-sitter==0.25.2`, `tree-sitter-cpp==0.23.4` (index only); `pytest==8.4.2`, `hypothesis==6.140.3`; `g++ 13.3.0 -std=c++20 -O0 -ffp-contract=off` |
| corpora | `dummy-corpora/*.json` (hashes in `sources.json` for DB2 tables; corpus hashes below) |

Direct consumer coordinates (TrinityCore `7f3d43b`):

```
src/server/game/Spells/SpellEffects.cpp        :90 SpellEffectHandlers[]  :450 EffectNULL  :571 EffectDummy
                                               :590 EffectTriggerSpell  :721 EffectTriggerMissileSpell  :787 EffectForceCast
                                               :3069 EffectScriptEffect  :5410 EffectCreateAreaTrigger
src/server/game/Spells/Auras/SpellAuraEffects.cpp :70 AuraEffectHandler[]  :843 CalculateAmount script hook
                                               :1134 HandleEffect (apply/remove hooks, default prevention)
                                               :1301 PeriodicTick  :1365 CheckEffectProc  :1460 HandleProc
                                               :4861 HandleAuraDummy  :5324 HandleAuraLinked  :5470 HandleAuraOverrideSpells
                                               :5583/:5607 periodic trigger ticks  :6155/:6180 proc trigger casts
src/server/game/Spells/Auras/SpellAuras.cpp    :1375 HandleAuraSpecificMods (spell_area, spell_linked_spell, family switch)
                                               :1630 CheckAreaTarget  :1795 PrepareProcToTrigger  :1903/:1907 CanProc+CheckProc
                                               :2014/:2027 Proc/AfterProc  :2057 LoadScripts  :2067-2495 CallScript*
src/server/game/Spells/Spell.cpp               :1050-2112 target-select hooks  :2749 PreprocessTarget  :2796 DoTargetSpellHit
                                               :2825 DoDamageAndTriggers  :3123 PreprocessSpellHit  :3226 DoSpellEffectHit
                                               :3353 spell_linked_spell HIT  :3527/:3562/:3738/:3840/:3947 cast hooks
                                               :3949 spell_linked_spell CAST  :3994 handle_immediate  :4189/:4209 phases
                                               :5713 HandleEffects  :5920 conditions(17)  :6116 CheckCast hook  :8775-9040 CallScript*
                                               :9395 implicit-target conditions
src/server/game/Spells/SpellScript.cpp         :27 _Validate  :120 GetAffectedEffectsMask  :143 IsEffectAffected
                                               :184 SpellScript::EffectBase::CheckEffect  :200 TargetHook::CheckEffect
                                               :276 SpellScript::_Validate  :355-395 hook-state predicates
                                               :694/:705 PreventHitEffect/PreventHitDefaultEffect  :827 AuraScript::_Validate
                                               :943 AuraScript::EffectBase::CheckEffect  :998/:1017 default-action prevention
src/server/game/Spells/SpellScript.h           :268 SpellScriptHookType  :973 AuraScriptHookType  HookList<> members
src/server/game/Spells/SpellMgr.cpp            :1965 LoadSpellPetAuras  :2074 LoadSpellLinked  :2296 LoadSpellAreas
                                               :2735 LoadSpellInfoServerside  :2981 LoadSpellInfoCustomAttributes
                                               :3363/:3379 ApplySpellFix/ApplySpellEffectFix  :3390 LoadSpellInfoCorrections
                                               :730 SpellArea::IsFitToRequirements
src/server/game/Spells/SpellInfo.cpp           :2222 CheckLocation spell_area  :2888-2954 _LoadSpellSpecific ids
src/server/game/Globals/ObjectMgr.cpp          :5965 LoadSpellScriptNames  ValidateSpellScripts
src/server/game/Scripting/ScriptMgr.cpp        :1425 CreateSpellOrAuraScripts  ScriptMgr.h:1351-1428 Register* macros
src/server/game/Conditions/ConditionMgr.cpp    :193 Condition::Meets  :1021 IsObjectMeetToConditionList  :1150 NotGrouped
src/server/game/Entities/Pet/Pet.cpp           :1730 CastPetAuras  :1749 CastPetAura   Player.cpp :22179 AddPetAura
src/common/Utilities/Util.h                    :72 CalculatePct  :85 AddPct  :91 ApplyPct  CompareValues
```

Commands:

```bash
# from the wowlab-data root
python3 scripts/research/tools/tdb_server_overlay.py --tdb ../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql   # ~2 min
python3 scripts/research/tools/tc_dispatch_tables.py
python3 scripts/research/tools/gen_build_skew.py
cd scripts/research
uv run --with tree-sitter==0.25.2 --with tree-sitter-cpp==0.23.4 python tools/tc_script_index.py                  # ~6 s
python3 dummy_semantics.py all                     # writes every corpus into docs/research/dummy-corpora (~3 min)
python3 dummy_semantics.py census | head -80
python3 dummy_semantics.py spell 53651             # everything about one SpellID
python3 dummy_semantics.py procxref | jq .summary
make -C tools/tc_dummy_probe && uv run --with pytest==8.4.2 --with hypothesis==6.140.3 python -m pytest tests/test_dummy_*.py -q
```

Witness ids: 686, 348, 774, 596, 32175, 47515, 53651, 8092, 1784, 114165, 54149,
172, 2050, 14914, 86949, 168534, 1943, 34026, 19574 (§8, `witnesses.json`).

Corpus hashes (sha256):

| corpus | sha256 |
|---|---|
| `bindings.json` | `49db03fa252a15c64942e9e97bb0d76b8fe78ee0481496c82dfa5b55d9f0b3a4` |
| `build-skew.json` | `de1bab805980b54c99459421889fa52224cd709f962cf07402bdd26ebb8b87e5` |
| `census.json` | `c8c2e66e1ed537ff45184f629551e2e7ff34d1912234440fd99a6f0371258454` |
| `conditions.json` | `33a380e341704fd99a8b4c6054b9bbfe82e2dbfef89e9cbfc891fb41d80e2e15` |
| `corrections.json` | `ae74c2494dd1d55cc931271e549fd510068070d58cde18169d994299a3390f2d` |
| `dispatch-tables.json` | `a68ee2522ecaa2262464490474fbbac21c94212342b30c149ce6c6d9b1b472f8` |
| `families.json` | `88c418fd0678922db7f4cbdaa633e141e51cdac4f65297d987d6425b24f530af` |
| `graph.json` | `c5ae81f473829b48faee70e781ee60b61b8bfbb9df5dfe9fe6adabb7ce2fe29e` |
| `hardcoded.json` | `693d5db9fe43fc89f2e86ce97cdc1d10cd2553e38e58b0eff2765cb6effb31f4` |
| `hooks.json` | `7bde384e0d84758454fbfb2cced4e7ff8f23046d37c6e3b01c6c0329c604baf4` |
| `markers.json` | `fe72f2f79dd34403ee4cdd580283d09b673570bd2fc9290a996d1f5d5cb77a41` |
| `population.json` | `1a492f4d102ea52f1405998d4c7dac483f1c435f1c9beb7046746ad51d6f0c1f` |
| `proc-xref.json` | `db738ac256f3fd48652baf9fc7d465fb36307df1e03218e39c89fe442a88e91c` |
| `script-index.json` | `67b7e7c323bc9eecee73621263fe3d811fadd076093362f1b1b0c116a45ad8c7` |
| `sources.json` | `dcf46b3cc79a25549d8b6640e87c37ac5ec7dbc44bdd91b19a5379f74387ad3c` |
| `specs.json` | `6102429c5cab76f0844c9bc762fdfa2fc14f989825deda0cab8e85f7970f2655` |
| `trinity-server-overlay.json` | `dd864b641392d1aa113f1e41be1540acc8199b114802f5c408aa9290ed74aaf7` |
| `witnesses.json` | `46bf300c4401504b22632e50e0ab3b47df309538a14584090cef3160c5a87167` |

External documentation, Wowhead, SimC and historical knowledge were not used as
semantic authority; the sibling `simc` checkout was not consulted.

---

## 24. Final report

### Server-side semantic layers

Missing behaviour is supplied, in order of weight for current players, by (1) the
`spell_script_names` → SpellScript/AuraScript hook system (523 bindings, 625 executing
hooks), (2) client-data marker reads that need no code (`SpellAuraRestrictions`
gates, 15 Dummy owners), (3) six `spell_linked_spell` rows, 7 `spell_group`, 10
`spell_threat`, 2 `spell_custom_attr`, 1 condition row, (4) 7 `LoadSpellInfoCorrections`
blocks, (5) 2 dead hardcoded engine branches. `serverside_spell`, `spell_area`,
`spell_target_position`, `spell_pet_auras`, `areatrigger` scripts and pet AI are
encounter/legacy machinery with a 2-effect / 21-areatrigger / 3-creature footprint
in player scope.

### Reusable semantic families

Fifteen structural families explain every executing hook in player scope (§5
table). By hooks: cast-child 204, amount-adapter 92, proc-filter-adapter 77,
cast-child-with-amount 72, linked-aura-mutation 39, target-adapter 31,
cooldown-mutation 21, choose-among-children 17, random-child 15, suppress-default 8,
cast-gate 7, delayed-child 4, pet-owner-forward-cast 4, resource-mutation 4,
consume-and-cast 2; plus 28 state-only bookkeeping hooks. Owners: 385 spells
(272 with a Dummy-class effect, 113 on ordinary effects).

### Marker/state families

157 Dummy-aura owners are meaningful without executing anything: 137 are read by
other scripts (`HasAura`/`GetAuraEffect(id, EFFECT_n)->GetAmount()`), 15 by client
`SpellAuraRestrictions`, 9 by engine cases. 132 bindings are `Load()`-gated on a
talent aura. Their meaningful content is existence + `GetAmount()` of a specific
effect index; stacks and durations are ordinary.

### Script/helper infrastructure

Shared machinery: the hook system (52 hook types, 59 call sites), 95 free helpers
(one hub: `SetBonusValueForEffect`, 77 users), same-class helper methods (merged
in 3,812 bindings), nested `BasicEvent` classes for delays, constructor-parameterised
scripts (13 player bindings), DB2-parameterised generic scripts (40 player
bindings). Fanout: 127 spells carry more than one script; the widest player scripts
bind 31 / 22 / 18 spells.

### Proc cross-reference

550 inert-only → 524 no consumer, 13 build skew, 10 marker-only, 3 scripted (1
trigger, 1 family, 1 drifted). 78 mixed → 67 no consumer, 4 marker-only, 4 scripted,
2 build skew, 1 drifted. 130 script-bound → 48 ordinary triggers, 34 reusable
families, 34 amount adapters, 1 target adapter, 2 state-only, 11 drifted. Zero
genuinely unique.

### Genuine unique tail

At hook granularity: none in player scope; 48 legacy hooks (46 spells) in all
content. The specific residue inside families: 1 hardcoded id table (Elemental
Overload), 1 creature-entry list (Inescapable Torment), cross-script state reads
(Stormblast), 51 scripts with member state, and 3 drifted/dead engine branches.

### Runtime facts and mutable state

Explicit inputs the families read: caster / hit unit / aura target / proc actor and
action target identity; `ProcEventInfo` (proc spell, `SpellInfo`, damage/heal info
amounts, `m_appliedMods` of the proc spell); caster aura amounts by (spell, effect
index); health and power values and percentages; explicit target reaction and
facing; remove mode; remaining/max duration; combo points and power costs; the
caster's controlled summons; GUID-captured targets for scheduled work. Mutable
state introduced by scripts: 51 player scripts keep members (GUID lists, primary
target, flags, cached percentages); 4 schedule events; 1 mutates a summon timer;
cross-script state exists (`GetScript<T>()`).

### RNG and ordering boundaries

Directly proved: `roll_chance(aurEff->GetAmount())` in 23 proc filters and 15
random-child hooks is drawn inside the script, at hook time -- for `DoCheckEffectProc`
before the generic `CalcProcChance` roll (which then rolls at 101%), for `OnEffectHit`
before unit targets are processed, for `OnEffectHitTarget` per target. `RandomResize`
in 3 target adapters draws during target selection. Ordering: target hooks →
OnCast → launch effects → per-target BeforeHit/OnHit → aura creation → HIT_TARGET
effects → damage/heal (calc hooks) → aura apply → procs → linked HIT → AfterHit →
AfterCast → linked CAST (§4).

### Ordinary-action boundary

316 of 625 hooks (51%) end in `CastSpell` of an authored SpellID; 164 (26%) end in
a scalar handed to an ordinary damage/heal/absorb/duration pipeline or to
BasePoints; 39 end in an ordinary aura mutation, 21 in a cooldown mutation, 4 in a
power change, 31 in a target list; 77 are predicates; 8 suppress a default; 28
only keep state. Everything Trinity does for current players ultimately emits
ordinary SpellIDs, aura operations or scalars.

### Current-player census

2,880 owners (2,750 with a population effect + 130 script-bound spells on
ordinary effects): the 15 families and 3 generic data consumers cover 422 owners
(15%); 136 are marker-only (5%); 38 have only a drifted script (1%); 2,284 (79%)
have no consumer, 69 of them provably newer than the supported build. Effects:
4,325 population effects, 3,609 unresolved + 27 drifted.

### Open questions

1. **What implements the 2,236 unresolved owners?** Nothing in Trinity `7f3d43b`.
   Reopen per spell with a direct consumer (a newer Trinity, another server, or a
   client-side consumer). Most are passive talents whose Dummy `BasePoints` is a
   value read by nobody server-side.
2. **Effect-layout drift.** 74 player hooks (13 proc providers) match no 12.1
   effect. Reopen when Trinity supports ≥ 12.1; until then their scripts are
   evidence of intent, not behaviour.
3. **Unnamed aura types 320/380/493/531 and effect 324** in player scope: no
   consumer; reopen with a handler.
4. **`TargetHook::CheckEffect` refinement** (selection category × object type) not
   ported; reopen if a target-adapter mask decision depends on it.
5. **The 6 unresolved ScriptNames** (database rows without registrations): reopen
   on the next TDB/Trinity bump.
6. **`serverside_spell` id collisions** with 12.1 `SpellName` (10 ids): Trinity
   would reject those rows on a 12.1 store; reopen when the DB targets 12.1.
7. **Script member state and cross-script reads** are recorded, not modelled;
   reopen when a family oracle needs them (Atonement target list, Stormblast
   original-cast gate).
8. **`SpellAuraRestrictions`-driven markers** are client facts consumed by
   `Spell::CheckCast`; whether current clients gate more markers than the 15
   found here is not decidable from this snapshot alone.
