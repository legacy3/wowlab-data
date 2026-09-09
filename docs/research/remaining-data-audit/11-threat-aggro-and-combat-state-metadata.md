# Threat, aggro, and combat-state metadata

## Scope and evidence

This report audits current threat effects and auras, threat-list targeting, threat/combat spell
attributes, combat-transition proc metadata, and combat-related interrupt flags. “Threat,” “engage,”
“combat state,” “target selection,” and “auto-attack” are kept separate because the executable
sources do not treat them as one operation.

Evidence versions are Wago retail `12.1.0.69497` at repository commit
`2ddced452a6f9076de5c86bc92f73de5b60f8556`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimulationCraft
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 scanned all current rows. Counts were
independently repeated with direct filters and grouped raw-identity censuses. Comparison-source
names are evidence only; executable consumers determine the conservative contracts below.

## Threat effects and aura operations

| Surface | Raw | Rows | Spells | Disposition |
|---|---:|---:|---:|---|
| Spell effect | 63 | 283 | 277 | **Strongly verified — add calculated flat threat to the caster's reference on the hit target** |
| Spell effect | 91 | 5 | 5 | **Partially characterized — historical/source label “Threat All,” no generic Trinity handler** |
| Spell effect | 125 | 27 | 27 | **Strongly verified — scale the caster's existing threat on the hit target by a percentage** |
| Spell effect | 130 | 10 | 10 | **Partially characterized — register percentage threat redirection; lifecycle incomplete** |
| Aura subtype | 10 | 166 | 166 | **Strongly verified — percentage threat-generation modifier selected by spell-school mask** |
| Aura subtype | 11 | 256 | 254 | **Strongly verified — taunt-priority state while applied** |
| Aura subtype | 103 | 43 | 43 | **Strongly verified — temporary flat adjustment to existing threat references** |
| Aura subtype | 183 | 10 | 10 | **Rejected threat interpretation; strongly verified as a critical-chance/health-threshold aura** |
| Aura subtype | 311 | 87 | 84 | **Partially characterized — ignore-combat family, generic operation unknown** |

### Effects 63, 125, and 130 are different operations

Effect 63 runs at hit-target time, requires a living unit caster and a target capable of owning a
threat list, then calls `AddThreat(caster, effectValue, spell, ignoreModifiers=true)`
(`SpellEffects.cpp:2948-2963`). The population's authored base points range from -100,000,000 to
500,000,000, includes 31 zeros and eight negatives, and spans many selectors. Those extremes and
scaling fields mean `EffectBasePointsF` is not necessarily the final amount; the operation consumes
the calculated effect value. Modifiers are bypassed, but the normal `AddThreat` no-threat gates,
combat engagement, and redirection layers remain. This exact layering is **strongly verified in
Trinity**.

Effect 125 calls `ModifyThreatByPercent` for the caster's reference on the hit target
(`SpellEffects.cpp:4442-4452`). Its 27 authored values range from -100,000 to 600, with 16 negative
and one zero row. Trinity uses factor `(100 + value) / 100`, clamped to a nonnegative factor
(`ThreatManager.h:147`, `ThreatManager.cpp:477-482`). It changes existing threat; it does not add
the value as flat threat. The extreme -100,000 `Re-Direct (DNT)` row is an outlier that collapses to
zero in Trinity and warns against treating every authored magnitude as a sensible retail percent.

Effect 130 has calculated value 100 on all ten rows and registers the hit target as a 100% redirect
recipient on the caster's threat manager (`SpellEffects.cpp:4963-4974`). Its `misc0` values are 0,
5,000, 30,000, 36,000, 38,000, or 3,600,000 and look duration-like, but Trinity's handler does not
read them. No generic caller of `UnregisterRedirectThreat` was found outside the declarations and
definitions (`ThreatManager.cpp:801-826`). **Partially characterized:** registration and percentage
ownership are established; expiry, replacement, and removal behavior are not. Calling `misc0` the
duration without an executable consumer is rejected. Required evidence is a retail trace or a
current lifecycle consumer.

Effect 91's five rows are heterogeneous: two transport spells at -100, a passenger DNT spell at
1,000,000, and two zero-valued event/threat rows. Trinity dispatches raw 91 to `EffectNULL`
(`SpellEffects.cpp:183`). The old “Threat All” name is insufficient to establish target ownership,
amount semantics, or whether the operation still exists. This remains **Partially characterized**,
not silently treated as effect 63.

### Aura 10: school-selected multiplicative modifier

Aura 10's calculated amount updates the holder's cached spell-school threat multipliers
(`SpellAuraEffects.cpp:3053-3061`, `ThreatManager.cpp:713-729,793-799`). It changes threat generated
by later spells and is not an immediate edit to an existing threat list. `misc0` is 127 on 129 rows,
zero on 32, 126 on three, and 1023/2588 on one each. Values 1023 (`Reduce Vol'jin Threat`) and 2588
(`Gormones`) set bits above the seven-school domain. Trinity queries only the low seven school bits,
so those higher bits have no traced meaning there. The multiplicative, holder-owned, school-mask
operation is **Strongly verified**; meanings of the high bits are **Unknown after exhaustive
available evidence**.

### Aura 103: temporary flat threat

Aura 103 is not another percentage modifier. On amount changes, if the aura target is a living
player and the caster is alive, Trinity asks the caster's threat manager to sum all aura-103 amounts
on its owner and assign that sum as `_tempModifier` to every threat reference
(`SpellAuraEffects.cpp:3063-3077`, `ThreatManager.cpp:773-790`). Effective threat is
`max(base + temporary, 0)` (`ThreatManager.h:272`). This establishes an additive temporary
adjustment and its owner/caster relationship. The current raw base points include very large scaled
values; again, final calculated amounts—not raw CSV base points—enter the operation.

### Aura 11: priority is not threat matching

Aura 11 application/removal recomputes taunt priority on the aura target's threat list. Only the
last taunt aura per caster participates, later taunts have higher priority, and taunting references
cannot be suppressed (`SpellAuraEffects.cpp:3079-3086`, `ThreatManager.cpp:504-525`). The aura
handler does not use its amount.

Threat matching is a separate effect. Of 254 spells owning aura 11, 167 also own effect 114, whose
handler calls `MatchUnitThreatToHighestThreat`; 28 own effect 63. This cross-population and
`SpellEffects.cpp:2790-2812` prove that “taunt aura sets threat equal to the leader” is too broad.
Aura 11 establishes selection priority while active; a sibling effect or script may separately
change numeric threat. That narrower identity is **Strongly verified**.

### Aura 183: stale “critical threat” identity rejected

SimulationCraft still names raw 183 `A_MOD_CRITICAL_THREAT`, but it has no executable consumer.
Trinity renamed raw 183 from that historical identity to
`SPELL_AURA_MOD_CRIT_CHANCE_VERSUS_TARGET_HEALTH` in commit `bd7c714c97` (2021) and executes it in
melee and spell critical-chance queries (`Unit.cpp:2917-2921,7257-7261`). The current ten rows carry
amount/`misc1` pairs 30/80 (eight `Initiation` spells), 50/20 (`Executioner`), and 10/50 (`Conduit of
Flame`), consistent with amount gated by target-health threshold. Therefore a threat meaning is a
**Rejected interpretation**; the current critical-chance identity is **Strongly verified**.

### Aura 311: ignore-combat family remains incomplete

Aura 311 is present on 87 rows / 84 spells; 85 have zero base points, 86 have `misc0=0`, and its
population includes `Feign Death`, `Permanent Feign Death`, `[DNT] Drop Combat`, `_JKL - No Agro`,
knockouts, immunities, stasis, vehicle/visual, and teleport contexts. Trinity labels it
`SPELL_AURA_IGNORE_COMBAT` but marks it NYI and dispatches it to `HandleNULL`
(`SpellAuraDefines.h:398`, `SpellAuraEffects.cpp:383`). SimC provides only “Ignore Combat State.”

The family/domain is useful evidence, but the population does not establish whether it prevents
engagement, clears existing combat, suppresses threat-list insertion, affects AI, or only changes a
client unit flag. It is **Partially characterized**. Required evidence is an executable client/server
consumer or controlled retail transitions covering application and removal while both in and out
of combat.

Aura 202, historically “ignore combat result,” has zero current Wago rows. Trinity's executable
consumer instead suppresses selected dodge, block, or parry outcomes according to `misc0` and class
mask (`Unit.cpp:2700-2721`). It concerns hit resolution, not combat state. Treating its name as
“ignore combat/aggro” is a **Rejected interpretation**, and it is not a current-population identity.

## Threat and combat spell attributes

Raw attribute identity is flattened as `word * 32 + bit`; the mask column is only the mask within
that source word.

| Raw | Word.bit | Mask | Rows | Spells | Conservative result |
|---:|---:|---:|---:|---:|---|
| 20 | 0.20 | `0x00100000` | 2,736 | 2,734 | Stop current auto-attack after cast — **Strongly verified** |
| 28 | 0.28 | `0x10000000` | 8,288 | 8,288 | Not usable in combat except Trinity's mounted/allow-mount exception; qualifying preparation is interrupted on entry — **Strongly verified** |
| 40 | 1.8 | `0x00000100` | 325 | 325 | Explicit target must be peaceful/not in combat — **Strongly verified** |
| 41 | 1.9 | `0x00000200` | 1,821 | 1,815 | Client metadata: initiates combat/enables auto-attack — **Partially characterized** |
| 42 | 1.10 | `0x00000400` | 171,210 | 170,152 | Suppress threat and engagement through `AddThreat` — **Strongly verified** |
| 53 | 1.21 | `0x00200000` | 25 | 25 | “Threat only on miss” label — **Unknown after exhaustive available evidence** |
| 57 | 1.25 | `0x02000000` | 1,951 | 1,943 | Preserve aura during creature evade cleanup — **Strongly verified narrowly** |
| 81 | 2.17 | `0x00020000` | 1,449 | 1,318 | Do not reset melee/ranged auto-attack timers on qualifying cast — **Strongly verified** |
| 84 | 2.20 | `0x00100000` | 167 | 167 | “Initiate combat post-cast/enables auto-attack” metadata — **Partially characterized** |
| 86 | 2.22 | `0x00400000` | 130,097 | 129,212 | Suppress threat only while threat-list owner is not yet engaged — **Strongly verified** |
| 94 | 2.30 | `0x40000000` | 532 | 531 | “Active threat” family — **Unknown after exhaustive available evidence** |
| 131 | 4.3 | `0x00000008` | 366 | 365 | Suppress helpful/assist threat — **Strongly verified** |
| 132 | 4.4 | `0x00000010` | 507 | 502 | Suppress harmful/damage initial threat — **Strongly verified** |
| 152 | 4.24 | `0x01000000` | 94 | 94 | “Auto ranged combat” metadata — **Unknown after exhaustive available evidence** |
| 156 | 4.28 | `0x10000000` | 122 | 122 | “Ignore combat timer” metadata — **Unknown after exhaustive available evidence** |
| 211 | 6.19 | `0x00080000` | 47 | 45 | Pause combat/attack-timer countdown while this cast is current — **Strongly verified in Trinity** |
| 220 | 6.28 | `0x10000000` | 224 | 224 | Do not auto-select target with initiates-combat, client-only — **Partially characterized presentation/targeting metadata** |
| 279 | 8.23 | `0x00800000` | 34 | 34 | Enforce encounter combat-resurrection charge admission — **Strongly verified** |

Raw 20 calls `AttackStop` after the spell and also participates in proc suppression around attack
handling (`Spell.cpp:2986,4446`). Raw 81 prevents the normal melee/ranged attack-timer reset
(`Unit.cpp:3212-3220`, `Spell.cpp:8312`). Raw 211 is checked while generic or channeled spells are
current and pauses the timer decrement (`Unit.cpp:452-475`). These are attack/timer mechanics, not
threat modifiers.

Raws 41 and 84 are referenced server-side only in a gate that classifies and rejects such a spell
under an “attacking disabled except abilities” restriction (`Spell.cpp:5777-5778`). No generic
server branch that actually begins combat or starts auto-attack was found. Raw 220 is explicitly
client-only. Their labels are therefore not promoted to verified server actions.

Raw 42 returns from `ThreatManager::AddThreat` before combat is established and also suppresses
assist threat (`ThreatManager.cpp:382-391,735`). Raw 86 returns only when the threat-list owner is
not already engaged: once engaged, later threat from the spell can be added. This proves the
inverted distinction “no threat” versus “no initial threat.” Raw 131 short-circuits split assist
threat and periodic helpful threat; raw 132 suppresses damage-generated threat and contributes to
`SpellInfo::HasInitialAggro` (`ThreatManager.cpp:735`, `SpellAuraEffects.cpp:5986`,
`Unit.cpp:1086`, `SpellInfo.cpp:1919-1924`).

The four suppression flags compose rather than alias. The largest exact combinations are raw
42+86 only (128,858 rows / 128,032 spells), raw 42 only (41,851 / 41,620), raw 86 only (800 / 742),
and all four (256 / 255). There are also 80 helpful-only rows and seven harmful-only rows. These
outliers reject any plan to collapse them into one `no_threat` boolean.

Raw 57's executable scope is specifically `Unit::RemoveAurasOnEvade` (`Unit.cpp:4442-4460`), not a
generic hook at every combat exit. Raw 279 checks remaining combat-resurrection charges only while
an instance encounter is in progress (`SpellInfo.cpp:2516-2526`). The scope qualifiers are part of
the identities.

No current generic consumers were found for raws 53, 94, 152, or 156. Their names and correlations
are insufficient; they remain unknown even though their spell populations are complete.

### Rejected historical SimC raw 22 label

SimC's enum token calls flattened raw 22 `SX_NO_COMBAT`, but its own display mapping now says “Track
Target in Cast (Player Only)” (`data_enums.hh:1839`, `sc_spell_info.cpp:532`). Trinity independently
uses the latter client-only identity (`SharedDefines.h:459`). Current Wago sets raw 22 on 1,722 rows /
1,717 spells. “No combat” is therefore a **Rejected interpretation** caused by a stale token name.

## Threat-list targeting is selection, not threat mutation

Implicit target raw 122 appears on 213 effect rows / 186 spells, always in `ImplicitTarget_0`.
Trinity implements it by enumerating victims from the caster's current unsorted threat list
(`SpellInfo.cpp:370`, `Spell.cpp:1408-1413`). The effect distribution is heterogeneous: effect 134
(51 rows), 140 (43), Apply Aura (27), Dummy (26), Kill Credit (18), and 13 other kinds. Neither
effect 63 nor 125 is a leading use. This is **Strongly verified — threat-list unit selection** and
explicitly not proof that the selected effect reads or writes threat.

## Combat entry/exit metadata

`SpellInterrupts.csv` has 122,150 rows / 122,099 spells. In word 0, bit 28 (`0x10000000`) is set on
3,880 aura-interrupt rows and 4,063 channel-interrupt rows; bit 31 (`0x80000000`) is set on 4,703
aura-interrupt rows / 4,701 spells and 569 channel-interrupt rows. Trinity names these EnteringCombat
and LeavingCombat (`SpellDefines.h:77-117`). SimC presents them as identities 29 and 32 because its
display is one-based; those numbers must not be confused with zero-based bit positions 28 and 31.

`Unit::AtEnterCombat` runs aura scripts, interrupts a qualifying peaceful preparation, removes
auras carrying EnteringCombat, and emits proc raw 27. `Unit::AtExitCombat` runs scripts, removes
auras carrying LeavingCombat, and updates spell history (`Unit.cpp:9215-9255`). These aura-removal
and proc-event behaviors are **Strongly verified in Trinity**. The separately populated channel
flags participate in channel interruption checks (`SpellInfo.cpp:1617`), but a complete retail
transition ordering and all channel call paths are **Partially characterized**.

Proc raw 27 itself occurs on 90 aura-option rows / 90 spells and is emitted after entering-combat
aura removal. There is no matching leave-combat proc bit in the current proc enum. Correlation
between an entering-combat interrupt flag and proc raw 27 therefore must not be treated as one
field or a symmetric pair.

## Terminal findings

- **Strongly verified:** effects 63 and 125; aura 10, 11, and 103 with the narrow operations above;
  threat-list selector 122; the behaviorally consumed no-threat variants; attack-timer controls;
  combat-resurrection admission; and Trinity's entry/exit aura-removal sequence.
- **Partially characterized:** effect 91, effect 130 lifecycle, aura 311, client-side initiate-combat
  attributes, raw 220, and channel-interrupt transition behavior.
- **Rejected interpretations:** aura 183 as critical threat; aura 202 as combat-state suppression;
  SimC raw 22 as no-combat; taunt aura alone as numeric threat matching; redirect `misc0` as proven
  duration; and all no-threat attributes as synonyms.
- **Unknown after exhaustive available evidence:** generic behaviors for attribute raws 53, 94,
  152, and 156; aura-311 application/removal semantics; aura-10 high selector bits; and redirect
  cleanup/expiry. Current client symbols, retail combat traces, or executable consumers are needed
  to resolve them.
