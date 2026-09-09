# Proc and aura-options metadata

## Scope, snapshot, and method

This report audits `SpellAuraOptions.csv`, `SpellProcsPerMinute.csv`, and
`SpellProcsPerMinuteMod.csv`, including the two-word proc-event mask and the fields that TrinityCore
loads as aura stacks, proc chance, proc charges, and proc cooldown. It is a data and comparison-source
audit, not a proposal to adopt TrinityCore's runtime contract.

Evidence versions are Wago retail `12.1.0.69497` at repository commit
`2ddced452a6f9076de5c86bc92f73de5b60f8556` (1,090/1,090 requested tables downloaded, zero
failures), TrinityCore `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimulationCraft
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned the complete CSVs. Counts were
repeated using both direct filtered aggregates and per-value grouped censuses.

The raw proc identity used below is flattened as `word_index * 32 + bit_index`. It is not the mask:
for example, raw 37 is word 1, bit 5, mask `0x00000020` in `ProcTypeMask_1`.

## Population census

`SpellAuraOptions` has 32,162 rows / 31,849 spells. The fields are populated as follows:

| Condition | Rows | Distinct spells |
|---|---:|---:|
| Either proc-mask word nonzero | 14,220 | 14,189 |
| `SpellProcsPerMinuteID != 0` | 1,987 | 1,987 |
| `ProcChance != 0` | 32,038 | 31,725 |
| `ProcCharges != 0` | 1,406 | 1,380 |
| `ProcCategoryRecovery != 0` | 3,542 | 3,517 |
| `CumulativeAura != 0` | 16,396 | 16,168 |

Mask and PPM are independent dimensions: 12,248 rows have a mask without PPM, 1,972 have both,
15 have PPM without a mask, and 17,927 have neither. An aura-options row therefore does not by
itself prove that the spell is an event-driven proc aura.

Trinity copies the fields in `SpellInfo.cpp:1383-1395`; the DB2 storage types are recorded in
`DB2Structure.h:3787-3798`. The executable proc gate is
`SpellAuras.cpp:1834-1978`: charges, cooldown, event conditions, scripts, effect eligibility,
equipment, location, caster/target state, and finally chance are separate gates. Preparation adds
the cooldown and consumes a charge; the last charge removes the aura only after effect handlers run
(`SpellAuras.cpp:1777-1831`, `2010-2032`). This ordering is **strongly verified for TrinityCore**,
but current Wago does not establish retail ordering.

## Proc event identities

All raw identities 0 through 38 are set in the current build. Trinity defines 0..34, 36, and 38 in
`SpellMgr.h:90-198`; it has no entries for 35 or 37. The ordinary attack/spell families are produced
through `Unit::ProcSkillsAndAuras` and filtered by `SpellMgr::CanSpellTriggerProcOnEvent`. Special
event producers include heartbeat (`Unit.cpp:522`), kills/death/target-dies (`Unit.cpp:11387-11400`),
jump (`MovementHandler.cpp:620`), encounter start (`Unit.cpp:565`), enter combat (`Unit.cpp:9233`),
cast-ended (`Spell.cpp:4401`), looting (`LootHandler.cpp:139` and `Player.cpp:27130`), knockback
(`SpellEffects.cpp:4073`), cast-successful (`Spell.cpp:3906`), and successful dispel
(`SpellEffects.cpp:2231,4265`).

| Raw | Word.bit | Mask | Rows | Spells | Conservative identity and disposition |
|---:|---:|---:|---:|---:|---|
| 0 | 0.0 | `0x00000001` | 55 | 55 | Heartbeat — **Strongly verified** |
| 1 | 0.1 | `0x00000002` | 563 | 562 | Actor dealt killing blow — **Strongly verified** |
| 2 | 0.2 | `0x00000004` | 3,639 | 3,627 | Deal melee auto-attack — **Strongly verified** |
| 3 | 0.3 | `0x00000008` | 2,143 | 2,133 | Take melee auto-attack — **Strongly verified** |
| 4 | 0.4 | `0x00000010` | 4,870 | 4,862 | Deal melee-class ability — **Strongly verified** |
| 5 | 0.5 | `0x00000020` | 2,306 | 2,292 | Take melee-class ability — **Strongly verified** |
| 6 | 0.6 | `0x00000040` | 1,941 | 1,938 | Deal ranged auto-attack — **Strongly verified** |
| 7 | 0.7 | `0x00000080` | 1,765 | 1,756 | Take ranged auto-attack — **Strongly verified** |
| 8 | 0.8 | `0x00000100` | 2,900 | 2,895 | Deal ranged-class ability — **Strongly verified** |
| 9 | 0.9 | `0x00000200` | 1,915 | 1,902 | Take ranged-class ability — **Strongly verified** |
| 10 | 0.10 | `0x00000400` | 1,822 | 1,820 | Deal helpful, damage-class-none ability — **Strongly verified** |
| 11 | 0.11 | `0x00000800` | 504 | 504 | Take helpful, damage-class-none ability — **Strongly verified** |
| 12 | 0.12 | `0x00001000` | 3,497 | 3,492 | Deal harmful, damage-class-none ability — **Strongly verified** |
| 13 | 0.13 | `0x00002000` | 1,987 | 1,974 | Take harmful, damage-class-none ability — **Strongly verified** |
| 14 | 0.14 | `0x00004000` | 2,528 | 2,526 | Deal helpful magic spell — **Strongly verified** |
| 15 | 0.15 | `0x00008000` | 519 | 519 | Take helpful magic spell — **Strongly verified** |
| 16 | 0.16 | `0x00010000` | 4,284 | 4,278 | Deal harmful magic spell — **Strongly verified** |
| 17 | 0.17 | `0x00020000` | 2,028 | 2,015 | Take harmful magic spell — **Strongly verified** |
| 18 | 0.18 | `0x00040000` | 2,024 | 2,024 | Deal harmful periodic event — **Strongly verified** |
| 19 | 0.19 | `0x00080000` | 1,297 | 1,296 | Take harmful periodic event — **Strongly verified** |
| 20 | 0.20 | `0x00100000` | 128 | 128 | Take any damage — **Strongly verified** |
| 21 | 0.21 | `0x00200000` | 1,210 | 1,210 | Deal helpful periodic event — **Strongly verified**; Trinity's adjacent old “trap activation” comment is rejected as stale |
| 22 | 0.22 | `0x00400000` | 51 | 51 | Main-hand weapon swing — **Strongly verified** |
| 23 | 0.23 | `0x00800000` | 41 | 41 | Off-hand weapon swing — **Strongly verified** |
| 24 | 0.24 | `0x01000000` | 727 | 727 | Aura holder dies — **Strongly verified** |
| 25 | 0.25 | `0x02000000` | 147 | 147 | Player jumps — **Strongly verified** |
| 26 | 0.26 | `0x04000000` | 2 | 2 | “Proc clone spell” family — **Partially characterized**; enum name exists but no generic producer was found |
| 27 | 0.27 | `0x08000000` | 90 | 90 | Aura holder enters combat — **Strongly verified** |
| 28 | 0.28 | `0x10000000` | 251 | 251 | Encounter starts — **Strongly verified** |
| 29 | 0.29 | `0x20000000` | 70 | 69 | Cast ends — **Strongly verified**; do not equate this with successful cast |
| 30 | 0.30 | `0x40000000` | 24 | 24 | Loot taken — **Strongly verified** |
| 31 | 0.31 | `0x80000000` | 734 | 734 | Take helpful periodic event — **Strongly verified** |
| 32 | 1.0 | `0x00000001` | 101 | 101 | Target dies, including qualifying assists — **Strongly verified** |
| 33 | 1.1 | `0x00000002` | 7 | 7 | Knockback — **Strongly verified** |
| 34 | 1.2 | `0x00000004` | 850 | 850 | Cast succeeds — **Strongly verified** |
| 35 | 1.3 | `0x00000008` | 1 | 1 | **Unknown after exhaustive available evidence** |
| 36 | 1.4 | `0x00000010` | 21 | 21 | Successful dispel — **Strongly verified** |
| 37 | 1.5 | `0x00000020` | 178 | 178 | **Unknown after exhaustive available evidence** |
| 38 | 1.6 | `0x00000040` | 11 | 11 | “Do emote” family — **Partially characterized**; enum exists but no generic producer was found |

Raw 35 occurs only on `Photo Finisher`. Raw 37 occurs on 178 spells spanning class talents,
periodic debuffs, encounter spells, and modern item drivers (`Balanced Stratagem`, `Aspect of
Harmony`, `Cataclysmic Signet Brand`, `Venomfang`, and many others). That heterogeneous population
falsifies a narrow item-only or periodic-only label. Neither Trinity nor SimC defines or consumes
either bit, and spell names cannot establish the event. Missing evidence is a current client symbol,
retail event trace, or executable producer that distinguishes them from the neighboring events.

SimulationCraft's `proc_types` (`data_enums.hh:102-143`) represents only Blizzard word-0 bits
0..23 plus special handling for word-0 bit 31 and cast-successful. It is not a complete representation
of the current 64-bit DB2 mask; absence there is not evidence that current Wago bits are irrelevant.

## Chance, cooldown, charges, and stacks

### Chance and PPM

`ProcChance` has 37 current values. Values 0..80 account for 2,031 rows; 100 has 5,893 rows / 5,870
spells; 101 has 24,111 / 23,825; 105 has two; and 109 has one. Trinity copies the unsigned byte to
the default proc entry and calls `roll_chance(float)` (`SpellMgr.cpp:1579-1582`,
`SpellAuras.cpp:1971`). `Random.h:48-72` draws on `[0,100)`, so any value at least 100 is guaranteed
in that implementation. **Strongly verified for Trinity:** the field is a percent-roll input when
RPPM does not replace it. **Rejected interpretation:** 101, 105, and 109 are not distinct probabilities
in Trinity. Their distinct retail authoring meanings remain unknown.

All 1,987 PPM references resolve and point to positive base rates; therefore Trinity's
`ProcBasePPM > 0` branch replaces the authored chance for every currently referenced PPM record.
The 1,987 rows still carry chances (1,650 are 101 and 311 are 100), demonstrating that simultaneous
population is not composition. The RPPM formula caps elapsed attempt time at 10 seconds, elapsed
success time at 1,000 seconds, applies bad-luck protection, and then clamps chance to `[0,1]`
(`SpellAuras.cpp:2034-2050`). SimC independently implements elapsed-attempt RPPM and bad-luck
protection in `sim/proc_rng.cpp:30-125`. Formula parity with retail is not established by two
community implementations sharing historical documentation.

`ProcCategoryRecovery` is zero on 28,620 rows and has 79 nonzero millisecond values. Its range is
0..9,000,000; the maximum belongs to two `Egg Thief` spells. Trinity uses it as an aura-wide proc
cooldown, subject to a proc-cooldown spell modifier (`SpellAuras.cpp:1777-1807`). This is
**strongly verified for Trinity**; current data does not prove cooldown sharing across distinct
auras or retail clock semantics.

### Charges

`ProcCharges` is zero on 30,756 rows. Nonzero common values are 1 (1,068 rows), 3 (99), 2 (62), 5
(57), 10 (37), and 4 (28). Outliers are five `-1` rows, `120` on `Petrified Bark`, and `999999` on
two `Twilight Torment` spells. Trinity's DB2 field and `SpellInfo::ProcCharges` preserve an integer,
but `Aura::CalcMaxCharges` returns `uint8` (`SpellAuras.cpp:1004-1015`): those outliers become 255,
120, and 63 respectively in that runtime. Thus the ordinary role “maximum consumable proc charges”
is **strongly verified**, while treating every authored integer literally is **rejected**. Retail
sentinel meaning and whether the extreme rows are active content are unknown.

Charges are not stacks. A server-side `spell_proc` attribute can explicitly consume aura stacks
instead of the charge counter, and a triggering spell marked not to consume resources can suppress
charge loss (`SpellAuras.cpp:1810-1831`). These server overrides are absent from Wago and prevent a
DB2-only reconstruction of every proc lifecycle.

### `CumulativeAura`

Trinity loads `CumulativeAura` as `SpellInfo::StackAmount`, and ordinary aura stack growth caps at
that value (`SpellAuras.cpp:1083-1125`). This supports **Partially characterized — authored maximum
aura-stack/cumulative-count field**. The population is heterogeneous: zero occurs 15,766 times;
frequent nonzero values include 1 (3,977), 99 (2,539), 10 (2,045), 5 (2,029), and 3 (1,332), while
the maximum is 65,000 (`In Queue`), followed by 30,000 (`Stolen Phantasma`), six 9,999 score/speed
trackers, and three 9,001 rows. Trinity stores a live stack in `uint8` even though the DB2 and
`SpellInfo` values are wider (`SpellAuras.h:238-239,411`). Therefore a universal “literal maximum
live stack count” interpretation is rejected. Missing evidence is the client/retail meaning of the
large counter-like values and whether another subsystem consumes them.

## PPM modifiers

`SpellProcsPerMinute` has 295 records, 85 distinct base rates, range 0..50. The two zero-rate records
are unreferenced; all 1,987 aura-option references resolve. Fifty-four PPM records are unused by
current aura options. `SpellProcsPerMinuteMod` has 821 rows across 209 PPM IDs, with no orphan
reference:

| Type | Conservative identity | Mod rows | PPM IDs | Distinct params | Coefficient range | Disposition |
|---:|---|---:|---:|---:|---:|---|
| 1 | haste scaling | 159 | 159 | 4 | 1..2.5 | **Strongly verified** |
| 2 | critical-strike scaling | 9 | 9 | 2 | 1..3.25 | **Strongly verified** |
| 3 | caster class-mask conditional | 58 | 56 | 17 | -0.5..1.5 | **Strongly verified** |
| 4 | player specialization conditional | 573 | 75 | 37 | -0.75..2 | **Strongly verified** |
| 5 | caster race-mask conditional | 1 | 1 | 1 | 0.5 | **Strongly verified** |
| 6 | item-level scaling relative to parameter level | 2 | 2 | 1 | 1 | **Strongly verified** |
| 7 | battleground/arena conditional | 6 | 6 | 1 | -1..-0.75 | **Strongly verified in Trinity** |
| 8 | caster-has-aura conditional | 13 | 10 | 13 | -0.5..1.5 | **Strongly verified** |

For each applicable row Trinity multiplies PPM by `1 + adjustment` in
`SpellInfo.cpp:4325-4414`; multiple applicable rows compose multiplicatively. Type 7 is checked
against `IsBattlegroundOrArena`, which is executable evidence stronger than SimC's
`RPPM_MODIFIER_UNK_ADJUST` label. SimC handles haste/crit and types 3, 4, 5, 6, and 8 in
`sc_const_data.cpp:1375-1433`, but currently omits type 7. The item-level formula depends on random
property points rather than a direct item-level percentage.

The PPM table's `Flags` population is 0 on five records, 1 on 285, and 3 on five. Neither Trinity's
spell construction nor SimC's current RPPM path reads these flags. The meanings of bits 0 and 1 are
therefore **Unknown after exhaustive available evidence**. An enum name alone was not invented.

## Conclusions and unresolved evidence

- **Strongly verified:** two-word proc masks are event filters; chance, positive referenced RPPM,
  aura-wide internal cooldown, ordinary charge counters, and PPM modifiers have the narrow runtime
  roles described above.
- **Partially characterized:** raw proc 26 and 38, `CumulativeAura` across its extreme counter-like
  population, and extreme/sentinel charge values.
- **Rejected interpretations:** raw 21 as generically “trap activation”; values over 100 as distinct
  Trinity chance percentages; PPM plus chance as two simultaneously applied chances; every
  `CumulativeAura` value as a representable byte-sized aura-stack cap.
- **Unknown after exhaustive available evidence:** proc raws 35 and 37, PPM `Flags` bits 0 and 1,
  and the retail meanings of the extreme stack/charge encodings. Retail traces, current client
  symbols, or an executable consumer would resolve them.
