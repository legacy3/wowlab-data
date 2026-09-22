# Predictive Training: carrier reconstruction and the dodge/parry vs Hit/Critical contradiction

Status: complete 2026-09-20. Read-only pass. Core and Sidecar were not modified; `git status` in both was
inspected before and after and neither was touched. This is research only: nothing here is an executable
contract, and nothing here proposes a Core change.

Question answered: why does a player see "dodge or parry -> 8%/10% damage taken reduction" while Core's
2026-09-20 milestone reproduced "taken direct Hit/Critical -> child 451230"?

Short answer: **both observations are about the same single carrier and the same single event family.** They
differ in exactly one field - the *hit-result filter* - and that field does not exist anywhere in the client
tables. Core imported it from TrinityCore, where it is a **fallback default**, not authored data. The 8%/10%
split is fully authored and lives in the Brewmaster specialization aura, not in the talent.

## 1. Pins

| input | pin |
|---|---|
| wowlab-data | branch `research`, `5de3ddcfec1f4b307d8a95de58d3563b708f435e`; working tree carried unrelated modified `selected-package-corpora/*.json` and untracked `data/latest.duckdb` (neither used as evidence) |
| DB2 snapshot | Wago client build `12.1.0.69497`, `data/tables/` (1,104 tables). sha256 prefixes: SpellEffect `157f4d94`, SpellMisc `3ef1315b`, SpellAuraOptions `6063ce2a`, SpellLabel `1f4772d3`, Spell `ddfeef82`, TraitDefinition `2f4b7c4c`, TraitNodeEntry `c317b6af`, SpecializationSpells `91c46e3b` |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (the milestone pin), read-only |
| TDB world overlay | `TDB_full_world_1200.26021_2026_02_06.sql` (sha256 `54ddf4c1...`) + 522 master updates, via the existing `docs/research/procs-corpora/trinity-world-overlay.json` |
| Core at milestone start | `cf597ccd454023d9a6ce0c345bbb42385fe2a633` |
| Core semantic closeout | `252942ef0354ba5e3705107bff8b2621fa973242` |
| Core when read here | `a803967354c8656ea550a69f6b35d22535f79220` (advanced past the closeout; the Predictive Training owners below are unchanged between the two). Its working tree carried unrelated in-progress edits that grew during this pass - concurrent work by another session, not by this one. Nothing in Core was written here |
| Sidecar at milestone | `722408c9feae40c71ba591f93739ac42872abd3f` |
| Sidecar when read here | `17345558614e34b4e81f273a117fd3d7f50b0937`. Sidecar has no `hit_mask`, no `450992`/`451230`, and no Predictive Training prose at either revision - it is not a party to this contradiction |

Reproduction: every row below was reopened directly from `data/tables/*.csv` with DuckDB
(`duckdb -c "select ... from read_csv('data/tables/<T>.csv', header=true, all_varchar=true, sample_size=-1)"`).
No archived promoted fact was trusted without reopening it.

## 2. Acquisition identities (exact)

| fact | row |
|---|---|
| Trait tree | `TraitTree` 1000, owned by `SkillLineXTraitTree` ID 57 -> `SkillLine` 829 (Monk) |
| Hero sub-tree | `TraitSubTree` 65 "Shado-Pan" |
| Node | `TraitNode` 101245, `Type=2` (choice node), `Flags=9`, `TraitSubTreeID=65` |
| Choice siblings | `TraitNodeXTraitNodeEntry` 122496 -> entry 125064 (Predictive Training, `_Index=200`); the other arm is entry 125065 -> definition 129897 -> spell 450991 Whirling Steel |
| Entry | `TraitNodeEntry` 125064, `TraitDefinitionID=129896`, `MaxRanks=1`, `NodeEntryType=2`, `TraitSubTreeID=0` |
| Definition | `TraitDefinition` 129896, `SpellID=450992`, `OverridesSpellID=0`, `VisibleSpellID=0` |
| Rank shaping | `TraitDefinitionEffectPoints` for 129896: **no rows**. `TraitNodeEntryXTraitCond` for 125064: **no rows**. One rank, no curve, no per-rank amount |
| Spec gating | `TraitNodeGroupXTraitNode` 243339 -> group 10872 and 243761 -> group 10889. Group 10872 carries `TraitCond` 27743 (`SpecSetID=24` -> `SpecSetMember` -> ChrSpecialization **268 Brewmaster**) and `TraitCond` 27745 (`SpecSetID=26` -> **269 Windwalker**), both `CondType=1`, `Flags=4`. Group 10889 carries `TraitCond` 27737, `SpentAmountRequired=1` (ordinary sub-tree spend gate) |

`VisibleSpellID=0` means the definition's visible spell **is** 450992. There is no separate display spell, no
override spell, and no per-specialization definition: Brewmaster and Windwalker acquire the *same* entry,
definition and spell.

Name search over `SpellName.csv` returns exactly two spells named "Predictive Training": **450992** and
**451230**. There is no third, hidden, or specialization-specific variant.

## 3. The complete authored carrier graph

### 3.1 Provider 450992

| table | row |
|---|---|
| `SpellMisc` | ID 743367. `Attributes_0=64` (`SPELL_ATTR0_PASSIVE`), `Attributes_4=32768` (`SPELL_ATTR4_NOT_IN_SPELLBOOK`), `Attributes_11=8` (Trinity `SPELL_ATTR11_UNK3`; Core catalog raw 355 `DoNotLogOnLearn`). `DurationIndex=0`, `RangeIndex=1`, `SchoolMask=1`, `Speed=0`, `CastingTimeIndex=1` |
| `SpellEffect` | ID **1151274**, `EffectIndex=0`, `Effect=6` ApplyAura, `EffectAura=42` ProcTriggerSpell, `EffectTriggerSpell=451230`, `EffectBasePointsF=0`, `ImplicitTarget_0=1` (UnitCaster), `ImplicitTarget_1=0`, `EffectAttributes=0`, misc 0/0, radii 0/0, class mask all zero, all coefficients neutral |
| `SpellAuraOptions` | ID **236958**. `ProcTypeMask_0=139944` = **0x222A8**, `ProcTypeMask_1=0`, `ProcChance=101`, `ProcCharges=0`, `ProcCategoryRecovery=0`, `SpellProcsPerMinuteID=0`, `CumulativeAura=0` |
| `SpellClassOptions` | ID 76244, `SpellClassSet=53` (Monk), all four class-mask words **0** |
| `SpellLabel` | 155572 -> 22 `Monk`, 155573 -> 16 `ClassSpells`, 155574 -> 292 `InternalDnt` |
| `Spell.Description_lang` | *"When you dodge or parry an attack, reduce all damage taken by $451230s1% for the next $451230d."* |
| absent | `SpellCategories`, `SpellCooldowns`, `SpellInterrupts`, `SpellAuraRestrictions`, `SpellShapeshift`, `SpellEquippedItems`, `SpellTargetRestrictions`, `SpellCastingRequirements`, `SpellPower`, `SpellMissile`, `SpellScaling`, `SpellXDescriptionVariables`, `SpellReplacement`, `SpellProcsPerMinute[Mod]` - **no rows for 450992** |

`0x222A8` decodes against `TrinityCore/src/server/game/Spells/SpellMgr.h:97-119` as
`TAKE_MELEE_SWING (0x8) | TAKE_MELEE_ABILITY (0x20) | TAKE_RANGED_ATTACK (0x80) | TAKE_RANGED_ABILITY (0x200)
| TAKE_HARMFUL_ABILITY (0x2000) | TAKE_HARMFUL_SPELL (0x20000)` - a pure **target-side "I was attacked" event
family**, with no outcome qualifier of any kind.

### 3.2 Child 451230

| table | row |
|---|---|
| `SpellMisc` | ID 743609. `Attributes_8=4096` (`SPELL_ATTR8_AURA_POINTS_ON_CLIENT`), `Attributes_13=1` (`SPELL_ATTR13_ALLOW_CLASS_ABILITY_PROCS`; Core raw 416). `DurationIndex=32`, `Speed=0` |
| `SpellDuration` | ID 32 -> `Duration=6000`, `MaxDuration=6000` |
| `SpellEffect` | ID **1151687**, `EffectIndex=0`, `Effect=6` ApplyAura, `EffectAura=87` ModDamagePercentTaken, `EffectBasePointsF=-10`, `EffectMiscValue_0=127` (all seven schools), `ImplicitTarget_0=1` (UnitCaster), `EffectAttributes=0`, `EffectTriggerSpell=0` |
| `SpellAuraOptions` | **no row** - no proc, no charges, no stacking (cumulative 0 -> one application) |
| `SpellClassOptions` | ID 76281, `SpellClassSet=53`, all class-mask words 0 |
| `SpellLabel` | 155700 -> 22 Monk, 155701 -> 16 ClassSpells, 155702 -> 292 InternalDnt, **204772 -> 6596** |
| `Spell` text | `Description_lang = $@spelldesc450992`; `AuraDescription_lang = "Damage taken reduced by $w1%."` |

### 3.3 Closure of the trigger graph

- `SpellEffect where EffectTriggerSpell in (450992, 451230)`: the **only** row is 450992's own effect 1151274
  -> 451230. Nothing triggers 450992; nothing else triggers 451230.
- `SpellReplacement`: no row referencing either spell in either direction.
- `SpellAuraRestrictions`: no rows - **no aura-state dependency** on either spell.
- `SpellScript.csv` was checked and is irrelevant: it is a 42-row client dev table of AreaTrigger movement Lua
  authored by named Blizzard engineers, keyed by its own `ID`/`Name`, with no `SpellID` column and no mention
  of either identity.

So the authored graph is exactly two spells deep. Core's `450992 -> 451230` is not a truncation of a longer
chain; it is the whole chain.

## 4. Where the 8% / 10% split comes from

Label **6596** is a singleton: `SpellLabel` membership for 6596 is **only 451230**. It is a purpose-built
addressing handle, not a category.

The sole consumer of that handle in the entire snapshot is:

> `SpellEffect` ID **1317394** - spell **137023 "Brewmaster Monk"**, `EffectIndex=18`, `Effect=6` ApplyAura,
> `EffectAura=219` `SPELL_AURA_ADD_FLAT_MODIFIER_BY_SPELL_LABEL`, `EffectMiscValue_0=3`, `EffectMiscValue_1=6596`,
> `EffectBasePointsF=2`, `ImplicitTarget_0=1`, `EffectAttributes=0`.

137023 is the Brewmaster specialization aura: `SpecializationSpells` ID **3498**, `SpecID=268`. The Windwalker
counterpart is `SpecializationSpells` ID 3500, `SpecID=269`, `SpellID=137025`, and 137025 carries **no** label-6596
effect (an exhaustive scan of `EffectAura in (218,219)` for `EffectMiscValue_1=6596` returns 137023 only).

Direct-consumer decode at the pin:

- `SpellAuraDefines.h:306` - aura **219** = `SPELL_AURA_ADD_FLAT_MODIFIER_BY_SPELL_LABEL`.
- `SpellAuraEffects.cpp:1049-1053` - builds `SpellFlatModifierByLabel(op = MiscValue, label = MiscValueB, value = amount)`.
- `SpellDefines.h:152-157` - `SpellModOp` **3** = `PointsIndex0`.
- `Object.cpp:1678-1686` - `WorldObject::ApplyEffectModifiers` applies `SpellModOp::PointsIndex0` to `EFFECT_0`.

Arithmetic: 451230 effect 0 base `-10`, plus flat `+2` when (and only when) the Brewmaster spec aura is active:

- Windwalker: `-10` -> tooltip **10%**
- Brewmaster: `-10 + 2 = -8` -> tooltip **8%**

This is also exactly why the client tooltip differs per specialization without a second definition: the
description resolves `$451230s1`, and the client evaluates spell effect values through the same active spell
modifiers. **The 8%/10% split is fully authored client data.** No script, no second child, no second
definition, no spec condition on the trait, no effect-point curve is involved.

Negative control: the other three labels on the chain (16 ClassSpells, 22 Monk, 292 InternalDnt) do have
label modifiers in the snapshot, but every one is `EffectAura=218` with `op` 5 (Range), 10 (ChangeCastTime) or
22 (PeriodicHealingAndDamage) on unrelated non-Monk carriers (Shroud of Winter, Divine Ascension, Spatial
Paradox, Smoke Bomb, ...). None uses op 3 and none can reach 451230's amount.

Other numeric collisions on 6596 were checked and rejected: spells 1288218/1288323/1288477/1292068 use it as
`EffectMiscValue_1` on `Effect=28` Summon (a `SummonProperties` id), and 209386 Windfury Totem Passive uses it
on `EffectAura=395` `SPELL_AURA_AREA_TRIGGER` (an AreaTrigger id). Neither namespace is `SpellLabel`.

## 5. Where dodge/parry enters - and where it does not

**It is not in the client tables.** There is no hit-result / outcome column anywhere in the DB2 proc surface:
`SpellAuraOptions` carries `ProcTypeMask_0/1`, `ProcChance`, `ProcCharges`, `ProcCategoryRecovery`,
`SpellProcsPerMinuteID`, `CumulativeAura` and nothing else. `ProcTypeMask_1` is 0 here, so no `ProcFlags2` bit
is involved either.

In the direct consumer the outcome filter is a distinct field with a **world-database-only** source:

- `SpellMgr.h:234-250` - `ProcFlagsHit`, where `PROC_HIT_DODGE = 0x10`, `PROC_HIT_PARRY = 0x20`, and the comment
  on `PROC_HIT_NONE = 0` reads *"no value - PROC_HIT_NORMAL | PROC_HIT_CRITICAL for TAKEN proc type"*.
- `SpellMgr.h:286` - `SpellProcEntry::HitMask`, loaded from the `spell_proc` world table
  (`spell_proc_columns` in the overlay corpus includes `HitMask`; DB2 has no counterpart).
- `SpellMgr.cpp:1497` `LoadSpellProcs`, generation path at `:1835`:
  `procEntry.HitMask = PROC_HIT_NONE; // uses default proc @see SpellMgr::CanSpellTriggerProcOnEvent`.
- `SpellMgr.cpp:574-591` `CanSpellTriggerProcOnEvent`: when `HitMask` is zero and the event is a taken hit,
  it substitutes `PROC_HIT_NORMAL | PROC_HIT_CRITICAL` (**0x3**).

The event itself absolutely does carry dodge/parry on the taken side:

- `Unit.cpp:1345/1360/1364` - `damageInfo->ProcVictim = PROC_FLAG_TAKE_MELEE_SWING` is assigned for the swing
  **regardless of outcome**; only `PROC_FLAG_TAKE_ANY_DAMAGE` is added conditionally at `:1510` when damage > 0.
- `Unit.cpp:166-190` - `DamageInfo(CalcDamageInfo const&)` sets `PROC_HIT_DODGE` (`:173`) / `PROC_HIT_PARRY` (`:176`).
- `Unit.cpp:2347` - dispatches `ProcSkillsAndAuras(..., damageInfo.ProcVictim, ..., dmgInfo.GetHitMask(), ...)`.
- `Unit.cpp:10419+` - `createProcHitMask` sets `PROC_HIT_DODGE` (`:10431`) / `PROC_HIT_PARRY` (`:10434`) for the
  spell path.

So a dodged or parried swing **does** deliver a `TAKE_MELEE_SWING` proc event to the victim, carrying hit mask
`0x10`/`0x20`. 450992's authored `0x222A8` subscribes to precisely that event family. The only thing standing
between that event and 451230 is the missing outcome filter.

**450992 has no `spell_proc` row and no script at the pin.** Verified in
`docs/research/procs-corpora/trinity-world-overlay.json` (1,280 `spell_proc` rows, TDB1200.26021 + 522 master
updates): neither 450992 nor 451230 appears in `spell_proc`, `spell_proc_conditions`, `spell_custom_attr` or
`spell_script_names`. A scoped `grep -rnw "450992|451230" src/server/` over the Trinity checkout returns
nothing.

### The structural precedent

Trinity expresses "dodge/parry only" in exactly the missing field, 18 times in the pinned world DB. The clean
witness is **spell 37519 "Rage Bonus"**:

- DB2: `ProcTypeMask_0 = 20` (`0x14` = DEAL_MELEE_SWING | DEAL_MELEE_ABILITY) - a bare event family, no outcome.
- Client text: *"You gain an additional ... rage each time one of your attacks is parried or dodged."*
- World DB: `spell_proc` row with `HitMask = 48` = `0x30` = `PROC_HIT_DODGE | PROC_HIT_PARRY`.

Same shape as Predictive Training, one row apart. 450992 simply never received that row.

Corroborating (not decisive) pattern: other current spells whose description says "dodge or parry" carry the
same coarse generic masks - Endurance 310603/310607/310608 (`ProcTypeMask_0 = 664232` = `0x222A8 | 0x80000`)
and Serrated Edge 298739. Blizzard authors the event family in DB2 and keeps the outcome qualifier server-side
across the board.

**Conclusion for question C:** dodge/parry is real, is the talent's actual trigger per the client's own
authored description string, and belongs to **external/server policy**. It has no client-table representation.
No edge was invented to reconcile it.

## 6. Answers

**A. What is 450992?** The visible talent spell itself, and simultaneously the proc provider. `TraitDefinition`
129896 has `VisibleSpellID = 0` and `OverridesSpellID = 0`, so the definition's display spell is 450992; it is
a passive (`SPELL_ATTR0_PASSIVE`), not-in-spellbook aura whose single effect is raw-6 / aura-42 ProcTriggerSpell.
It is not a helper and not a specialization branch. The same spell serves both specializations.

**B. What is 451230?** The triggered child and the final mitigation aura in one: a single raw-6 / aura-87
`ModDamagePercentTaken` effect, `-10` base, all-schools misc 127, self-delivered, six-second finite lifetime,
one stack, no proc options, no dispel route. Core identifies it as the authored child because
`SpellEffect` 1151274 `EffectTriggerSpell` literally names it, and it is the only such edge. There is no
downstream relationship: `EffectTriggerSpell = 0`, and nothing else in the snapshot triggers or replaces it.
It additionally carries singleton label 6596, which is the addressing handle used in section 4.

**C. Where does dodge/parry enter?** Section 5. In the hit-result filter, which exists only in server policy
(represented in Trinity as `spell_proc.HitMask`, absent for this spell). Strongest evidence, in order:
(1) the client's own `Spell.Description_lang` on 450992; (2) the authored `0x222A8` mask subscribing to exactly
the taken-attack event family in which dodge/parry outcomes are delivered; (3) the total absence of any
hit-result column in DB2; (4) the 37519 precedent showing the field's normal home.

**D. Where do 8% and 10% enter?** Section 4. `SpellEffect` 1317394 on the Brewmaster spec aura 137023
(`SpecializationSpells` 3498, spec 268), aura 219, op 3 `PointsIndex0`, label 6596, `+2` flat onto 451230's
`-10`. Windwalker has no such row and keeps `-10`. Fully authored; no script, override, curve or second child.

**E. Are we looking at two different event layers?** **No.** The hypothesised chain
(dodge/parry -> hidden state -> later Hit/Critical -> 450992 -> 451230) is refuted: there is no hidden state
spell, no second provider, no additional trigger edge, no aura-state dependency (`SpellAuraRestrictions` empty)
and no spell replacement. There is **one** event layer - the taken-attack event - and **one** disputed field
inside it. The reproduced graph is section 7.

**F. Did Core name/scope the milestone correctly?** Section 8.

**G. Does any research/oracle need correction?** Section 9.

## 7. Reproduced mechanism graph

```
  Monk (SkillLine 829) -> TraitTree 1000 -> TraitSubTree 65 "Shado-Pan"
        |
        |  TraitNodeGroup 10872 : TraitCond 27743 (SpecSet 24 -> spec 268 Brewmaster)
        |                         TraitCond 27745 (SpecSet 26 -> spec 269 Windwalker)
        v
  TraitNode 101245 (choice)  --[TraitNodeXTraitNodeEntry 122496]-->  TraitNodeEntry 125064 (MaxRanks 1)
        |                                                                     |
        | (other arm: 125065 -> 129897 -> 450991 Whirling Steel)              v
        |                                                        TraitDefinition 129896 (VisibleSpellID 0)
        |                                                                     |
        v                                                                     v
                                   SPELL 450992  "Predictive Training"  (passive, self)
                                   SpellEffect 1151274 : raw 6 ApplyAura / raw 42 ProcTriggerSpell
                                   SpellAuraOptions 236958 : ProcTypeMask 0x222A8, chance 101,
                                                             no PPM / charges / ICD
                                             |
                                             |  EVENT LAYER (one, not two)
                                             |  authored : taken melee swing | melee ability
                                             |             | ranged attack | ranged ability
                                             |             | harmful ability | harmful spell
                                             |
                                   ==========+===========================================
                                   OUTCOME FILTER  -- NOT PRESENT IN ANY CLIENT TABLE --
                                   ==========+===========================================
                                             |
                         +-------------------+--------------------+
                         |                                        |
             Blizzard / client text                    TrinityCore at the pin
             "when you dodge or parry"                 no spell_proc row, no script
             => hit results DODGE|PARRY (0x30)          => CanSpellTriggerProcOnEvent
                                                           SpellMgr.cpp:578-585 substitutes
                                                           NORMAL|CRITICAL (0x3)
                         |                                        |
                         +-------------------+--------------------+
                                             |
                                             v  EffectTriggerSpell (the only edge)
                                   SPELL 451230  "Predictive Training"
                                   SpellEffect 1151687 : raw 6 ApplyAura / raw 87 ModDamagePercentTaken
                                   points -10, misc 127 (all schools), self, 6.000 s (SpellDuration 32),
                                   1 stack, SpellLabel 204772 -> label 6596
                                             |
                    SpellEffect 1317394 (spell 137023 "Brewmaster Monk", SpecializationSpells 3498)
                    raw 6 / raw 219 AddFlatModifierByLabel, op 3 PointsIndex0, label 6596, +2
                                             |
                         +-------------------+--------------------+
                         |                                        |
                 Windwalker  -10                          Brewmaster  -10 + 2 = -8
                         |                                        |
                         +-------------------+--------------------+
                                             v
                                   damage-taken authority: raw 87 percentage,
                                   all schools, for the remaining lifetime
```

## 8. Reconciling the tooltip with Core's Hit/Critical policy

Core's admitted effective policy is pinned verbatim in `core/tools/test-support/src/selected_trait_proc.rs:201-220`:

```
proc_flags: [0x0002_22a8, 0],  spell_type_mask: 0x7,  spell_phase_mask: 0x2,
hit_mask: 0x3,                 attributes_mask: 0x2,  effect_mask: 1,
fixed_chance: 101.0,           procs_per_minute: 0.0, cooldown_ms: 0, charges: 0,
proc_condition: Absent,        code_correction: Absent,
```

Field-by-field provenance:

| field | value | authored in DB2? |
|---|---|---|
| `proc_flags` | `0x222A8` | **yes** - `SpellAuraOptions` 236958 `ProcTypeMask_0` |
| `fixed_chance` | `101` | **yes** - `ProcChance` |
| `procs_per_minute` / `cooldown_ms` / `charges` | 0 | **yes** - `SpellProcsPerMinuteID`, `ProcCategoryRecovery`, `ProcCharges` |
| `spell_type_mask` | `0x7` | **no** - `LoadSpellProcs` seeds every aura with `PROC_SPELL_TYPE_MASK_ALL` (SpellMgr.cpp:1676) and never narrows aura 42; assigned at SpellMgr.cpp:1833 |
| `spell_phase_mask` | `0x2` | **no** - `procEntry.SpellPhaseMask = PROC_SPELL_PHASE_HIT`, SpellMgr.cpp:1834 |
| `attributes_mask` | `0x2` `TRIGGERED_CAN_PROC` | **no** - granted by the `addTriggerFlag` branch for taken-mask aura-42 providers, SpellMgr.cpp:1789-1799 / 1876-1881 |
| **`hit_mask`** | **`0x3`** | **no** - `HitMask` is stored as `PROC_HIT_NONE` (SpellMgr.cpp:1835) and expanded to `NORMAL\|CRITICAL` only by the *fallback* at SpellMgr.cpp:578-585 |

The contradiction resolves cleanly: **the one field that decides Hit/Critical versus dodge/parry is the one
field in Core's admitted policy that has no authored source.** Core's `hit_mask: 0x3` is a TrinityCore
compatibility default for spells whose real filter was never ported, not a reproduction of Predictive Training.

Core implements it faithfully and tests it explicitly - `crates/combat/tests/selected_proc_provider.rs:270`
`normal_and_critical_hits_activate_the_child_after_damage_commit` and `:316`
`miss_dodge_and_parry_do_not_activate_the_child`. The second test asserts, as an invariant, the exact case the
client's own description says is the trigger.

Two corroborating (not decisive) observations:

1. **Uptime sanity.** With `ProcChance 101`, no PPM, no charges and `ProcCategoryRecovery = 0`, the admitted
   policy means *every single landed hit taken* applies a fresh 6 s aura. Any Monk in melee contact holds the
   mitigation permanently, and the talent degenerates to a flat passive. Under the authored dodge/parry reading
   the uptime is instead proportional to avoidance, which is what a talent named "Predictive Training" in an
   avoidance-themed hero tree is shaped like.
2. **Mask over-coverage.** `0x222A8` includes `TAKE_HARMFUL_ABILITY` and `TAKE_HARMFUL_SPELL`, which can never
   produce a dodge or parry outcome. So the authored mask is *broader* than dodge/parry alone. This is recorded
   as an unknown in section 10 rather than resolved.

Nothing here establishes what Blizzard's server actually does. It establishes that Core's value for the
deciding field came from a Trinity fallback and contradicts the only authored statement of intent in the
snapshot.

## 9. Assessment of Core's milestone scope

None of the four offered classifications fits exactly. The closest is **(2) - Core correctly implements a
bounded subgraph, and the milestone name can read as complete talent support** - with one correction that
matters:

- **What Core got right.** The carrier identities are exactly right and exhaustive: entry 125064, definition
  129896, provider `450992:1`, child 451230 *is* the entire authored Predictive Training graph. There is no
  larger mechanism Core stopped short of, and `450992 -> 451230` is not a truncation. The authored policy
  fields Core admitted (`0x222A8`, chance 101, no PPM/ICD/charges) all reproduce real rows. Classification (3)
  is therefore wrong as worded: the policy is attached to the right carrier.
- **What is off.** The admitted **outcome set** is not a subset of the talent's behavior, it is (on the only
  authored evidence available) **disjoint** from it: dodge and parry are non-landing results, and Core's own
  test names them as exclusions. Calling this "a bounded subgraph of Predictive Training" understates the
  problem - the executable vertical is real and well-built, but the behavior it gates is a generic
  "guaranteed damage-reduction proc on any landed hit taken", which is Trinity's fallback shape rather than
  this talent's.
- **Second gap.** Core reproduces only the Windwalker amount. `-10` is hardcoded in the fixture; the
  Brewmaster `-8` path is unreachable, because Core's raw-219 contract
  (`crates/dbc/src/aura_subtype.rs:492`) admits *selected trait* sources (Unyielding Stance 1235047:1,
  Recuperator 378996:1) and 137023 is a **specialization** aura, not a selected trait entry. The milestone
  prose does not mention the specialization-dependent amount at all. Notably, Core's raw-219 contract already
  names "a declared finite active self-applied raw-6/raw-87 damage-taken modifier effect 1" as an admitted
  target shape - i.e. exactly 451230's shape - so the missing piece is the source-acquisition side, not the
  target side.
- **Classification (4) is rejected.** The evidence is sufficient to decide where the deciding field lives and
  that it is not authored in the client; what remains unknown is Blizzard's exact server filter, which is a
  different question.

Practical framing: the milestone is sound as *engine infrastructure* (immutable effective-proc catalog,
provider/child compilation, post-commit trial ordering, forced draw, deferred child) and unsound as a
*claim about Predictive Training's behavior*. Nothing in Core's ledger or research note records that
`hit_mask = 0x3` is a host fallback rather than a reproduced row, and neither mentions the client description
string that contradicts it.

## 10. Research / oracle terminology audit

Checked, and **clean** - no existing artefact conflates visible talent / provider / helper / child / final aura:

- `docs/research/*.md` in this repo: no occurrence of `450992`, `451230` or "Predictive" in any prose note.
- `docs/research/procs-corpora/census.json`: 450992 appears only as an entry in `player/provider_ids`.
- `docs/research/selected-package-corpora/proc-policy.json`: only as the single `examples` member of shape
  `cum=0;rec=0;chance=101;charges=0;ppm=0;mask=0x222A8`. Purely structural, no semantic claim.
- `docs/research/selected-package-corpora/proc-counterexamples.json`: only inside the 1,005-member `all_ids`
  of `D-CE-10`, explicitly annotated "listed as the confirming population, not as a failure".
- `docs/research/aura-lifecycle-corpora/identity.json`: mechanical `player_rows` for both spells (450992
  passive/multislot, 451230 per-caster/refresh). Correct and carrier-neutral.
- `docs/research/targeting-corpora/*`: identity-level rows only.
- Core Sidecar at both pinned revisions: no occurrence of either id, no `hit_mask` concept at all.

One terminology hazard worth recording, in Core rather than here:

> Core's `docs/research/selected-predictive-training-proc.md` and the 2026-09-20 ledger entry both state the
> policy as *"normal-or-critical hit mask"* alongside the genuinely authored fields, with no marker
> distinguishing a reproduced DB2 row from a Trinity default. Any later reader will reasonably take all seven
> listed fields as equally source-backed. The same documents describe the deliverable as "Predictive Training",
> which reads as complete talent support.

This note does not edit Core or Sidecar. Correcting that prose is a Core decision.

## 11. Explicit unknowns

1. **Blizzard's actual server filter is not observable here.** That it is `DODGE|PARRY` is inferred from the
   client description string, which is authored text, not executable authority. No packet capture, no server
   source, no in-game experiment was run in this pass.
2. **The mask/text mismatch is unresolved.** `0x222A8` includes `TAKE_HARMFUL_ABILITY` and `TAKE_HARMFUL_SPELL`,
   which cannot yield dodge or parry. Either (a) Blizzard authors a coarse event family and narrows it entirely
   server-side, or (b) the real mechanic also fires on some non-avoided events and the tooltip is a partial
   description. Under (a) Core's outcome set is **disjoint** from the real one; under (b) it could be a
   **subset**. The evidence does not decide between them, and this contradiction is preserved rather than
   resolved.
3. **`SPELL_ATTR11_UNK3` (`Attributes_11 = 8`) on 450992 is an unnamed bit** in the pinned consumer (Core maps
   raw 355 to `DoNotLogOnLearn`). If it turns out to carry proc-related meaning, section 5 must be reopened.
4. **`SPELL_ATTR13_ALLOW_CLASS_ABILITY_PROCS` (raw 416) on 451230** is globally Disabled in Core and inert only
   through the declared proc-child relationship. Its real semantics were not reconstructed in this pass.
5. **Whether the client applies the 137023 label modifier to the *aura amount* as well as the tooltip** was
   verified only through the Trinity consumer path (`Object.cpp:1678-1686`), which applies it to both. Retail
   confirmation that a Brewmaster actually mitigates 8% (and not 10% with an 8% tooltip) was not obtained.
6. **Build skew.** The DB2 snapshot is client `12.1.0.69497`; the pinned Trinity supports clients up to
   `12.0.7.68453`. Enum decodes used here (proc flags, hit mask, aura 219, SpellModOp 3) are long-stable, but
   the skew is not zero.

## 12. Exact reopen conditions

Reopen this note when any of the following changes:

1. **A `spell_proc` row for 450992 appears** in a newer TDB or in `sql/updates/world/master`. Re-extract
   `docs/research/procs-corpora/trinity-world-overlay.json` and read its `HitMask`. If it is `0x30`, section 8
   is confirmed and Core's `hit_mask: 0x3` is proved a fallback artefact by the direct consumer itself.
2. **A Trinity script for 450992/451230 appears** (`spell_script_names`, `src/server/scripts/Spells/spell_monk.cpp`).
3. **DB2 grows a hit-result / outcome column** on `SpellAuraOptions` or a new proc sidecar table in any future
   build. That would move dodge/parry from external policy into authored data and invalidate section 5.
4. **`SpellEffect` 1317394 changes** (spell 137023, EffectIndex 18) - amount, op or label - or a label-6596
   modifier appears on 137025 or any other carrier. Re-run the section 4 scan
   (`EffectAura in (218,219) and EffectMiscValue_1 = 6596`).
5. **`SpellLabel` 6596 gains a second member**, which would end its singleton addressing-handle status.
6. **451230's `EffectBasePointsF` moves off `-10`**, or `SpellDuration` 32 changes, or `SpellAuraOptions`
   gains a row for 451230.
7. **A third spell named "Predictive Training" appears**, or `TraitDefinition` 129896 gains a nonzero
   `VisibleSpellID` / `OverridesSpellID`, or entry 125064 gains `TraitDefinitionEffectPoints` rows.
8. **Core changes the admitted policy**: if `hit_mask` in `tools/test-support/src/selected_trait_proc.rs`
   moves off `0x3`, or the raw-219 contract in `crates/dbc/src/aura_subtype.rs` is widened to specialization
   auras (which would make the Brewmaster `-8` reachable), sections 8 and 9 must be rewritten.
9. **A retail experiment is run.** A Brewmaster and a Windwalker log with combat-log proc timestamps against a
   melee attacker would settle unknowns 1, 2 and 5 at once, and is the single highest-value follow-up.
