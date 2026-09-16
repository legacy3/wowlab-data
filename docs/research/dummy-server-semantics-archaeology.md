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
   are `SPELL_AURA_DUMMY` on 2,069 owners. For 2,217 owners (81% of the population
   owners) no server-side layer at all names the spell: no script, no observer, no
   world-DB row, no hardcoded case. A further 69 owners are newer than the last
   client build Trinity supports and 35 carry a script whose hooks no longer match
   the 12.1 effect layout. These are *unimplemented in this Trinity revision*, not
   *unique* and not *inert in Retail* (§20, §24).
2. **Everything Trinity does implement for current players reduces, structurally,
   to 15 code-shape families plus a 4-hook residue.** 389 reachable spells carry 633
   executing script hooks. 629 fall into one of 15 code shapes (`cast-child`,
   `cast-child-with-amount`, `amount-adapter`, `proc-filter-adapter`,
   `linked-aura-mutation`, `target-adapter`, `cooldown-mutation`,
   `choose-among-children`, `random-child`, `suppress-default`, `cast-gate`,
   `delayed-child`, `pet-owner-forward-cast`, `resource-mutation`, `consume-and-cast`)
   or the `state-only` bookkeeping shape; 4 hooks on 4 spells (Demonic Circle
   teleport, Alter Time, Divine Image, Dancing Rune Weapon) move units or deal
   damage outside the spell system and are classified **genuinely unique** (§5,
   §6). A family label is a *primary* code shape: 144 of the 633 hooks perform
   more than one action kind and 81 perform an action outside their family's
   declared set (all recorded per hook, §5). Structural equivalence is **not**
   proof of semantic equivalence: the families say which engine actions a hook
   emits, not that one production primitive would reproduce every member.
3. **The ordinary-action boundary is almost universal.** Counting overlapping
   action kinds over the 633 hooks: 326 call `CastSpell` on an authored child
   SpellID, 84 write an amount (BasePoints or the current hit/absorb pipeline),
   66 mutate another aura, 38 touch cooldowns, 8 touch power, 15 edit a target
   list, 29 prevent a default, 47 roll RNG, 8 schedule work, 3 teleport, 1 deals
   damage directly (§14, §24 "Ordinary-action boundary").
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
6. **Marker/state auras are real but small.** 152 of the 2,069 Dummy-aura owners
   are observed by a strong consumer (139 by script `HasAura/GetAuraEffect`
   queries: 76 as a `Load()` gate, 59 as an amount read, 35 as a presence read, 4
   as a proc gate; 15 by `SpellAuraRestrictions.*AuraSpell` in the client data
   itself; 1 by a gameplay engine case; 8 more only by load-time classification
   sites, counted as weak). 134 of the 523 bindings gate the entire script in
   `Load()` on `HasAura(<talent>)`: the talent Dummy aura is a boolean marker (§9).
7. **The proc census resolves as follows.** Of the 550 inert-only, 78 mixed and
   130 script-bound current providers: 592 have no consumer at all, 15 are build
   skew, 13 are marker-only, 48 are ordinary triggers supplied by code, 37 fall in
   other reusable families, 36 are amount adapters, 1 is a target adapter, 12 are
   bound to a script whose hooks no longer match the 12.1 effect layout, 2 are
   state-only scripts and 2 (Dancing Rune Weapon, Divine Image) are genuinely
   unique (§16). The buckets are a strict partition of the 758.

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
| bindings whose ScriptName has no registration | 17 | 0 |
| SpellScript / AuraScript / both | 2,213 / 1,520 / 62 | 284 / 222 / 17 |
| hooks registered / never executing (mask 0) / executing | 4,937 / 261 / 4,626 | 725 / 74 / 633 |
| bindings that call PreventDefaultAction / PreventHitDefaultEffect | 465 | 37 |
| bindings with constructor arguments | 136 | 13 |
| bindings with mutable script fields | 422 | 52 |
| bindings gated in `Load()` (HasAura 105, HasAuraEffect 12, HasSpell 7, ...) | -- | 134 |

Class resolution follows C++ inheritance: a script deriving from a same-file
helper base (`spell_dru_berserk : spell_dru_base_transformer : SpellScript`)
inherits the base's `Register()` hooks, methods and fields. The 17 unresolved
all-content names (Kael'thas, Sindragosa, Sunwell necks, Lich King, Dalaran
sewers, ...) have world-DB rows but no registration in this checkout.

The 74 never-executing player hooks are **effect-layout drift** between the data
Trinity's authors saw (≤ 12.0.7) and the 12.1 snapshot. Witness: `spell_warr_avatar`
registers `OnEffectHitTarget(EFFECT_5, SPELL_EFFECT_SCRIPT_EFFECT)` for Avatar 107574; in
the snapshot effect 5 is `MOD_AOE_DAMAGE_AVOIDANCE` and no SCRIPT_EFFECT exists.
12 proc providers are affected (§16).

Hook-list usage in player scope (executing): `OnEffectProc` 95, `OnEffectHitTarget` 92,
`AfterCast` 51, `DoCheckEffectProc` 44, `CalcDamage` 36, `DoCheckProc` 33,
`OnEffectPeriodic` 31, `AfterEffectRemove` 26, `AfterHit` 25, `OnObjectTargetSelect` 20,
`DoEffectCalcAmount` 18, `OnProc` 16, `AfterEffectApply` 14, `OnEffectHit` 14,
`OnEffectLaunch(Target)` 13+13, `OnHit` 11, `OnObjectAreaTargetSelect` 10,
`CalcHealing` 9, `OnCast` 8, `OnCalcCritChance` 7, `OnCheckCast` 7, then ≤ 6 each
(`AfterCast` 52, `OnEffectHitTarget` 93 and `CalcDamage` 38 after inheritance).

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
a **primary** family from its code shape with a fixed precedence (hook role first,
then the action it performs). Every family below was **verified by reading the
witness code** (coordinates in `witnesses.py`; structural cross-check in
`test_witnesses_agree_with_structural_index`). Counts are current player scope.

Two limits are stated up front. (1) A family is a code shape, not a proof of
semantic equivalence: two `cast-child` hooks may differ in trigger flags,
original-caster handling, gating conditions or the value they copy, and a
production primitive would have to carry every such difference as an explicit
parameter. The corpora keep the full parameterisation per hook (child ids, scalar
source, cast target, trigger-flag tokens, gating aura ids, RNG, delay, prevent).
(2) Hooks do more than one thing: 144 of the 633 perform more than one action kind
and 81 perform an action their primary family does not declare (`secondary_actions`
per hook; totals: aura 26, prevent 19, cooldown 13, target-ops 8, rng 5, power 5,
delay 4, cast 3, areatrigger 3, movement 3, pet-owner 2, direct-damage 1, summon 1).
Nothing a hook does is dropped by the label; the label only orders it.

| family (proved) | hooks | spells | scripts | source facts / consumer | immutable inputs | explicit runtime inputs | mutable state | RNG | output action | negative near-match |
|---|---:|---:|---:|---|---|---|---|---|---|---|
| cast-child | 207 | 174 | 174 | `Unit::CastSpell(target, id, args)` from Effect/Hit/Cast/Apply/Remove/Proc hooks | child SpellID (script constant or `SpellAuraRestrictions.*AuraSpell`), trigger flags | caster, hit unit / aura target / proc target | none | none | ordinary cast of an authored spell | `suppress-default` (cast replaced by nothing) |
| cast-child-with-amount | 73 | 67 | 65 | same + `CastSpellExtraArgs::AddSpellMod(SPELLVALUE_BASE_POINTn, x)` / `SpellValueOverrides` | child SpellID, effect index, percentage (talent amount) | damage/heal info amount, aura amount, health/power, AP/SP | none | none | ordinary cast with fixed BasePoints | `amount-adapter` (no cast, rewrites in place) |
| amount-adapter | 94 | 82 | 58 | `CalcDamage/CalcHealing/DoEffectCalcAmount/OnCalcCritChance/OnEffectAbsorb` writes, `SetHitDamage/SetHitHeal/SetEffectValue/SetSpellValue` | percentage from a talent aura amount or another spell's effect value | caster aura amounts, victim identity, health pct | occasionally a float member (`_pctMod`) | none | scalar into the ordinary damage/heal/absorb/duration pipeline | `state-only` (reads but writes nothing) |
| proc-filter-adapter | 77 | 64 | 64 | `DoCheckProc` / `DoCheckEffectProc` predicates before the roll | spell family masks, effect indices | proc spell info, `m_appliedMods`, damage/heal info, target auras | none | 23 hooks roll `roll_chance(aurEff->GetAmount())` (script RNG replaces the generic chance) | accept/reject proc | generic `ProcFlags`/`spell_proc` gating |
| linked-aura-mutation | 38 | 37 | 36 | `RemoveAurasDueToSpell`, `RefreshDuration`, `SetDuration`, `ModStackAmount`, `ChangeAmount` on another aura | linked SpellID | aura target / caster, remove mode | none | none | modify-aura | `spell_linked_spell` REMOVE rows (data-driven twin) |
| target-adapter | 31 | 21 | 22 | `OnObject{Area,}TargetSelect` / `OnDestinationTargetSelect` list edits | target type, effect index | candidate list, explicit target, caster auras | none | 3 hooks (RandomResize) | select-targets | `DoCheckAreaTarget` (predicate only) |
| cooldown-mutation | 25 | 21 | 19 | `SpellHistory::ModifyCooldown/ResetCooldown/RestoreCharge` | SpellID, amount from another spell's effect value | caster history, spec | none | none | modify-cooldown | `SPELL_AURA_MOD_SPELL_CATEGORY_COOLDOWN` (generic) |
| choose-among-children | 17 | 15 | 15 | ≥ 2 child casts selected by branch (`IsFriendlyTo`, switch on id) or all cast | child SpellIDs | reaction, proc spell id | none | none | one or several casts | `random-child` |
| random-child | 16 | 15 | 16 | `roll_chance(x)` guarding a cast | chance (talent amount) | caster aura amount | none | script roll | cast or nothing | generic proc chance |
| suppress-default | 10 | 6 | 5 | `PreventHitDefaultEffect`/`PreventDefaultAction` and nothing else | effect index, gating aura ids | caster auras | none | none | no-action | -- |
| cast-gate | 7 | 7 | 7 | `OnCheckCast` returning `SpellCastResult` | -- | explicit target reaction / facing / path | none | none | gate-cast | `conditions` source 17 |
| delayed-child | 4 | 4 | 4 | `m_Events.AddEventAtOffset(lambda/BasicEvent)` casting later | child SpellID, delay | GUID-captured targets | scheduled event | none | schedule + cast | `SPELL_EFFECT_TRIGGER_SPELL` `MiscValue` delay (generic) |
| pet-owner-forward-cast | 3 | 3 | 2 | cast issued by a controlled summon / owner | creature entries, child SpellID | `m_Controlled` list | summon timer | none | cast (other caster) | `spell_pet_auras` (generic twin, unused) |
| resource-mutation | 3 | 3 | 3 | `ModifyPower` | power type, percentage | duration ratio, power cost, proc spell | none | 1 hook rolls | modify-power | `SPELL_EFFECT_ENERGIZE` (generic) |
| consume-and-cast | 2 | 2 | 2 | own `DropCharge/Remove` then cast | child SpellID | aura target, damage info | none | none | modify-aura + cast | `linked-aura-mutation` |
| state-only (not a family) | 22 | 19 | 20 | bookkeeping: GUID lists, primary-target marks, cosmetic visuals | -- | hit unit identity | script fields | 2 | marker | -- |
| unclassified (genuinely unique) | 4 | 4 | 4 | `NearTeleportTo` (Demonic Circle 48020, Alter Time 342246, Divine Image 392988), `Unit::DealDamage` by a controlled summon (Dancing Rune Weapon 49028) | -- | position/health snapshots, summon list | Alter Time snapshot members | none | teleport / direct damage | no family declares these actions |

Sub-kinds (60 named in player scope, `families.json → sub_kinds`) pin the scalar
source and the cast target: `cast-child/{caster 69, aura-target 53, hit-unit 48,
aura-spell-field 11, proc-target 8, nullptr 7, other 6, destination 4}`,
`cast-child-with-amount/{aura-amount 25, damage-copy+aura-amount 10, computed 9,
heal-copy+aura-amount 4, effect-value 3, pct-of-health 2, ...}`,
`amount-adapter/{aura-amount 38, constant-or-other 9, effect-value 8, pct-math 4,
hit-amount:damage-copy 7, pct-of-health 6, ...}`.

Hidden helper behaviour was checked explicitly: same-class helper methods,
same-file free functions (including the three helper namespaces
`MajorPlayerHealingCooldownHelpers`, `DivineImageHelpers`, `HealingRain`),
static methods of same-file helper structs (`spell_dh_shattered_souls_base_lesser::CreateFragments`,
`spell_pri_holy_words_base`), nested `BasicEvent` classes and methods reached
through `GetScript<X>()` are merged into the calling handler before
classification (`ScriptIndex.merged_facts`). 11 player hooks read or write
another script's state through `GetScript<>` (Stormblast, Tricks of the Trade,
Healing Rain, Thorim's Invocation, Blade Dance / First Blood, Molten Thunder,
Mind Devourer, Divine Procession); 5 call static helpers of another class. Both
are recorded per hook (`cross_script`, `cross_class`).

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

Genuinely unique behaviour among *scripts* in player scope: 4 hooks on 4 spells.
Demonic Circle: Teleport 48020 teleports the caster to its summoned circle
gameobject and clears movement impairment; Alter Time 342246 snapshots health
and position on apply and restores both on expiry (plus a Blink charge reset);
Divine Image 392988 teleports the controlled image next to the priest before
empowering it; Dancing Rune Weapon 49028 deals half the proc damage directly
through the summon with `Unit::DealDamage` (Trinity's own comment: "port of the
old switch hack, it's not correct"). Position and direct-damage actions are
outside every family's declared action set and stay `unclassified`. Across all
content 96 hooks on 39 spells are unclassified, the rest legacy (Muisek Vessel
item family, Kel'Thuzad chains, Illidan/Akama, Headless Horseman, Oscillating
Field, ...). Spell-specific *parameterisation* inside otherwise reusable families
is the larger residue: hardcoded id tables
(`spell_sha_mastery_elemental_overload::GetTriggeredSpellId`, 5 pairs;
`DivineImageHelpers::GetSpellToCast`, 24 ids → 6 children), creature-entry lists
(`spell_pri_inescapable_torment`), cross-script state (11 hooks), and script
member state (52 player bindings: `_appliedAtonements` GUID list, `_procTarget`,
`_wasStealth`, `_pctMod`, Alter Time's `_health`/`_pos`, ...).

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
| no consumer | 2,217 player owners (e.g. talent 200390 Cultivation *aura* itself) | Dummy aura applied by the talent's passive cast → `HandleAuraDummy` REAL: no pet aura, no case → nothing; observed only as a value holder by another spell's script |
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
| `script-aura-query` (another script's `HasAura/GetAura/GetAuraEffect(<id>)`) | 139 |
| &nbsp;&nbsp; of which read as a `Load()` gate / amount read / presence read / proc gate | 76 / 59 / 35 / 4 |
| `script-validate` (named in some `ValidateSpellInfo`; navigation only) | 106 |
| `script-own-amount` (its own script reads `aurEff->GetAmount()`) | 59 |
| `db2-aura-restriction` (`SpellAuraRestrictions.{Caster,Target,Exclude*}AuraSpell` of another spell) | 15 |
| `engine-classification` (load-time `_LoadSpellSpecific` / DR / immunity sites; weak) | 8 |
| `engine-hardcoded` (gameplay-semantic engine branch) | 1 |
| `spell-linked`, `script-aura-remove`, `condition-aura` (any condition source) | 2, 2, 1 |

Strong observers are those that read the aura as gameplay state: script queries
in any role, client `SpellAuraRestrictions`, gameplay engine branches,
`spell_linked_spell` and `CONDITION_AURA` rows. A `ValidateSpellInfo` mention,
a `TraitDefinition` pointer and a load-time classification case are reference-only
and never make an owner "consumed". 152 of 2,069 owners have a strong observer;
1,794 have none and no script of their own; 41 of those are newer than Trinity's
supported build. The DB2 restriction observers are notable
because they are *client* facts: Stealth 1784, Blade Flurry 13877, Avenging Wrath
31884, Killing Spree 51690, Water Shield 52127, Vampiric Blood 55233, Thunder
Focus Tea 116680, Bone Shield 195181 gate other spells' `CheckCast` without any
server code.

Marker lifetime and stacks follow the ordinary aura (duration/stacks from
`SpellMisc.DurationIndex` / `SpellAuraOptions.CumulativeAura`); no marker in player
scope has its amount, stacks or duration mutated by a consumer other than its
own script (`linked-aura-mutation` edges in `graph.json`). 134 bindings use the
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

| spec | reachable spells | owners with population effects | scripted | unique | marker-only | engine/world | build-skew | unresolved |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Death Knight / Blood | 337 | 201 | 9 | 1 | 13 | 0 | 55 | 123 |
| Death Knight / Frost | 348 | 204 | 7 | 0 | 8 | 0 | 55 | 134 |
| Death Knight / Unholy | 338 | 206 | 7 | 0 | 7 | 0 | 55 | 137 |
| Demon Hunter / Devourer | 331 | 186 | 10 | 0 | 8 | 0 | 55 | 113 |
| Demon Hunter / Havoc | 364 | 215 | 20 | 0 | 13 | 0 | 55 | 127 |
| Demon Hunter / Vengeance | 344 | 204 | 15 | 0 | 12 | 0 | 55 | 122 |
| Druid / Balance | 358 | 196 | 15 | 0 | 0 | 0 | 56 | 125 |
| Druid / Feral | 366 | 197 | 11 | 0 | 1 | 0 | 55 | 130 |
| Druid / Guardian | 360 | 200 | 16 | 0 | 4 | 0 | 55 | 125 |
| Druid / Restoration | 356 | 203 | 13 | 0 | 4 | 0 | 55 | 131 |
| Evoker / Augmentation | 296 | 179 | 3 | 0 | 2 | 0 | 55 | 119 |
| Evoker / Devastation | 322 | 189 | 4 | 0 | 4 | 0 | 55 | 126 |
| Evoker / Preservation | 330 | 198 | 3 | 0 | 2 | 0 | 55 | 138 |
| Hunter / Beast Mastery | 320 | 192 | 5 | 0 | 5 | 0 | 55 | 127 |
| Hunter / Marksmanship | 326 | 191 | 12 | 0 | 5 | 0 | 55 | 119 |
| Hunter / Survival | 321 | 184 | 3 | 0 | 3 | 0 | 55 | 123 |
| Mage / Arcane | 321 | 193 | 6 | 0 | 4 | 0 | 58 | 125 |
| Mage / Fire | 320 | 187 | 12 | 0 | 4 | 0 | 55 | 116 |
| Mage / Frost | 324 | 193 | 7 | 0 | 0 | 0 | 55 | 131 |
| Monk / Brewmaster | 411 | 239 | 4 | 0 | 3 | 0 | 55 | 177 |
| Monk / Mistweaver | 411 | 251 | 4 | 0 | 6 | 0 | 55 | 186 |
| Monk / Windwalker | 430 | 245 | 3 | 0 | 5 | 0 | 55 | 182 |
| Paladin / Holy | 384 | 244 | 12 | 0 | 4 | 0 | 55 | 173 |
| Paladin / Protection | 371 | 227 | 6 | 0 | 5 | 0 | 56 | 160 |
| Paladin / Retribution | 376 | 223 | 11 | 0 | 4 | 0 | 55 | 153 |
| Priest / Discipline | 333 | 191 | 21 | 0 | 16 | 0 | 56 | 98 |
| Priest / Holy | 332 | 189 | 19 | 1 | 11 | 0 | 55 | 103 |
| Priest / Shadow | 347 | 213 | 16 | 0 | 8 | 0 | 55 | 134 |
| Rogue / Assassination | 338 | 198 | 10 | 0 | 4 | 0 | 55 | 129 |
| Rogue / Outlaw | 329 | 182 | 10 | 0 | 4 | 0 | 55 | 113 |
| Rogue / Subtlety | 342 | 195 | 8 | 0 | 9 | 0 | 56 | 122 |
| Shaman / Elemental | 389 | 210 | 18 | 0 | 3 | 0 | 55 | 134 |
| Shaman / Enhancement | 388 | 226 | 26 | 0 | 8 | 0 | 55 | 137 |
| Shaman / Restoration | 374 | 216 | 13 | 0 | 4 | 0 | 56 | 143 |
| Warlock / Affliction | 376 | 215 | 9 | 0 | 3 | 0 | 57 | 146 |
| Warlock / Demonology | 384 | 226 | 3 | 0 | 2 | 0 | 56 | 165 |
| Warlock / Destruction | 379 | 219 | 9 | 0 | 4 | 0 | 57 | 149 |
| Warrior / Arms | 395 | 203 | 16 | 0 | 7 | 0 | 55 | 125 |
| Warrior / Fury | 416 | 200 | 19 | 0 | 14 | 0 | 58 | 109 |
| Warrior / Protection | 388 | 191 | 19 | 0 | 4 | 0 | 55 | 113 |

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
| unresolved-unbound (no script, no observer) | 525 | 67 | -- | 592 |
| build-skew (newer than 12.0.7.68453, no consumer) | 13 | 2 | -- | 15 |
| marker-only (observed, executes nothing) | 9 | 4 | -- | 13 |
| ordinary-trigger (script casts fixed children on proc) | 1 | -- | 47 | 48 |
| reusable-family (other families) | 1 | 3 | 33 | 37 |
| amount-adapter | 1 | 1 | 34 | 36 |
| target-adapter | -- | -- | 1 | 1 |
| unresolved-bound-no-executing-hook (script exists, masks 0) | -- | 1 | 11 | 12 |
| state-only-script | -- | -- | 2 | 2 |
| genuinely-unique | -- | -- | 2 | 2 |
| **total** | **550** | **78** | **130** | **758** |

The classification is a strict partition: one proc bucket (from the proc census
shape) and one resolution per provider, decided in this precedence -- executing
hooks (unclassified → unique; trigger families only → ordinary-trigger; amount
families only → amount-adapter; target-select only → target-adapter; state-only
→ marker-only if observed else state-only-script; otherwise reusable-family),
then bound-without-executing-hook, then marker observers, then build skew, then
unresolved. A provider that is both scripted and observed is counted under its
script resolution; the observer stays visible in `marker_observers`.

The 12 drifted providers: Misdirection 34477, Rime 59057, Obliteration 207256,
Stormblast 319930, Dream of Cenarius 372119, Mental Decay 375994, Kingsbane
385627, Cycle of Binding 389718, Ashen Catalyst 390370, Inner Focus 390693,
Power Surge 453109, Acrobatic Strikes 455143 (Prayer of Mending 33076 resolves
after inheritance: `spell_pri_prayer_of_mending_dummy` derives from a helper
base). The 13 marker-only providers: Avenging Wrath, Pillar of Frost, Water
Shield, Thunder Focus Tea, Bone Shield, Shear, Guardian of Elune, Obliteration
281238, Cleaving Strikes, Inner Demon, Power of the Archdruid, Student of
Suffering, Void Metamorphosis (Havoc 80240 dropped to unresolved: its only
observer is a load-time classification site). "Generic HandleProc does nothing"
is therefore *not* equated with gameplay-inert: 13 of the 550 are consumed as
state, 3 are executed by scripts, 525 simply have no Trinity implementation.

---

## 17. Server-semantic graph

`graph.json`: 28,649 nodes, 34,358 edges (kinds: db2-marker 11,669, family/emits
9,225, implemented-by 3,857, bound 3,812, condition 2,114, casts 1,962, queries 784,
removes 319, uses 280, engine 123, linked 211, pet-aura 2). 7,418 weakly connected
components; the largest (11,442 nodes) is held together by `SpellAuraRestrictions`
marker edges and the shared generic scripts. Hubs: `SetBonusValueForEffect`
helper (77 users, Mixology), `spell_gen_mixology_bonus` (77 spells),
`spell_gen_tournament_pennant` (36), `spell_warr_improved_whirlwind_cleave` (31),
`spell_gen_trigger_exclude_*_aura_spell` (22 + 18). 112 non-trivial SCCs (spell ↔
class cast/query cycles, e.g. aura scripts that re-cast their own owner). The
player subgraph from the 4,836 reachable spells has 2,099 nodes (961 spells, 541
classes, 528 scripts, 38 helpers), max depth 9, and reaches 357 child SpellIDs
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

`scripts/research/tests/test_dummy_*.py` (53 tests): oracle arithmetic and probe
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
all corpora, 0 unparsed statements, `spell_scripts` empty); inheritance, namespace
and cross-class helper merging; movement / direct damage never absorbed into a
family; secondary actions retained; the unique set is exactly the four spells.
The pre-existing gearing / charstats / proc suites (1,377 tests) still pass with
the shared conftest.

---

## 20. Shared infrastructure versus exceptions

Final census (`census.json`), current player scope. Owners = spells with a
population effect **or** a script binding (2,880; the 130 bound spells without a
Dummy-class effect are server-side behaviour on ordinary effects). One primary
bucket per owner; tags record overlaps (`tag_overlaps`).

| bucket | owners | population effects |
|---|---:|---:|
| generic-engine (data-decided consumer: DB2 marker read by CheckCast, spell_pet_auras, linked rows) | 32 | 63 |
| script-family (reusable script shapes other than the three below) | 229 | 264 |
| target-adapter | 11 | 6 |
| amount-adapter | 103 | 101 |
| proc-adapter (proc filter + ordinary trigger cast) | 42 | 57 |
| marker-state | 133 | 184 |
| world-data-policy | 3 | 5 |
| source-correction | 2 | 4 |
| **unique** (a consumer exists; the research cannot reduce it to a family) | **4** | **4** |
| script-drift (bound, but no hook matches the 12.1 effect layout) | 35 | 26 |
| unresolved / build-skew (newer than any client build this Trinity supports) | 69 | 108 |
| unresolved / no consumer at all in this Trinity revision | 2,217 | 3,503 |

Category definitions, kept apart on purpose:

| category | meaning | evidence |
|---|---|---|
| unsupported (build skew) | the SpellID did not exist in the last client build family Trinity supports | `build-skew.json` (added since 12.0.7.68367) and no consumer |
| script-drift | a `spell_script_names` row binds a script but every hook's effect/aura/target mask is 0 against 12.1 data | `bindings.json` per hook |
| no consumer in this revision | no script, no strong observer, no world-DB row, no gameplay engine case | absence in all corpora |
| consumer exists, unclassifiable | executing hooks with an action outside every family (teleport, direct damage) | `unique` bucket |
| genuinely inert | **not decidable from this evidence**; absence in Trinity is not evidence of absence in Retail | -- |
| genuinely unique | the 4 `unique` owners | §6 |

Overlaps (`tag_overlaps`): 352 owners are script-only, 130 marker-only, 35
script-drift, 32 DB2-marker + marker, 23 script + marker, 6 script + DB2 marker +
marker, 4 script + correction, 2,286 carry no tag at all. Distinct reusable
primitives in use: **15 families, 60 named sub-kinds** (103 family/sub-kind keys
counting families without a sub-kind), explaining 629 of the 633 executing hooks.
Unresolved profile (2,286 owners incl. build skew): 1,809 passive, 1,774
class-trait roots, 191 spec-spell roots, 96 gear roots, 1,663 Dummy-aura-only
owners, 1,601 with a nonzero Dummy BasePoints (tooltip value holders), 696 proc
carriers, 76 periodic dummies, 42 trigger-without-trigger. A grep of every
unresolved SpellID across the whole Trinity source tree and all world updates
found 80 ids mentioned in code: 79 as enum constants used only in `Validate`
lists or not at all, the rest as coordinates, quest ids and comments; none in a
`HasAura`, cast or `case` consumer (§25).

All-content comparison (174,143 owners): generic-engine 3,581, script-family 2,659,
amount 387, target 190, proc 129, marker 488, world-data 1,095, correction 55,
**unique 94**, script-drift 122, build-skew 4,995, unresolved 160,348; 17 families in use.

**Answer to the central question.** After normalising repeated consumers, the
server side of current player behaviour that Trinity implements is explained
*structurally* by 15 code-shape families (with 60 named parameterisations of
scalar source and cast target, and per-hook secondary actions) plus three
data-driven generic consumers (`SpellAuraRestrictions` marker reads,
`spell_linked_spell`, `EffectTriggerSpell`-with-value), leaving a 4-hook / 4-spell
unique tail. That is a statement about which engine actions the hooks emit; it is
not evidence that 15 production primitives would be *sufficient*, because members
of one family still differ in parameters and gating that a primitive would have to
carry explicitly (§5). The dominant fact is not uniqueness but absence: 2,286 of
2,880 owners (79%) have no server-side consumer in this Trinity revision (35 more
have only a drifted script), and nothing in this evidence base says what they do
in Retail.

---

## 21. Fail-closed inventory

* 2,217 unresolved + 69 build-skew owners: no semantics inferred; absence in
  Trinity is never read as absence in Retail.
* 74 registered-but-never-executing hooks (effect-layout drift) reported per binding;
  35 owners have no other consumer and are bucketed `script-drift`.
* 0 unresolved ScriptNames in player scope, 17 all-content.
* 4 unique hooks (teleport / direct damage) are not folded into any family.
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
| `bindings.json` | `a82519dab38b337dcbe99ae560197064fbb9622a897147aaf8c2123b79108d1a` |
| `build-skew.json` | `de1bab805980b54c99459421889fa52224cd709f962cf07402bdd26ebb8b87e5` |
| `census.json` | `076533c4d1d71b1762831160113dfe46808d122d9a807ed464466cf275b2e45d` |
| `conditions.json` | `33a380e341704fd99a8b4c6054b9bbfe82e2dbfef89e9cbfc891fb41d80e2e15` |
| `corrections.json` | `ae74c2494dd1d55cc931271e549fd510068070d58cde18169d994299a3390f2d` |
| `dispatch-tables.json` | `a68ee2522ecaa2262464490474fbbac21c94212342b30c149ce6c6d9b1b472f8` |
| `families.json` | `936162efb96aec024b08724f237a9efa828739d963e1d061b4c8cec514ab1e6d` |
| `graph.json` | `116c85d8bfe70d905bb9b50876bc4ff4a31d9a517b816a7646d9d233ecfabc38` |
| `hardcoded.json` | `693d5db9fe43fc89f2e86ce97cdc1d10cd2553e38e58b0eff2765cb6effb31f4` |
| `hooks.json` | `7bde384e0d84758454fbfb2cced4e7ff8f23046d37c6e3b01c6c0329c604baf4` |
| `markers.json` | `ee61e12d5d04cb679429047d92a1f4184f83b36b27577f699276dd07d30ad4f1` |
| `population.json` | `1a492f4d102ea52f1405998d4c7dac483f1c435f1c9beb7046746ad51d6f0c1f` |
| `proc-xref.json` | `a491cd2108931907a9ea24ee0f079282a1028dfc8d6fe33b1a47952cad58c06f` |
| `script-index.json` | `5cad4b5c52da44e970008ce00688bfb469c3b12e6c349a10206952ab60a937fc` |
| `sources.json` | `00f0ab4b8c95cb48aa999d7df1837093e7c8f51fa2c2b1511c2c5c1e6766a9ce` |
| `specs.json` | `102e2fc4a46c47cfdc5dba6297752fe0593f217a09f72be6a46b06d69fcaaf7b` |
| `trinity-server-overlay.json` | `19e3bbc7e51c2f610753e5d35472080d889def45a8e66a770e4a0b63b904f4b4` |
| `witnesses.json` | `46bf300c4401504b22632e50e0ab3b47df309538a14584090cef3160c5a87167` |

External documentation, Wowhead, SimC and historical knowledge were not used as
semantic authority; the sibling `simc` checkout was not consulted.

---

## 24. Final report

### Server-side semantic layers

Missing behaviour is supplied, in order of weight for current players, by (1) the
`spell_script_names` → SpellScript/AuraScript hook system (523 bindings, 633 executing
hooks), (2) client-data marker reads that need no code (`SpellAuraRestrictions`
gates, 15 Dummy owners), (3) six `spell_linked_spell` rows, 7 `spell_group`, 10
`spell_threat`, 2 `spell_custom_attr`, 1 condition row, (4) 7 `LoadSpellInfoCorrections`
blocks, (5) 2 dead hardcoded engine branches. `serverside_spell`, `spell_area`,
`spell_target_position`, `spell_pet_auras`, `areatrigger` scripts and pet AI are
encounter/legacy machinery with a 2-effect / 21-areatrigger / 3-creature footprint
in player scope.

### Reusable semantic families

Fifteen structural families explain 629 of the 633 executing hooks in player
scope (§5 table). By primary label: cast-child 207, amount-adapter 94,
proc-filter-adapter 77, cast-child-with-amount 73, linked-aura-mutation 38,
target-adapter 31, cooldown-mutation 25, choose-among-children 17, random-child 16,
suppress-default 10, cast-gate 7, delayed-child 4, pet-owner-forward-cast 3,
resource-mutation 3, consume-and-cast 2; plus 22 state-only bookkeeping hooks and
4 unique hooks. Owners: 389 spells (259 with a Dummy-class effect, 130 on ordinary
effects). Structural family membership is not semantic equivalence: 81 hooks carry
secondary actions outside their family and every member keeps its own parameters.

### Marker/state families

152 Dummy-aura owners are meaningful without executing anything: 139 are read by
other scripts (76 as a `Load()` gate, 59 as `GetAuraEffect(id, EFFECT_n)->GetAmount()`,
35 as a presence check, 4 as a proc gate), 15 by client `SpellAuraRestrictions`,
1 by a gameplay engine branch, 2 by `spell_linked_spell`, 1 by a `CONDITION_AURA`
row. 134 bindings are `Load()`-gated on a talent aura. Their meaningful content
is existence + `GetAmount()` of a specific effect index; stacks and durations are
ordinary.

### Script/helper infrastructure

Shared machinery: the hook system (52 hook types, 59 call sites), 111 free helpers
(one hub: `SetBonusValueForEffect`, 77 users; three helper namespaces), same-file
helper base classes and structs (618 indexed), same-class helper methods (merged in
3,812 bindings), nested `BasicEvent` classes for delays, constructor-parameterised
scripts (13 player bindings), DB2-parameterised generic scripts (40 player
bindings). Fanout: 127 spells carry more than one script; the widest player scripts
bind 31 / 22 / 18 spells.

### Proc cross-reference

550 inert-only → 525 no consumer, 13 build skew, 9 marker-only, 3 scripted (1
trigger, 1 family, 1 amount). 78 mixed → 67 no consumer, 4 marker-only, 4 scripted,
2 build skew, 1 drifted. 130 script-bound → 47 ordinary triggers, 33 reusable
families, 34 amount adapters, 1 target adapter, 2 state-only, 11 drifted, 2
genuinely unique (Dancing Rune Weapon, Divine Image). Strict partition of 758.

### Genuine unique tail

At hook granularity: 4 hooks on 4 spells in player scope (Demonic Circle
teleport, Alter Time, Divine Image, Dancing Rune Weapon); 96 hooks on 39 spells
in all content, the rest legacy. The specific residue *inside* families: 2
hardcoded id tables (Elemental Overload, Divine Image), 1 creature-entry list
(Inescapable Torment), cross-script state reads in 11 hooks, 52 scripts with
member state, 81 hooks with secondary actions, and 2 dead engine branches.

### Runtime facts and mutable state

Explicit inputs the families read: caster / hit unit / aura target / proc actor and
action target identity; `ProcEventInfo` (proc spell, `SpellInfo`, damage/heal info
amounts, `m_appliedMods` of the proc spell); caster aura amounts by (spell, effect
index); health and power values and percentages; explicit target reaction and
facing; remove mode; remaining/max duration; combo points and power costs; the
caster's controlled summons; GUID-captured targets for scheduled work. Mutable
state introduced by scripts: 52 player scripts keep members (GUID lists, primary
target, flags, cached percentages, Alter Time's health/position snapshot); 8 hooks
schedule work; 1 mutates a summon timer; 11 hooks read or write another script's
members through `GetScript<T>()`.

### RNG and ordering boundaries

Directly proved: `roll_chance(aurEff->GetAmount())` in 23 proc filters and 16
random-child hooks is drawn inside the script, at hook time -- for `DoCheckEffectProc`
before the generic `CalcProcChance` roll (which then rolls at 101%), for `OnEffectHit`
before unit targets are processed, for `OnEffectHitTarget` per target. `RandomResize`
in 3 target adapters draws during target selection. Ordering: target hooks →
OnCast → launch effects → per-target BeforeHit/OnHit → aura creation → HIT_TARGET
effects → damage/heal (calc hooks) → aura apply → procs → linked HIT → AfterHit →
AfterCast → linked CAST (§4).

### Ordinary-action boundary

Overlapping action kinds over the 633 hooks (one hook may count several times;
144 do): 326 call `CastSpell` on an authored SpellID; 84 write an amount into
BasePoints or the current hit/absorb pipeline; 66 mutate another aura; 38 touch
cooldowns; 8 change power; 15 edit a target list; 29 prevent a default; 47 roll
RNG; 8 schedule work; 5 route through a pet/owner; 3 query areatriggers; 3
teleport; 1 deals damage directly; 1 summons. By primary label: 77 predicates
(proc filters), 7 cast gates, 10 pure suppressions, 22 state-only. Apart from
the 4 unique hooks, everything Trinity does for current players ultimately
emits ordinary SpellIDs, aura operations or scalars.

### Current-player census

2,880 owners (2,750 with a population effect + 130 script-bound spells on
ordinary effects): the 15 families and 3 generic data consumers cover 422 owners
(15%); 4 are unique; 133 are marker-only (5%); 35 have only a drifted script (1%);
2,286 (79%) have no consumer in this Trinity revision, 69 of them provably newer
than the supported build. Effects: 4,325 population effects, 3,611 unresolved +
26 drifted + 4 unique.

### Open questions

1. **What implements the 2,217 unresolved owners?** Nothing in Trinity `7f3d43b`.
   Reopen per spell with a direct consumer (a newer Trinity, another server, or a
   client-side consumer). Most are passive talents whose Dummy `BasePoints` is a
   value read by nobody server-side.
2. **Effect-layout drift.** 74 player hooks (12 proc providers) match no 12.1
   effect. Reopen when Trinity supports ≥ 12.1; until then their scripts are
   evidence of intent, not behaviour.
3. **Unnamed aura types 320/380/493/531 and effect 324** in player scope: no
   consumer; reopen with a handler.
4. **`TargetHook::CheckEffect` refinement** (selection category × object type) not
   ported; reopen if a target-adapter mask decision depends on it.
5. **The 17 unresolved all-content ScriptNames** (database rows without
   registrations; none in player scope): reopen on the next TDB/Trinity bump.
6. **`serverside_spell` id collisions** with 12.1 `SpellName` (10 ids): Trinity
   would reject those rows on a 12.1 store; reopen when the DB targets 12.1.
7. **Script member state and cross-script reads** are recorded, not modelled;
   reopen when a family oracle needs them (Atonement target list, Stormblast
   original-cast gate).
9. **Structural families versus production primitives.** Whether one primitive
   per family (with the recorded parameters) reproduces every member is not
   proved here; reopen per family with differential tests against Trinity.
8. **`SpellAuraRestrictions`-driven markers** are client facts consumed by
   `Spell::CheckCast`; whether current clients gate more markers than the 15
   found here is not decidable from this snapshot alone.

---

## 25. Closeout review

A hostile closeout pass tried to falsify the strongest conclusions before commit.
Defects found and fixed:

* **Hidden helper behaviour.** Namespace-level helpers (`MajorPlayerHealingCooldownHelpers`,
  `DivineImageHelpers`, `HealingRain`), static methods of same-file helper structs,
  same-file helper base classes, handlers bound directly to
  `PreventHitDefaultEffect`, template handlers (`CheckSpecialization<...>`) and
  methods reached through `GetScript<X>()` were not indexed or not merged. All are
  now indexed (618 helper classes, 111 free functions) and merged into the calling
  handler; class kind resolves through inheritance. Effect: player-scope unresolved
  bindings 6 → 0, all-content 223 → 17; executing player hooks 625 → 633.
* **Silent secondary actions.** A single family label hid additional actions in
  144 hooks (e.g. a `cast-child` that also resets a cooldown). Every hook now
  carries its full `actions` set and `secondary_actions`; the census reports
  overlapping action counts instead of a partition.
* **False "zero unique".** Teleports (`NearTeleportTo`) and direct `Unit::DealDamage`
  were absorbed into cast/power families. They are now action kinds outside every
  family; 4 player hooks / 4 spells (2 proc providers) are genuinely unique.
* **Marker strength.** Load-time classification engine sites were counted as
  strong observers; they are now weak (`engine-classification`). Script queries
  carry a role (load gate / amount read / presence read / proc gate).
  `CONDITION_AURA` rows from every condition source are now extracted.
  Marker-consumed owners 157 → 152.
* **Own-consume regression** introduced while separating areatrigger `Remove()`
  from aura `Remove()` was caught by the synthetic family tests.

Checks that held: the population, scope and proc-bucket totals; the strict
partition of the 758 providers; the 26 player-scope hardcoded sites; a grep of all
2,286 unresolved SpellIDs over the entire Trinity tree and world updates (no
missed consumer: 79 enum constants, coordinates, quest ids, comments); the world-DB
replay (0 unparsed statements after extending the conditions filter); two
independent `all` runs producing byte-identical corpora; the C++ probe rebuilt
from a clean extraction; the pre-existing research suites.
