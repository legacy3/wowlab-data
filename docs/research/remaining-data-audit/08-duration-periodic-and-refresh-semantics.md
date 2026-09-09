# Duration, periodic, and refresh semantics

## Scope and evidence

This report audits `SpellDuration`, `SpellMisc.{DurationIndex,PvPDurationIndex,MinDuration}`,
`SpellEffect.{EffectAuraPeriod,EffectAmplitude}`, and the current attributes that materially alter
duration, periodic scheduling, or refresh. It intentionally separates authored fields from the
runtime meanings implemented by comparison projects.

Evidence versions are Wago retail `12.1.0.69497` at repository commit
`2ddced452a6f9076de5c86bc92f73de5b60f8556`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimulationCraft
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned every current row. Important
counts were repeated with grouped censuses and independently resolved `(SpellID,DifficultyID)`
joins. Difficulty joins below use exact difficulty first and difficulty zero only when the exact row
is absent.

## Duration-table topology

`SpellDuration.csv` has 435 records but only 384 distinct
`(Duration,MaxDuration,DurationPerResource)` triples. Forty-four triples are duplicated, accounting
for 51 records beyond a value-deduplicated table. IDs are identities, however; equality of values
does not authorize substituting one ID for another.

`SpellMisc.csv` has 417,571 rows. `DurationIndex` is zero on 207,630 and nonzero on 209,941; all
nonzero references resolve, spanning 343 IDs including zero and 342 nonzero IDs. `PvPDurationIndex`
is nonzero on 417 rows, spans 31 IDs, and also has no orphan. Ninety-three of the 435 duration
records are referenced by neither field in this build.

The duration records have these structural properties:

- 35 have `Duration != MaxDuration`.
- 28 have nonzero `DurationPerResource`.
- Only two contain a negative duration component: ID 21 is `(-1,-1,0)` and ID 427 is
  `(-600000,600000,0)`.
- ID 21 is used by 95,755 base-duration rows and two PvP-duration rows. ID 427 is used once, by
  `Debuff All Resist` (spell 101822).

Trinity's `SpellInfo::GetDuration/GetMaxDuration` (`SpellInfo.cpp:3975-3998`) assigns exactly `-1`
the permanent sentinel and takes the absolute value of every other negative number. Therefore ID
427 becomes a positive 600-second duration there. **Strongly verified for Trinity:** `-1` means
permanent and non-`-1` negatives are not permanent. **Rejected interpretation:** every negative
duration means permanent. The client/retail reason for ID 427's signed minimum is unknown.

For a record whose minimum and maximum differ, `WorldObject::CalcSpellDuration`
(`Object.cpp:1707-1730`) returns the minimum unless the cast consumed combo points, then computes
`min + DurationPerResource * consumed_combo_points`, capped at the maximum. This is **strongly
verified in Trinity**, but it is narrower than the Wago field name: Trinity does not generically
search every resource type. Seven varying records have no per-resource increment; their authored
interpolation rule is **Unknown after exhaustive available evidence**.

### PvP duration

The 417 PvP references clearly form an alternate-duration relation. Examples include Polymorph
60,000 ms base / 6,000 ms PvP, Hammer of Justice 6,000 / 5,000, and Kidney Shot with base
`(3000,8000,1000)` versus PvP `(0,5000,1000)`. This is **Partially characterized — PvP-specific
duration record identity**.

Trinity's `SpellInfo` construction loads `DurationIndex` but never reads `PvPDurationIndex`
(`SpellInfo.cpp:1337-1372`), despite retaining the DB2 member in `DB2Structure.h:4050-4068`.
SimulationCraft has no current `PvPDurationIndex` consumer. The mode-selection rule, zero-minimum
meaning, and interaction with PvP modifiers therefore remain unknown. The population proves a
relation, not an executable selection contract.

`SpellMisc.MinDuration` is nonzero on 783 rows, but Trinity uses it as a minimum projectile travel
time in hit-delay calculations (`Spell.cpp:888-896,2514-2520`), not as an aura-duration floor.
Calling it a minimum aura duration is a **Rejected interpretation**.

## Periodic population

`SpellEffect.csv` has 629,298 rows / 413,805 spells. `EffectAuraPeriod` is nonzero on 47,952 rows /
39,252 spells. Its most common periods in milliseconds are 1,000 (20,352 rows), 2,000 (7,251),
3,000 (4,648), 500 (3,988), 1,500 (1,625), 5,000 (1,558), 10,000 (1,290), and 250 (1,138).

The complete nonzero-period population by aura subtype is:

| Aura raw | Rows | Spells |
|---:|---:|---:|
| 23 | 17,541 | 16,145 |
| 3 | 16,339 | 10,641 |
| 226 | 9,108 | 8,657 |
| 20 | 1,110 | 1,053 |
| 70 | 848 | 846 |
| 8 | 809 | 752 |
| 89 | 727 | 691 |
| 21 | 447 | 320 |
| 24 | 402 | 333 |
| 53 | 295 | 232 |
| 227 | 142 | 110 |
| 162 | 63 | 51 |
| 64 | 62 | 54 |
| 48 | 35 | 35 |
| 62 | 12 | 9 |
| 0 | 12 | 12 |

This distribution is evidence that the period field is authored beyond the obvious damage/heal
families. The 12 aura-zero rows in particular falsify “nonzero period implies an ApplyAura
operation.”

Trinity maps this field to `SpellEffectInfo::ApplyAuraPeriod` (`SpellInfo.cpp:403-425`).
`AuraEffect::CalculatePeriodic` recognizes a closed set of aura operations—periodic damage, heal,
trigger, energize, leech, funnel, mana leech, percentage damage, power burn, and periodic dummy
families—then permits scripts to alter the decision and period (`SpellAuraEffects.cpp:907-1031`). A
recognized periodic aura with a zero final period is disabled to prevent an infinite update loop.
Thus **Strongly verified:** this field is the authored base interval for aura periodic scheduling,
but only a compatible aura operation or script turns it into live ticks.

### Duration-to-period balance and outliers

Resolving all 47,952 periodic rows to `SpellMisc` gives 42,691 exact-difficulty rows and 5,261
difficulty-zero fallbacks. Of these, 621 have no duration record, 14,176 resolve to permanent
duration, six resolve to duration zero, and 33,149 resolve to a positive duration. Among positive
durations, 31,599 divide evenly by the authored period and 1,550 do not; 57 have a period longer
than the nominal duration.

The latter include `Army of the Dead` (2,000 ms period / 1,000 ms duration), `Summon Lorgosh`
(1,000 / 100), multiple rafting visuals (1,000 / 500), and timer/visual spells. These are first-class
outliers: a nonzero period does not guarantee that a normal full tick occurs before expiry, and
integer `duration / period` is not a universal authored tick count.

Trinity's `GetTotalTicks` uses integer division and optionally adds the initial occurrence
(`SpellAuraEffects.cpp:938-946`). Runtime update scheduling, scripts, refresh, channeled haste, and
partial-final-tick behavior can differ. SimC explicitly models partial ticks in its own DoT engine;
that simulation policy is not evidence that every Wago periodic aura has the same policy.

## `EffectAmplitude` is not the aura period

`EffectAmplitude` is separately nonzero on 10,291 rows / 9,361 spells, with 1,305 rows also having
nonzero `EffectAuraPeriod`. It spans 50 effect kinds: Apply Aura (2,616), School Damage (2,428),
Summon (2,149), Jump Destination (800), Teleport (630), and many smaller families. Only 915 rows /
912 spells are Jump or Jump Destination.

Trinity stores it separately as `SpellEffectInfo::Amplitude` and its only generic gameplay use
found is a positive movement-speed multiplier for Jump and Jump Destination
(`SpellEffects.cpp:928-973`); another occurrence is a power-drain combat-log argument
(`Spell.cpp:5083-5095`). It ignores the field for the overwhelming majority of current effect
kinds. Therefore treating `EffectAmplitude` as the aura tick interval is a **Rejected
interpretation**. Its generic meaning across the other 48 effect kinds is **Unknown after exhaustive
available evidence**. SimC's generated field named `_amplitude` is exposed as `period`
(`sc_data.cpp:173`), but its extraction projection differs and cannot override the two distinct
current Wago columns.

## Attributes that compose with duration and refresh

Spell-attribute raw identities below are flattened as `word * 32 + bit`.

| Raw | Word.bit | Mask | Rows | Spells | Executable meaning and disposition |
|---:|---:|---:|---:|---:|---|
| 43 | 1.11 | `0x00000800` | 6,781 | 6,642 | Aura unique; a nonstacking recast does not refresh — **Strongly verified** |
| 169 | 5.9 | `0x00000200` | 8,702 | 8,052 | Extra initial periodic occurrence — **Strongly verified** |
| 173 | 5.13 | `0x00002000` | 888 | 885 | Caster spell/cast haste changes periodic interval — **Strongly verified** |
| 189 | 5.29 | `0x20000000` | 1,189 | 1,185 | Aura unique per caster; nonstacking recast does not refresh — **Strongly verified in Trinity** |
| 273 | 8.17 | `0x00020000` | 374 | 374 | Casting haste changes duration, aligned to whole live periods when present — **Strongly verified in Trinity** |
| 278 | 8.22 | `0x00400000` | 34 | 34 | Melee haste changes periodic interval — **Strongly verified** |
| 291 | 9.3 | `0x00000008` | 1,168 | 1,146 | Do not log aura refresh, client-only — **Partially characterized presentation metadata** |
| 436 | 13.20 | `0x00100000` | 795 | 795 | Refresh carries remaining duration up to 130% and preserves periodic timer — **Strongly verified in Trinity** |
| 489 | 15.9 | `0x00000200` | 36 | 33 | Aura does not refresh — **Partially characterized** |

On application/reapplication Trinity resets the periodic state; raw 169 changes the initial timer so
the first occurrence happens immediately and increments the total-tick estimate
(`SpellAuraEffects.cpp:938-1028`). Channeled spells have their interval altered first; otherwise raw
173 uses casting speed, otherwise raw 278 uses melee haste. Thirteen current rows set both 173 and
278, so the `else if` ordering means the casting-haste branch wins in Trinity. That priority is
**strongly verified for Trinity**, not for retail.

Raw 273 operates on duration, not directly on the period. During application Trinity aligns a
positive duration to a whole number of already-hastened periodic intervals, or multiplies duration
by casting speed if no periodic effect exists (`Spell.cpp:3268-3283`). This distinction prevents
collapsing raws 173 and 273 into one “haste affects DoT” flag.

Ordinary refresh recalculates maximum duration and periodic state. Raw 436 changes two pieces:
Trinity preserves the current periodic timer and caps carried duration at 130% of the new base
duration (`SpellAuras.cpp:976-989`, `Spell.cpp:3284-3288`). Of its 795 rows, 149 also have initial
period and 137 also have haste-affected duration. Commit `e1f345756b` introduced the implementation
in 2023; `92773e207c` moved/fixed the duration carry in 2025. This history supports the current
contract but also shows why the enum name alone is insufficient.

Unique-aura raws 43 and 189 suppress the refresh branch only for nonstacking auras; stacking auras
still refresh when their stack does not decrease (`SpellAuras.cpp:1091-1125`). They are admission
rules for refresh, unlike raw 436's change to an admitted refresh. The word-9 logging bit changes
presentation only and must not be used as a duration rule.

Raw 489 is new and sparse. SimC commit `d0c20af205` (2026-08-20) added it as
`SX_AURA_DOES_NOT_REFRESH` and maps it to disabled buff refresh in `buff.cpp:730-744`. Trinity still
calls the bit `SPELL_ATTR15_UNK9` and has no executable consumer. None of the 36 current rows
co-occurs with raw 436, raw 43, or raw 189. The population includes old `Garrote - Silence` and
`Iron Wire`, modern combat auras, emote auras, teleport-removal trackers, and three difficulty rows
of `Gravebound`. This supports the family label but not exact retail behavior, so the disposition
remains **Partially characterized**. Missing evidence is a retail trace or independent generic
consumer that proves whether recast is ignored entirely, reapplies non-duration state, or follows a
different lifecycle.

SimC defaults raw 436 to pandemic refresh and raw 489 to disabled refresh, but also gives ticking and
nonticking buffs simulation defaults even without those flags (`buff.cpp:1635-1665`,
`action/dot.cpp:942-1010`). Those defaults are useful modeling behavior, not implicit Wago metadata.

## Terminal findings

- **Strongly verified:** duration-table lookup; Trinity's exact `-1` sentinel; Trinity's combo-point
  interpolation; `EffectAuraPeriod` as an authored aura interval; the narrow haste/initial-period
  composition; unique-aura refresh admission; and raw 436's 130% carry plus timer preservation.
- **Partially characterized:** PvP duration selection, raw 489, and client-only refresh logging.
- **Rejected interpretations:** all negatives are permanent; `MinDuration` is an aura floor;
  `EffectAmplitude` is the current Wago aura period; every authored periodic row must tick; and
  correlation with periodic auras establishes partial-tick behavior.
- **Unknown after exhaustive available evidence:** generic `EffectAmplitude` semantics outside its
  traced jump/log uses, seven varying-duration records without resource increments, exact retail PvP
  selection, and raw 489's complete lifecycle. Required evidence is a current client symbol,
  executable retail-equivalent consumer, or controlled retail application/refresh trace.

