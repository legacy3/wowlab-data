# Selected-passive package generalization

## Scope and result

This report answers one question about the stable Core tree at `b1714eda`:

> Core admits a growing set of selected passive trait packages. Its downstream representation is
> already structural, but source recognition and same-actor package validation still contain
> package- and family-specific knowledge. **Is there a compact, source-backed, generic admission
> model that explains every currently proved package without naming individual talents — and that
> still rejects packages whose source, lifecycle or ownership semantics are not proved?**

This is research only. It does not modify Core, choose a Core milestone, or design the production
refactor. Where Core vocabulary appears it is a mapping, not a proposal.

The machine-readable corpora in [`selected-package-corpora/`](selected-package-corpora/) and the
oracle (`scripts/research/selected_package.py`) hold the reproducible detail. Every section names
the command that regenerates its numbers.

    current client data      != content supported by pinned Trinity
    no Trinity consumer      != no Retail behaviour
    structural similarity    != semantic equivalence
    Core's admitted set      == the ground truth a candidate rule may not contradict
                                unless the disagreement is adjudicated with evidence

---

### Headline

**Yes — for the source side, and the rule is smaller than expected.**

A rule with **two dimensions** — *every exact effect of the complete provider package classifies
into an ordinary Core semantic role*, and *the owner permits immutable passive preparation* —
reproduces Core's admitted selected-passive population with **no package names anywhere**, and with
**four disagreements, every one of which is adjudicated as a defect in Core rather than in the
rule**. Three further evidence-backed dimensions narrow its over-admission without adding a fifth.

The result decomposes into six findings.

1. **`selected_trait_package_contract` is not Core's only selected-passive path, and the other one
   is bigger.** Twelve aura subtypes reach a selected activation, across seven files. Sole-effect
   packages are admitted by the ordinary authority that owns their subtype and never build a package
   contract at all; only multi-effect packages reach the contract. Core's true admitted population
   is the **union** of the two paths: **204 entry×rank variants** (172 entries, 129 providers), of
   which 166 come through the contract path, 184 through the sole-effect path, and 146 through both.
   **All of Core's package hardcoding is concentrated in multi-effect composition** — nine branches
   in one file. That is a much smaller target than the mission assumed. (§4, §5)

2. **Core cannot currently over-admit, and that is load-bearing.** Its branches destructure the
   *complete* source effect slice with fixed-size array patterns, so an unrecognised sibling makes
   the pattern fail rather than the effect disappear. **1,154 of 8,846 entry×rank variants** (1,001
   entries, 620 providers) have at least one recognisable effect *and* at least one unrecognisable
   sibling — and **543 of those are same-authority**, where the dropped sibling is simply another
   spell modifier of the same passive. Any generalization that turns Core's exhaustive patterns
   into a filter over recognised effects admits all 1,154. This is the single largest hazard in the
   refactor. (§8)

3. **Effect order does not generalise.** Core assigns roles positionally, and its own branches
   already disagree about the order — Heart slots `[AutoAttackDamage, …]`, the auto-attack branch
   slots `[SpellModifier, AutoAttackDamage]`. Attuned to the Dream 376930 carries Ephemeral Bond's
   exact role multiset with `HealingReceived` **last**. Roles must be assigned per effect from that
   effect's own facts. (§8)

4. **The named literals cost Core one real package, and hide three inconsistencies.** Exactly one
   false positive is refused on identity alone: **entry 112197**, which resolves to the *same
   provider, same effect, same one rank, same unshaped 5.0, same family* as Furious Blows and is
   rejected only because `passive_melee_auto_attack_speed.rs` pins entry 116954 and definition
   121966. Separately, `is_ephemeral_bond_package` pins only the *provider spell*, so Core admits
   Ephemeral Bond's **detached, host-unselectable** twin entry 112613 while Heart's equivalent is
   fail-closed; the `auto_attack_damage` branch admits **nothing** because a school test with no
   semantic content excludes its only real instance (Precision Strikes); and the same owner
   attribute blocks a spell-modifier package while passing a critical-chance package. (§9)

5. **Provider-spell identity is not sufficient, and Core's state side uses nothing else.** The
   minimal non-lossy identity is **`(entry, rank)`** — zero collisions over all 8,846 variants,
   against 424 for `(provider)` and 211 for `(provider, rank)`. Decisive witness: provider
   **117216** with identical authored points `(4.0, 10.0)` resolves to `(7, 5)` through entry 80287
   (definition 85290, `Set`) and to `(4, 10)` through entry 124795 (definition 129633, `Multiply`).
   Both state-side atomicity checks key on the provider `SpellId` alone. (§10)

6. **Five Core defects were found by running the rule against Core**, not by reading it (§13).

**Score.** Final candidate rule **v5**: admits 368 against Core's 204, with **169 false positives**
— 87 likely-legitimate generalizations and 82 unreviewed multi-effect compositions — and **5 false
negatives**, of which 1 is adjudicated against Core and **4 are contested**, meaning the evidence
does not settle which side is wrong and the case stays open. Restricted to entries a host can
actually select, the score is **274 against Core's 144, with 133 false positives and 3 false
negatives**. (v1, the two-dimension rule, admits 410 with 210 false positives.) Named exceptions
that survive on evidence: **zero**.

## 1. Pins

| thing | pin |
| --- | --- |
| Core audited | `/home/dev/pallet/core` @ `b1714eda2b4b9393853f94c6517e78cce2dfa21a`, tree clean, **never modified** |
| wowlab-data | branch `research`, base `65da0d7` |
| client data | `data/tables/` — 1,090 DB2 CSV exports, 74 of them spell-keyed |
| Trinity | `/home/dev/pallet/TrinityCore` (pinned consumer) |
| world DB | the overlay corpora under `docs/research/procs-corpora/` and `docs/research/dummy-corpora/` |

Evidence classes used throughout: `source` (a DBC row), `core` (`file:line` in Core), `trinity`
(`file:line` in TrinityCore), `world` (a world-DB overlay row), `probe`, `inferred` (stated as
such), `unknown` (with a reopen condition).

## 2. Method

The oracle is a **port, not a reimplementation**. `selected_package/provenance.py` mirrors
`TraitSourceCatalog::try_selected_spell_effect_amounts` refusal-code for refusal-code, and
`selected_package/corebaseline.py` transcribes Core's admission branches so a candidate rule is
scored against what Core *actually compiles*, not against what its cold contract would accept.

Two method decisions matter for reading the numbers.

**Core's effective admission is the union of two paths, each fully proved.**
`selected_trait_package_contract` is a *cold source contract*; final `CombatProgram` compilation
independently re-proves the owner (`validate_owner`, `validate_owner_attributes`,
`selected_trait_owner_policy_issue`). Scoring against the contract alone reports **285** admitted
variants; scoring against the real contract pipeline reports **166**. But a single-effect selected
package can also be admitted by the ordinary authority compiler that owns its aura subtype, without
any package contract — that path admits **184**. The two overlap on 146, so **Core's admitted
population is 204**. Every number in this report uses 204. Getting this wrong in either direction
inflates or deflates the false-positive count by tens of packages, so it is stated explicitly.

**Core's catalogs are read out of Core, not retyped.** `selected_package/coreref.py` parses
`crates/dbc/src/aura_subtype.rs` (186 subtypes: 100 implemented, 60 disabled, 20 unimplemented,
6 ignored) and `crates/dbc/src/spell_attribute.rs` (126 attributes: 29 implemented, 23 ignored,
41 disabled, 33 unimplemented), and **fails closed** if either catalog stops covering its enum.
The oracle therefore cannot drift from Core's own declaration of what is implemented.

## 3. The population

    python3 selected_package.py census

| | |
| --- | --- |
| `TraitNodeEntry` rows | 17,139 |
| entry×rank variants examined | 16,752 |
| **resolvable entries** | **8,279** |
| **resolvable entry×rank variants** | **8,846** |
| distinct provider spells | 5,143 |
| source effects across resolved packages | 16,639 |

Source refusals: `MissingSpell` 7,089 · `UnsupportedDefinition` 306 · `UnsupportedEffectPoints` 299
· `MissingDefinition` 168 · `UnsupportedCurvePoints` 44.

The 8,279 figure was derived independently three times (lead, track I, track E) from the same rules
and agreed exactly.

### Effect-count distribution

1 effect 4,864 · 2 effects 2,139 · 3 effects 1,008 · 4 effects 397 · 5–9 effects 400 ·
10+ effects 38. **Packages larger than Core's four-slot ceiling are not rare.**

## 4. What Core admits today, measured

    python3 selected_package.py admitted

**204 entry×rank variants**, 172 distinct entries, 129 distinct providers, across 17 branches —
nine in the package contract, eight sole-effect authorities:

| branch | variants | pins an identity? |
| --- | ---: | --- |
| `generic_single_effect` | 117 | no |
| `ranked_direct` | 20 | no |
| `ranked_flat_cooldown` | 16 | no |
| `sole:PassiveDamageModifier/IncomingHolder` (raw 87) | 14 | no |
| `sole:CriticalChanceModifier/SourceSpellAndWeapon` (raw 290) | 8 | no |
| `sole:PassiveHasteAll` (raw 193) | 7 | no |
| `ranked_direct_and_periodic` | 4 | no |
| `sole:PassiveDamageModifier/OutgoingHolder` (raw 79) | 3 | no |
| `ephemeral_bond` | 2 | **yes** — provider spell |
| `heart_of_the_crusader` | 2 | **yes** — entry, definition, spell, amounts, AuraOptions literal |
| `improved_vivify` | 2 | **yes** — entry, spell, label, amount ladder |
| `phalanx` | 2 | **yes** — entry, definition, spell, label, class-mask words, AuraOptions literal |
| `sole:HealingReceived` (raw 118) | 2 | no |
| `sole:PassiveAbsorbReceived` (raw 422) | 2 | no |
| `martial_expert` | 1 | **yes** — entry, definition, spell, bitwise amounts |
| `sole:CriticalChanceModifier/TargetCasterSpellMagic` (raw 308) | 1 | no |
| `sole:MeleeAutoAttackSpeed` (raw 319) | 1 | **yes** — entry, definition, spell, bitwise amount |
| `auto_attack_damage` | **0** | no — but see §9 |

**60 of the 204 admitted variants are on detached entries** — entries with no
`TraitNodeXTraitNodeEntry` row, therefore not selectable by any host. Core admits them because it
never consults tree topology. (Track I: 3,038 detached entries exist, 2,143 of them pass every DBC
gate Core applies.)

### The two admission paths

Following `try_selected_spell_effect_amounts` through `crates/combat/src/program/` finds that the
package contract is one of **two** selected-trait admission shapes, and that four plausible entries
in it are not actually reachable:

| file | aura subtypes with a reachable selected activation |
| --- | --- |
| `passive_spell_modifier.rs` | 107, 108, 218, 219 — three sole-effect paths (`generic_spell_modifier`, `ranked_direct_contract`, `ranked_cooldown_contract`) **plus** the package contract |
| `passive_haste.rs` | 193 |
| `critical_modifier.rs` | 290, 308 sole-effect; **334 package-routed only** |
| `passive_damage_modifier.rs` | 79, 87 |
| `passive_absorb_received.rs` | 422 |
| `passive_healing_received.rs` | 118 |
| `passive_melee_auto_attack_speed.rs` | 319 — **named** (Furious Blows) |
| `passive_auto_attack_damage.rs` | 344 — **package-only**, no sole-effect path |
| `critical_block_amount.rs` | 638 — **no selected branch at all**; raw 638 reaches a selected package only as Martial Expert's second effect |

The sole-effect authority set is therefore exactly **{79, 87, 107, 108, 118, 193, 218, 219, 290,
308, 319, 422}** — twelve subtypes across seven files. Subtypes 52, 57, 183, 187 and 197 appear in
`critical_modifier.rs` but its `validate_activation` (`:1059-1070`) admits a `SelectedTrait`
activation only for 290/308/334, so they are **unreachable from a trait**; no sole-effect selected
package exists for any of them in the population either.

**184 of Core's 204 admitted variants come through this path** (107: 65 · 108: 65 · 219: 14 ·
87: 14 · 290: 8 · 193: 7 · 79: 3 · 218: 2 · 118: 2 · 422: 2 · 308: 1 · 319: 1), and every
sole-effect witness Core names in its own `aura_subtype.rs` prose is among them — Psychic Voice,
Static Charge, Improved Conjuration, Vital Clarity, Furious Blows.

**The source side is therefore already generic for sole-effect packages**, and the refusals there
are overwhelmingly owner-policy, not amounts: only **12 of 4,680** sole-effect refusals are
amount-only near-misses, and each is a data fact (a `PvpMultiplier` of 0.667 or 0.5, a live
`EffectTriggerSpell`, a positive flat cooldown) rather than a shaping disagreement.

### The sole-effect authorities disagree with each other for no reason

Comparing the twelve rules side by side (track G2) finds six disagreements with no semantic content:

- **The entire shaping clause is vacuous.** Provenance already forces one rank ⇒ unpointed and two
  ranks ⇒ exactly one `Set|Multiply` row. **Zero violations in 4,864 sole-effect variants.** Five
  different spellings of a tautology.
- **`points = rank_one` (193, 422) contradicts `points = authored` (79, 87, 118).** Applied
  uniformly, `rank_one` rejects **20 currently-admitted packages, including Core's own named Static
  Charge witness** (authored −15,000, rank-one −10,000). A latent contradiction, not a style choice.
- **79 requires `max_ranks == 1` while 87, five lines away, accepts 1 or 2**; **422 requires exactly
  2 while 118 requires exactly 1.**
- **87 and 193 accept `Set` only**, where the spell-modifier gates accept `Set|Multiply`.
- **The Physical-school test is applied by 5 roles and not by the other 7.** Applied uniformly it
  would lose **66 of the 184**; admitted owners carry school masks 1, 2, 4, 8, 16, 28, 32, 64 and
  106. Core itself states the source school is not a filter for haste (`aura_subtype.rs:481`).
- **Only 3 of 12 rules check `supports_unconditional_passive_application_policy`** — yet all 184
  admitted packages satisfy it. A free tightening. Same for empty exact-effect attributes.
- **`critical_modifier.rs` performs no owner-identity check at all** (no defense, dispel, mechanic
  or class-mask test) and **no sign or range rule**: a raw-290 selected passive with −5,000 points
  would compile.

Only the sign and range intervals are justified — each is a statement about what the consumer
represents. A **minimal uniform sole-effect rule** is therefore: structure (sole effect,
`max_ranks ∈ {1,2}`) + one neutral exact-effect shell + one owner shell + **four per-role facts**
(the `(subtype, misc0)` role key, the selector polarity, the sign/range interval, and the two-rank
operation whitelist). It admits 181 against Core's 184, differing on 5 of 4,864 variants — four
losses that exist only because of the attribute inconsistency in §13/D5, and one gain that is the
Furious Blows twin. Both differences are defects, not semantics.

## 5. The semantic dimensions

    python3 selected_package.py authorities

Per-effect classification (`selected_package/effects.py`) is a function of semantic facts only —
effect kind, aura subtype, misc operation and secondary misc, class-mask emptiness, period,
trigger, mechanic, radii, implicit targets, coefficients, chain shaping and exact-effect
attributes. It never reads an entry, definition, provider or label id.

Roles reachable today:

| family | roles |
| --- | --- |
| spell-property modifier | `Percent/{Direct,Periodic,Cooldown,CriticalBonus}/ClassMask`, `Percent/{Direct,Periodic}/Label`, `PercentFirstEffect/Label`, `FlatFirstEffect/{ClassMask,Label}`, `FlatCooldown/ClassMask` |
| passive stat authority | `AutoAttackDamage` (344), `AutoAttackCriticalChance` (334), `HealingReceived` (118), `CriticalBlockAmount` (638), `HasteAll` (193), `AllCriticalChance` (290), `CriticalChanceForCasterWithAbilities` (308), `MeleeAutoAttackSpeed` (319), `DamageDonePercent` (79), `DamageTakenPercent` (87), `AbsorbReceivedPercent` (422) |

Five subtypes that `critical_modifier.rs` compiles — 52, 57, 183, 187, 197 — are **not** roles,
because `validate_activation` (`critical_modifier.rs:1059-1070`) admits a selected-trait activation
only for 290, 308 and 334. An earlier draft of the classifier listed them, which would have
classified nine exact effects Core has no selected path for; hostile review 1 caught it. The most
instructive is raw 52, which sits in Trinity's `isTriggerAura[]` (`SpellMgr.cpp:1724`) — exactly the
proc-triggering property this pass treats as disqualifying.

Two of these carry a **selector** as well as a value: raw 79 and raw 87 read `EffectMiscValue_0` as
a `SpellSchoolMask`, so the role is `DamageTakenPercent/SchoolMask` and the mask is part of the
role rather than a value required to be neutral. Raw 118 is deliberately *not* one of them — Core's
received-healing contract requires the all-schools mask 127 exactly.

Raw 422 is also excluded, and the reason stated in an earlier draft — that it has no Trinity
consumer — was **wrong**. It does: `SPELL_AURA_MOD_ABSORB_TAKEN_PCT` is consumed by
`Unit::SpellAbsorbBonusTaken` (`Unit.cpp:7715`). The conclusion survives and gets *stronger*: that
consumer applies the aura through `GetTotalAuraMultiplier`, which **never reads the misc value at
all**. So raw 422's misc is not a school selector, its one admitted witness carries 0, and the
oracle requires 0 and fails closed on anything else.

All five reference packages classify with **zero package names**:

| package | complete effect set → roles |
| --- | --- |
| Improved Vivify 101510 | `Percent/Direct/ClassMask` + `Percent/Direct/Label` |
| Martial Expert 117409 | `Percent/CriticalBonus/ClassMask` + `CriticalBlockAmount` |
| Heart of the Crusader 115483 | `AutoAttackDamage` + `AutoAttackCriticalChance` + `Percent/Direct/ClassMask` + `Percent/CriticalBonus/ClassMask` |
| Phalanx 137000 | `Percent/Direct/ClassMask` + `Percent/Direct/Label` |
| Ephemeral Bond 136808 | `HealingReceived` + `PercentFirstEffect/Label` ×2 |

Note that Improved Vivify and Phalanx produce the **same role multiset**. Core has two separate
named branches for them; they differ only in rank shaping (`Set`+unshaped versus `Multiply`
+`Multiply`). One generic rule covers both.

### The modifier space is a free cross product, and Core's classifier is not

`SelectedSpellModifierValue` (`selected_trait_package.rs:95-140`) gives every value variant an
independent `selector`, and `Percent` an independent `operation`. The translation to the downstream
authority maps the selector **generically for every variant**
(`passive_spell_modifier.rs:776-777`), and applicability consumes both selectors uniformly
(`:698-702`). The representation is a free cross product of
`{raw 107, 108, 218, 219} × {operation 0, 3, 11, 15, 22}`.

`generic_spell_modifier` is much narrower: it covers raw-108 operations 0/22/11, raw-107 operations
3/11, raw-219 operation 3 and raw-218 operation 22 — exactly the cells the named branches needed. It
does not cover raw-108 operation 15 (`CriticalBonus`), raw-218 operation 0, or raw-218 operation 3,
although Core *compiles* all three today inside named branches.

**Hostile review 2 established that this restriction is overfitting, and measured its cost.** The
decisive test — would the table have been written this way if the five reference packages did not
exist? — fails: the missing cells are precisely the unused ones. Restoring the cross product costs
nothing in fidelity (all five reference packages classify identically) and admits **18 further
owner-clean packages that nothing else refuses**: 14 on raw-219 operation 11 (e.g. entry 91443 /
provider 387972, Teachings of the Satyr) and 4 on raw-218 operation 15 (e.g. entry 96323 / provider
390166, Plague Mastery). The candidate rule therefore uses the cross product, and `effects.py`
retains the narrow set only to *report* how much of it Core's classifier currently reaches.

## 6. The candidate rule and the falsification ledger

    python3 selected_package.py ledger

| rule | dimensions added | admits | FP | FN | verdict |
| --- | --- | ---: | ---: | ---: | --- |
| **v1** | complete-effect classification; owner policy | 410 | 210 | 4 | retained |
| v2 | rank amounts finite | 410 | 210 | 4 | **vacuous** — see below |
| *v2x* | *sibling shaping uniformity* | 396 | 198 | 6 | **FALSIFIED** |
| v3 | percentage floor ≥ −100; exact-i32 negative cooldown flats | 402 | 202 | 4 | retained |
| v4 | effective proc policy | 392 | 193 | 5 | retained |
| **v5** | four-effect ceiling; unique non-modifier authority | 368 | 169 | 5 | **final** |
| *v5x* | *unique provider identity* | 185 | 73 | 92 | **measurement, not a rule** |

### One dimension is measured vacuous

**v2's `rank_amounts` dimension never fires over the whole population.** Source resolution already
refuses a non-finite result (`SelectedTraitPointOperation::apply`, `selected_spell.rs:82-91`), and an
unshaped effect keeps its authored binary32 base points, which are finite by construction. The
dimension is retained in the ladder only so the redundancy is a *measured* fact, and
`test_sp_a_oracle.py` asserts it stays vacuous — if source resolution ever stops guaranteeing it, the
test fails loudly rather than the rule silently losing a guard.

**And one more is vacuous at the margin.** `role_uniqueness` — "no two effects of one package may
claim the same non-modifier authority" — *does* fire, on 15 variants, but every one of them is
already refused by another dimension, so removing it from v5 changes no admission. Hostile review 2
found this; half of v5's stated delta over v4 is the effect ceiling alone. The dimension is retained
because a future widening (raising the ceiling, adding an authority) could make it bite, but it must
not be counted as part of v5's delta, and a test now asserts the marginal-zero property.

This is the same class of result as track G2's finding that the sole-effect authorities' shaping
clause is vacuous (zero violations in 4,864 variants, §4). **The final rule is therefore four
load-bearing dimensions**: complete-effect classification, owner policy, amount floors, effective
proc policy, and the four-effect ceiling — with rank-amount finiteness and non-modifier role
uniqueness both proved redundant on the current population.

**v1 already reproduces Core's admitted population up to four adjudicated disagreements.** That is
the central positive result: the two dimensions the mission hypothesised as the hard part — source
provenance and per-effect semantic classification — are sufficient, with no package, entry,
definition, spell or label literal anywhere in the rule.

### Every disagreement is adjudicated individually

A false negative means the rule refuses something Core admits. That is not automatically a defect in
the rule — it can equally mean Core admits something it should not. The ledger therefore carries a
per-case adjudication with a `fault` field, and an **unadjudicated** false negative falsifies the
dimension by default.

| case | fault | why |
| --- | --- | --- |
| Improved Vivify 101510, ranks 1–2 (v2x only) | **rule** | mixed shaped/unshaped siblings are legitimate authoring — see below |
| Pyrogenics, entry 115942 (v4, v5) | **core** | proc- and script-driven, admitted as immutable (§13/D1) |
| raw-290 provider 378004, 4 variants | **core** | cross-compiler attribute inconsistency (§13/D5) |

### The falsified dimension

**v2x — "all siblings share one shaping operation" — is refuted by Improved Vivify.** Its effect 1
is `Set`-shaped `40 → 20/40` while effect 2 keeps its authored `40` unshaped, so at rank 1 the two
siblings carry *different* values (20 and 40). Core admits it. Mixed shaping is therefore legitimate
source authoring, and any rule requiring uniformity contradicts Core's own population.
Evidence: `TraitDefinitionEffectPoints` row 21888, curve 62007 `[(1,20),(2,40)]`.
Population-wide: 7,712 unshaped · 376 Set-only · 180 Multiply-only · **11 mixed Set+Multiply** · of
567 shaped entries, **164 have mixed shaped/unshaped siblings**. §7 gives a second, independent
reason this dimension must stay dead.

### The score a player would see

60 of Core's 204 admitted packages, and 94 of the rule's 368, sit on **detached** entries — entries
with no `TraitNodeXTraitNodeEntry` row, which `TraitMgr::IsValidEntry` (`TraitMgr.cpp:822-836`)
resolves through a node and therefore cannot select. Core admits them because it never consults tree
topology. Hostile review 1 asked for both scores, and both belong here:

| | Core admits | rule admits | FP | FN |
| --- | ---: | ---: | ---: | ---: |
| all resolvable entries | 204 | 368 | 169 | 5 |
| **host-selectable entries only** | **144** | **274** | **133** | **3** |

    python3 selected_package.py reachable --rule v5

The reachable score is the one that describes gameplay; the full score is the one that describes the
compiler's exposure. Neither is the "real" number on its own.

### The measurement

**v5x is not a candidate rule.** It adds "the provider must identify exactly one resolvable entry"
to size the state-side exposure. It loses **92 of Core's own 204 admitted packages** — which is
precisely the point: Core's state side keys package identity on the provider spell alone, so for
those 92 it cannot distinguish one package from another. See §10.

## 7. False positives, classified

    python3 selected_package.py false-positives --rule v5

All 169 are classified individually; none is dismissed in bulk.

| class | count |
| --- | ---: |
| likely-legitimate generalization | 87 |
| unreviewed multi-effect composition | 82 |

By the dimension on which the package departs from the nearest Core branch:

| axis | count | reading |
| --- | ---: | --- |
| role multiset has no Core composition | 113 | Core has no generic multi-effect rule at all |
| rank-domain + shaping | 28 | the same semantics at a rank count or shaping Core did not code |
| sole-authority amount/shape refusal | 19 | the authority compiler's own sign, range or `PvpMultiplier` rule |
| rank-shaping only | 8 | `Multiply` where Core coded `Set` |
| **identity pin** | **1** | Furious Blows' twin, entry 112197 |

**Exactly one false positive is refused on identity alone.** Entry 112197 (definition 117202)
resolves to provider **390354, effect 1** — the *same provider and effect as Furious Blows* — at one
rank, unshaped, authored and effective amount bitwise 5.0, owner family 4, no `SpellAuraOptions`,
zero blockers on every identity-free check. `passive_melee_auto_attack_speed.rs` refuses it solely
because `validate_selected_source` pins `FURIOUS_BLOWS_ENTRY = 116954` and
`FURIOUS_BLOWS_DEFINITION = 121966`. **The name is not load-bearing; it costs a real package.**
(The third raw-319 candidate, entry 115165 / provider 403509, is genuinely different — two ranks,
`Set`-shaped, family 10, `SpellAuraOptions` present — and the identity-free shell already refuses
it on those facts. That is the honest control for this claim.)

### The clearest generalization: direct + periodic

`is_ranked_direct_and_periodic_package` requires a two-rank package with **both** effects `Set`
-shaped. In the population that refuses:

- **26 one-rank direct+periodic packages** — identical semantics, no rank shaping at all. They are
  refused only because `single_rank_contract` has no multi-effect branch.
- **8 two-rank `Multiply`-shaped direct+periodic packages** — refused although `Multiply` is
  explicitly accepted by `supports_ranked_direct_source` for the single-effect case.

Neither refusal has a semantic justification. 34 packages, one missing branch.

### The four-effect ceiling

25 false positives are packages whose every effect classifies and whose owner is clean, but which
carry 5–8 effects. (Track E counts 438 variants over the cap in the whole population, **26 of them
otherwise clean** — 22 entries, 8 providers; the small difference is one variant the owner dimension
rejects.) The largest recurring shape is Barbaric Training 383082:
`Direct/ClassMask + CriticalBonus/ClassMask` repeated four times, 16 variants.

`MAX_PACKAGE_EFFECTS = 4` (`selected_trait_package.rs:26`) is a fixed-array storage bound, and
**all 26 clean over-cap packages are homogeneous `SpellModifier`** — they compile to one activation
per exact effect and bind through `passive_spell_modifier_package_effects`, neither of which is
arity-bounded, so the mixed-authority machinery is never reached. Nothing breaks at five.

Two things do change above four, and they matter for how the rule is phrased. Rank shaping can never
be total, because `MAX_RANK_SHAPED_EFFECTS` is also 4 — so 8 of the 26 are *structurally forced*
into the Improved Vivify mixed-shaping shape, which is a second, independent reason the falsified
uniformity dimension must stay dead. And role repetition becomes normal (Barbaric Training repeats
one role key four times), which kills any "at most one effect per role" phrasing of the rule.

## 8. Complete package topology and the over-admission hazard

    (track E) docs/research/selected-package-corpora/over-admission.json

The mission's constraint — *the generic rule must never become "all effects I happened to recognise
are supported" when an unrecognised sibling exists* — is the most important safety property here,
and the population is unforgiving.

| | variants |
| --- | ---: |
| fully recognised (every source effect owned) | 789 |
| **over-admitted by a recognised-effects-only rule** | **1,154** |
| fully unrecognised | 6,903 |
| total | 8,846 |

1,001 entries and 620 providers are implicated. What blocks the sibling (a package is counted once per
distinct blocking group, so the column sums past 1,154):

| blocking group | occurrences |
| --- | ---: |
| aura implemented in Core but with no selected-passive semantic | 661 |
| aura `unimplemented` in Core's catalog | 489 |
| aura `disabled` in Core's catalog | 243 |
| non-aura effect kind | 131 |
| aura implemented but with a non-neutral exact-effect shell | 34 |
| aura `ignored` | 25 |
| aura absent from Core's catalog entirely (build skew) | 13 |

**543 of the 1,154 are *same-authority*** — at least one unrecognised sibling sits on raw
107/108/218/219 with an unowned `SpellModOp` (1, 7, 12, 14, 19, 23, 32); for **370** of them *every*
unrecognised sibling does. The dropped effect is not exotic data — it is another spell modifier of
the same passive. Witnesses: Blessing of Renewal 9968, Arcane Power 80190, Siphon Storm 80210, Icy
Veins 80235, Flame Accelerant 80267. A filter that keeps "the modifiers I understand" silently drops
the rest of the same authority's contribution.

> These counts are a function of the classifier, not just the data: earlier drafts of `effects.py`
> gave 1,138/715 and then 1,151/755 as the school-mask selector, the trigger-inert set and the
> modifier cross product landed. Both corpora
> record a `provenance.substrate` digest of every module the numbers depend on, so a figure can
> always be tied to the code that produced it.

**Core is currently immune to this** because its branches destructure the complete source slice
with fixed-size array patterns — an unrecognised sibling makes the pattern fail rather than the
effect vanish (`selected_trait_package.rs:351, 388, 420, 457`), and `IncompleteEffects` guards
ingestion retention (`selected_spell.rs:263-266`). **The hazard is created only by replacing those
exhaustive patterns with a filter.** Any generic implementation must keep the "classify the
complete set, reject on any unclassified sibling" shape that `effects.py::classify_package` uses.

### Effect order does not generalise

Core's branches assign roles **positionally** (`let [healing, first, second] = resolved.effects()`,
`selected_trait_package.rs:351, 388, 420, 457`), and its own branches already contradict each other:
Heart slots `[AutoAttackDamage, …, SpellModifier]` while the single-rank auto-attack branch slots
`[SpellModifier, AutoAttackDamage]`. Eight role multisets occur in two different source orders, and
**Attuned to the Dream 376930** (entries 87699 and 115600) is Ephemeral Bond's exact multiset with
`HealingReceived` **last**. A positional rule would misclassify it.

**Roles must be assigned to each effect from that effect's own facts, never from its slot.** That is
how `effects.py::classify` works and it is a hard requirement on any replacement.

Other topology facts: 328 packages have mixed shaping, 118 mix `ClassMask` and `Label` selection,
78 mix a spell-modifier role with a non-modifier authority, 212 repeat a role, and 41 have a
non-canonical role order.

## 9. Named cases, and what they actually buy

Track F inventoried **242 decision points** across 19 Core files: 124 semantic-family predicates,
**53 exact special cases**, 47 package-level semantics, 15 redundant under generic classification,
3 temporary exact gates. The 53 special cases pin **17 distinct identities**.

Testing whether the names exclude anything, by searching the population for every package with the
same role multiset as a named branch:

| named branch | source-clean packages of that shape | admitted | excluded |
| --- | --- | --- | --- |
| Improved Vivify / Phalanx | 4 | 4 | 0 |
| Heart of the Crusader | 3 | 2 | **1** (entry 115441) |
| Martial Expert | 1 | 1 | 0 |
| Ephemeral Bond | 2 | 2 | 0 |
| auto-attack damage | 1 | **0** | 1 |

Three things follow.

**The one real exclusion is intentional.** Heart's detached twin — entry 115441, definition 120453,
same provider 406154, same four-effect shape, but one rank and unshaped — is excluded by the pinned
definition. Core's own catalog documents it: *"detached entry 115441 and definition 120453 …
remain fail closed"* (`aura_subtype.rs`, `ModAutoAttackCritChance`).

**The Ephemeral Bond pin is inconsistent with it.** `is_ephemeral_bond_package` pins only the
*provider spell* (`exact_source.rs:108-120`), so Core admits **both** Ephemeral Bond entries —
including **112613, which is detached** and therefore not selectable by any host. Two structurally
identical situations, opposite outcomes, because one branch pins a definition and the other does
not.

**The `auto_attack_damage` branch has no real instances, and the rule that empties it is an
accident.** Entry 126443 / provider 1267003 is **Precision Strikes**. It matches
`is_selected_auto_attack_damage_package` exactly — including its genuinely generic cross-effect
amount-equality requirement — but is refused by `require_physical_shell` because the owner's school
mask is 8 rather than 1. The branch is therefore exercised only by the synthetic fixture
`selected_trait_auto_attack_damage_inputs`.

Track C falsified the rule from data. `require_physical_shell` is set at exactly two sites
(`selected_trait_package.rs:452` and `:557`), and the value it tests — `owner.school()`
(`exact_source.rs:297`) — is **the owner aura's dispel school, which never reaches the auto-attack
damage school at all**. 24 providers carry raw-344 `ModAutoAttackDamage`; **6 of them are not
Physical**, and Precision Strikes is one of only six effect-clean variants containing raw-344.
Carrying the rule forward would admit Heart and refuse Precision Strikes for a reason with no
semantic content. **Keep the complete-effect-set half of the shell (`exact_source.rs:288-294`); drop
the school test.**

**Furious Blows was expected to be the one surviving named case. It is not — it is the one name
proved to cost a package.** `passive_melee_auto_attack_speed.rs` pins entry 116954 / definition
121966 / spell 390354 unconditionally, and entry **112197** resolves to the same provider and effect
with identical facts and zero blockers (§7). It is the fourth instance of the provider-identity
artefact, and the only case in the whole population where removing a literal would immediately admit
something Core does not.

**No named exception survives on evidence.** Every remaining literal either costs nothing today
(Heart, Martial Expert, Improved Vivify, Phalanx) or actively admits something it should not
(Ephemeral Bond's detached twin).

A structural observation from track F that bears on any refactor: **every id-pinned shape has a test
fixture that reproduces the same real ids.** The suite therefore cannot detect the hardcoding —
deleting a literal fails nothing, and adding a second real entry of the same shape is covered by
nothing.

## 10. Identity, atomicity, and the state side

### The minimal identity

Track B tested every candidate tuple over all 8,846 variants:

| tuple | collisions | lossy? |
| --- | --- | --- |
| `(entry)` | 567 | — |
| `(definition)` | 567 | — |
| `(provider)` | 424 | — |
| `(provider, rank)` | 211 | — |
| **`(entry, rank)`** | **0** | **no** |
| `(definition, rank)` | 0 | yes (8,845 keys for 8,846 variants) |

Adding `definition` or `provider` to `(entry, rank)` buys nothing. **`(entry, rank)` is the minimal
non-lossy package identity.**

The decisive witness against provider-keyed identity: provider **117216**, identical authored points
`(4.0, 10.0)` in both cases —

- entry 80287 → definition 85290, `Set` → rank amounts `(7, 5)` / `(15, 10)`
- entry 124795 → definition 129633, `Multiply` → rank amounts `(4, 10)` / `(8, 20)`

Same provider spell, same effects, **different compiled amounts**. Population-wide: 2,028 providers
are reachable from more than one definition; **302 have definitions with differing point rows; 96
resolve to different rank-1 amounts**; 171 have entries that disagree on `MaxRanks`.

> **Downgrade (hostile review 3).** The *collision* is real; the *exploit* is not reachable today.
> All four Core-named twins are on detached entries, so no host can select them. Population-wide,
> exactly one provider — **381650** (trees 1033/1034, entries 127060 at −3.0 and 127064 at −2.0,
> both Core-admitted) — puts two differing entries on two nodes of one tree, and those nodes share a
> position and carry `Visible` conditions on **disjoint SpecSets**, so a character cannot see both.
> And if a host did select both, Core refuses at the catalog layer
> (`crates/data/src/action/catalog/spell_modifier.rs`, `duplicate_spell_modifier`) before any
> state-side check runs. The finding is therefore **a latent representational defect, not a live
> bug**: provider identity cannot express the package, and any future change that admits two entries
> of one provider — or a second authority without the duplicate guard — makes it live.

### What the state side does

Core enforces same-actor package atomicity in two places, and **both key on the provider `SpellId`
alone**:

- `validate_complete_binding_packages` (`spell_modifier_authority.rs:387-411`) — covers
  all-`SpellModifier` packages via `passive_spell_modifier_package_effects(owner: SpellId)`.
- `validate_selected_mixed_passive_bindings` / `mixed_passive_effects(program, owner)`
  (`state/construction.rs:258-360`) — covers *mixed-role* packages, enumerating four authority
  families: `SpellModifier`, `CriticalBlock`, `AutoAttackDamage`, `PassiveCritical`.

Two gaps, both confirmed:

1. **There is no `HealingReceived` arm**, and the structural cause is that no
   `passive_healing_received_effects(owner)` accessor exists at all
   (`program_accessors.rs:204-223` exposes only `handle(effect)`). Ephemeral Bond's raw-118 half is
   therefore outside same-actor atomicity. Track F constructed the split: actor A binds
   `{e1,e2,e3}`, actor B binds `{e2,e3}`; B passes `construction.rs:315` (e1 invisible to it),
   passes `passive_modifier.rs:350-361` (which `.find()`s only the first bound actor) and passes
   `spell_modifier_authority.rs:392` (both spell modifiers present). B would receive the modifiers
   with no received-healing half.

   **Hostile review 3 broke the construction at its fourth link**: `passive_healing_received.rs`
   (`try_compile` → `audit_selected_effects`) **hard-refuses** a non-Player actor, and
   `spell_modifier_authority.rs:302-307` does the same for spell modifiers, reached unconditionally
   from `try_compile:121`. So the split is not merely unreached — it is **unconstructible**. The
   missing arm is still a real gap in the atomicity check, but it is **latent**, and the earlier
   claim that a partial binding would be "silently attributed to the Player" is false.
2. **Provider-keyed identity cannot express what it is asked to express.** For **92 of Core's own
   204** admitted packages the provider spell does not uniquely identify the package
   (measurement v5x, §6).

### Can authority enumeration be removed?

**Yes, and the information needed is small — but it is not free everywhere.** If final compilation
retained the package identity `(entry_id, effective_rank)` on each compiled definition — one
six-byte field per definition — state construction could enforce, for every actor,

    bindings(P, actor) == {}  OR  bindings(P, actor) == {e1 … en} exactly once

without knowing whether an effect belongs to spell modifiers, critical block, attack, healing,
resources or a future authority. That removes the `MixedPassiveBindingRole` enum and the four-arm
`has_mixed_passive_binding` dispatch, and closes the `HealingReceived` gap by construction rather
than by adding a fifth arm.

**Seven of the eight compilers already hold the value** (`SelectedTraitSource{entry_id,
effective_rank}` is carried through and then dropped). The eighth does not:
`critical_block_amount` has no activation enum at all — `CriticalBlockAmountInput`
(`crates/data/src/input.rs:504-508`) carries no selected-trait source — so it needs a new input
variant and a catalog field, not just plumbing. Hostile review 3 found this; the earlier claim that
"every compiler already holds the value" was false.

Two further prerequisites: a per-owner accessor for the healing-received authority (or a uniform
per-package effect list), and the identity field itself. **`CompiledCombatProgram` retains no
catalog today** — every definition keeps only a `SpellEffectRef`.

### The atomicity domain

**"One provider == one atomic package" is REFUTED generically.** It is true only on a domain that
must be stated, and Core does not currently state it.

- **Split by actor — refuted.** 749 packages name a non-caster unit recipient. The decisive witness
  is **Xalan's Cruelty 440040** (entry 117442): it carries three player-side spell modifiers *and*
  effects 6 and 9 as `SPELL_EFFECT_APPLY_AURA_ON_PET` carrying aura 218 — **the same authority, a
  different actor**. Also 440044, 389590, 449620 (`APPLY_AREA_AURA_PARTY` with aura 219), 405955,
  264735.
- **Split by server script — refuted, and invisible in the DBC.** 326 providers have a
  `spell_script_names` row; five are fully recognised. Pyrogenics 387095 is one self-delivered
  raw-219 effect with no `SpellAuraOptions` — exactly the generic single-rank shape — yet the world
  DB adds `spell_proc` and `spell_warl_pyrogenics` (`spell_warlock.cpp:931-950`), which hangs
  `OnEffectProc` on effect 0 and debuffs the proc *target*. One exact effect, two consumers, two
  actors. Mental Decay 375994 is the same shape.
- **Split by lifecycle — refuted at provider level.** Eight fully recognised packages have a
  non-Passive provider (Martial Prowess 316440, Shield Wall 871, Astral Shift 108271, Celestial
  Alignment 194223, Enraged Regeneration 184364). Only the owner contract's Passive check catches
  them; topology never would. Duration cannot split *within* a package — no provider has more than
  one `SpellMisc` row.
- **Split by presentation — real.** 25 over-admitted packages carry an `ignored` sibling; Avatar
  107574 (11 entries) has aura 61 `ScalePercentStacking` beside five combat roles.
- **Split by child action — not on the admitted domain.** On the modifier subtypes
  `EffectTriggerSpell` is the *payload identity*, not a child cast: Passing Seasons → Nature's
  Swiftness, Vulnerable Flesh → Maul, Refined/Concentrated → Mass Disintegrate, Quickened
  Invocation → Divine Toll. Independent support for the trigger-inert finding in §13/D3.

**The domain on which same-actor, all-effects-once atomicity does hold**, stated as a predicate over
source facts: one `SpellMisc` row and no difficulty split · every effect `APPLY_AURA` · every effect
`(TARGET_UNIT_CASTER, NONE)` · no period, radius, chain or mechanic · no trigger spell unless the
subtype is trigger-inert · provider `Passive` **and** `DurationIndex == 0` · **no world-DB
`spell_script_names` row and no `spell_proc` row** · dense effect indices.

**5,511 of 8,846 variants satisfy it, and 731 of the 755 fully recognised packages are inside it;
the 24 exceptions are all named above.** The conjunct Core does not check is the world-DB one — and
§13/D1 shows it matters.

(Recomputed by the lead against the final substrate with
`selected_package.procpolicy` supplying the script and overlay facts; track E measured 5,934 / 691
of 715 against the pre-fix classifier. The predicate is unchanged; only the classifier moved.)

## 11. Owner policy — a vocabulary, not tuples

    python3 selected_package.py owner-policy

Track C enumerated every spell-keyed DB2 table: of 1,090 CSVs, **72 carry a literal `SpellID`
column** and two more (`Spell`, `SpellName`) are keyed by `ID` *being* the spell id — **74 tables**,
of which **52 have at least one row over the selected population**. Core's owner checks cover 16.

Each table is assigned one A–I consequence class, with inertness argued separately from the class:

| class | tables | examples |
| --- | ---: | --- |
| A presentation/metadata | 24 | `Spell`, `SpellName`, `SpellXSpellVisual`, **`SpellMissile`**, `SpellActivationOverlay` |
| B immutable preparation fact | 2 | `SpellEffect`, `SpellScaling` |
| C acquisition gate | 30 | every inbound grant reference, `SpellLevels`, `SpellLearnSpell`, `TraitDefinition`, `SpecializationSpells`, `ItemEffect` |
| D conditional activation gate | 6 | `SpellAuraRestrictions`, `SpellShapeshift`, `SpellEquippedItems`, `SpellCastingRequirements`, `SpellCategories`, `SpellClassOptions` |
| E finite/mutable lifetime | 2 | `SpellMisc` (`DurationIndex`, `PvPDurationIndex`), `SpellInterrupts` (`AuraInterruptFlags` only) |
| F stacking/reapplication | 1 | `SpellAuraOptions` |
| G proc/runtime/cost | 7 | `SpellCooldowns`, `SpellPower`, `SpellReagents`, `SpellTotems`, `SpellEmpower` |
| H payload-side | 2 | `SpellLabel`, `SpellTargetRestrictions` |
| I unknown | **0 tables** | reached only per row |

No table needed an `unknown` class; **per-row class I does occur and blocks** — 1,464 providers carry
an owner attribute absent from Core's catalog (raws 23, 30, 50, 57, 64, 70, 96, 139, 157, 165, 240,
379, 404 …), 42 of them on effect-clean variants.

About a tenth of the population is professions: 444 providers carry `SpellReagents`, 407
`ModifiedCraftingSpellSlot`, 242 `SpellTotems`.

### Negative witnesses, reproduced from data (providers / entries / variants)

`caster_aura_state` **3 / 5 / 7** · `aura_restriction_any` 46 / 99 / 101 · `not_passive`
1,191 / 1,934 / 1,934 · `finite_duration` 408 / 721 / 721 · `shapeshift` 157 / 217 / 218 ·
`equipped_items` 122 / 213 / 216 · `aura_interrupt` 226 / 349 / 350 · `cooldown` 623 / 1,154 / 1,156
· `power_cost` 298 / 548 / 548 · `reagents` 444 / 644 / 644 · `attribute_unsupported`
2,634 / 4,606 / 4,800 · `attribute_uncatalogued` 1,464 / 2,547 / 2,565 · **`live_proc_entry`
994 / 1,566 / 1,669**.

**Cruelty is spell 392931, `CasterAuraState` 17** (`SpellAuraRestrictions` row 19441) — the mission's
named caster-aura-state witness, located. Only two others exist in the population: 373456 Unwavering
Will and 375542 Exuberance, both state 23.

**Core does not test `SpellInterrupts`, `SpellCooldowns`, `SpellPower`, `SpellReagents` or
`SpellCastingRequirements`.** The first is E-class (aura-interrupt flags make an application
removable); the rest are G-class (they imply an activation, not a passive). Trinity confirms the
gates Core *does* test are real: `HandlePassiveSpellLearn` (`Player.cpp:3079-3104`) suppresses the
cast on shapeshift `Stances`, and when `EquippedItemClass >= 0` the aura is added only while the
item requirement holds.

### Four adjudications on the vocabulary

1. **`require_physical_shell` is an accident** — falsified from data; see §9.
2. **"nonzero owner family when the selector is a class mask" is a separate, load-bearing
   dimension. Carry it verbatim.** `crates/data/src/game_data.rs:599-601`:
   `effect_spell_match_count` returns **1 for every payload spell** when the owner family is `NONE`,
   discarding the authored mask entirely — unbounded over-application. Trinity does not have this
   shape (`SpellInfo.cpp:2002-2007` tests family equality first). There are **0** selected witnesses
   but 57 rows across 10 spells globally, so the guard is protecting against data that exists.
3. **`SpellLevels` is safe to ignore for immutable preparation.** `Player::ApplyTraitEntry`
   (`Player.cpp:29391-29410`) calls `LearnSpell` with **no level check**; `MaxPassiveAuraLevel` is
   loaded (`DB2Structure.h:4043`) and never read anywhere; the one value-affecting use
   (`SpellInfo.cpp:578-588`) requires `EffectRealPointsPerLevel != 0`, which Core's neutral-amount
   shell already excludes.
4. **Redundancies, each with its subsumer named.**
   - `all_effect_mechanic_mask.is_empty()` is **fully redundant** — subsumed by the adjacent
     `spell_mechanic == NONE` (`passive_spell_modifier.rs:957`) plus the per-effect
     `EffectMechanic == 0` shell (`exact_source.rs:178-231`) given complete effect coverage. The
     mask is strictly weaker: `game_data.rs:1298` ignores raws outside 1..=36.
   - On the physical path, `neutral_physical_passive_owner_shell_issue` **re-tests `validate_owner`
     verbatim** (`exact_source.rs:298-306` ≡ `passive_spell_modifier.rs:953-959`; `:311-336` ≡
     `validate_owner_attributes`). Only `school == 1` and the effect-count equality are new — and
     the school test is the one §9 falsifies.
   - `spell_has_duration_entry` never fires independently (0 of 3,952 Passive providers carry a
     `DurationIndex`). That is an **empirical** observation, not a semantic redundancy — **keep it**.
   - Explicitly **not** claimed redundant: `SpellAuraRestrictions` (5 Passive carriers),
     `SpellShapeshift` (29), `SpellEquippedItems` (9), `DefenseType` (9 effect-clean variants),
     dispel type, owner class mask, and the family guard.

### Owner attribute 416

The largest single owner-side refusal is **attribute 416 = `AllowClassAbilityProcs`**
(`spell_attribute.rs:169`, catalogued `disabled` at `:479`), which blocks 145 effect-clean variants —
**85 of them (37 spells) blocked by nothing else**. Trinity's only consumer
(`SpellAuras.cpp:1859`) reads it off the **triggering** spell, never the aura owner, and only to
unlock some other aura carrying ATTR12. A permanently applied passive is never that triggering
spell, so **Core's block is over-conservative** for a Passive owner. Track C's module treats
`{415, 416}` as passive-inert, gated on the owner also carrying `Passive` raw 6 — the only
divergence from Core on the attribute path.

## 12. Effective proc policy

    python3 selected_package.py proc-policy

Track D built the AuraOptions → effective-proc classifier on top of the existing proc oracle
(`SpellMgr::LoadSpellProcs` default generation plus the world-DB `spell_proc` overlay). Families
over 5,143 providers:

| family | providers | admissible? |
| --- | --- | --- |
| `no-auraoptions` | 3,678 | yes |
| `effective-proc` | 1,003 | no |
| `script-driven` | 188 | no |
| `unknown` | 120 | no (fails closed) |
| `inert-no-trigger-aura` | 50 | yes |
| `inert-no-proc-flags` | 49 | yes |
| `guarded-proc` | 19 | no |
| `proc-icd` | 13 | no |
| `stack-capacity-only` | 12 | **yes** |
| `charges` | 7 | no |
| `rppm` | 4 | no |

3,782 providers are inert. Of the 220 providers reachable as admitted packages today, **213 are
inert and 7 are not**.

**Half 1 of the hypothesis is FALSIFIED as stated. Half 2 is confirmed.**

*Half 1 — "AuraOptions present + no effective proc generation ⇒ inert for immutable passive
admission" — is false as written*, on Trinity's own runtime terms:

- `Aura::CalcMaxCharges` (`SpellAuras.cpp:1004-1015`) reads `SpellInfo::ProcCharges` with **no proc
  entry at all**, and `Aura::Aura` then sets `m_isUsingCharges` (`:498-499`). A provider with
  charges has mutable per-application state whether or not a proc entry was generated.
- Trinity's infinite-loop guard (`SpellMgr.cpp:1888-1901`) yields "no entry" for **19 providers that
  are unambiguously procs**.
- An ICD (`ProcCategoryRecovery`) and an `SpellProcsPerMinuteID` are clocks that Trinity simply has
  not wired up; absence of a generated entry does not make them inert.

It survives only with three added conjuncts — `ProcCharges == 0`, `ProcCategoryRecovery == 0`,
`SpellProcsPerMinuteID == 0` — plus fail-closed guards on script bindings, world overlays and
uncataloged subtypes. `ProcTypeMask` and `ProcChance` stay uninterpreted. The principled line track D
arrived at is: **does the field require a counter or a clock?** If yes, it is not inert.

*Half 2 — `CumulativeAura` is maximum stack capacity, not initial stacks, and does not prove
repeated application — is confirmed*, with its premise made explicit. Initial stacks come from
`AuraCreateInfo::StackAmount`, default 1 (`SpellAuras.h:134`; `Aura::Aura` `SpellAuras.cpp:481`; via
`SpellValue::AuraStackAmount = 1` at `Spell.cpp:450`); `SpellInfo::StackAmount` appears nowhere on
the construction path. The stronger claim — a **passive** can never stack by reapplication — follows
from `IsMultiSlotAura()` being true for all passives (`SpellInfo.cpp:1808-1811`), which
short-circuits `Unit::_TryStackingOrRefreshingExistingAura` (`Unit.cpp:3395`). Residual hazards
(`Doses`, `SPELLVALUE_AURA_STACK`, `EffectModifyAuraStacks`, AuraScript) are real.

**Correction (hostile review 3).** The claim that they are "not gated by `CumulativeAura`" is
**wrong**: `Aura::ModStackAmount` *is* capped by it (`SpellAuras.cpp:1083-1106`, which clamps a
zero-capacity provider to 1). The field is not inert for stack mutation — it is inert only for
*initial* stacks, which is the narrower claim the admission actually needs. The conclusion survives
**empirically**: 0 of 201 `MODIFY_AURA_STACKS` rows target a selected provider, and no admitted
provider carries a `Doses` or `MaxAuraStacks` modifier. Phalanx's `CumulativeAura = 2` is dormant
source policy for a passive that is applied once, and the `stack-capacity-only` family (12
providers) is admissible on that basis — not on the broken one.

`IsMultiSlotAura` is at `SpellInfo.cpp:1803-1806` (the earlier citation of 1808-1811 was off).

### Core's two AuraOptions literals

**Both `Exact` literals can be replaced in full, with no residual package-specific fact.** Heart's
`proc_type_mask 4` and Phalanx's `cumulative_aura 2` are ballast: the first is subsumed by a
crowd-control-subtype guard, the second by the `is_passive` conjunct. Core's stated reason for Heart
— that none of its aura types is proc-triggering — was verified independently and **is the real
mechanism, not a coincidence**. It is, however, a one-enum-value margin: raw 334 is not
proc-triggering, but raw 52 `MOD_WEAPON_CRIT_PERCENT` **is**, and has a `PROC_HIT_CRITICAL` arm.
A generic predicate must test the property, never the neighbourhood.

**The `Absent` literal is unsound as written, and that is a live bug rather than a generalization
opportunity.** `SelectedAuraOptionsContract::Absent` checks only that no `SpellAuraOptions` row
exists. A world-DB `spell_proc` row keys on `SpellID` and does not care whether that row exists —
**17 selected providers violate the current spelling**. `Absent` must become *no row **and** no
effective entry **and** no script binding*.

**Cost of the replacement.** Of the 220 providers reachable as admitted packages today the predicate
admits 213 and rejects 7 — 387095, 422054 and 403509 (effective proc), 377098, 382517 and 383314
(proc ICD), 441346 (RPPM). It also admits three beyond Core's two witnesses: 462762 Encasing Cold,
1259922 Lethality, 1267093 Stormstream Totem — all in Heart's own inert family.

A second witness worth naming: **441346 Thousand Cuts** carries `ProcTypeMask 4`, byte-identical to
Heart's, **plus** an RPPM identity at 4.5/min. Matching Heart's literal bytes would admit it; testing
the semantics rejects it.

## 13. Defects found in Core

Each was found by running the rule against Core, not by reading Core. Each is labelled **LIVE** (a
package Core admits today because of it) or **LATENT** (the check is missing, but nothing currently
reaches it). The distinction is stated because a latent defect is a maintenance risk, not a wrong
number, and conflating them would overstate the result.

**D1 — LIVE — a proc- and script-driven passive is admitted as immutable.** Entry 115942, provider 387095
(Pyrogenics). The provider carries **no `SpellAuraOptions` row**, so every DBC-only owner check
passes and Core compiles it as an immutable raw-219 flat-label modifier. But the world DB supplies a
`spell_proc` row (proc flags 327680, chance 100) and `spell_script_names` binds
`spell_warl_pyrogenics` with an `OnEffectProc` hook. The real package has per-application mutable
proc state. *Evidence:* `trinity` `SpellMgr::LoadSpellProcs` (`SpellMgr.cpp:1497-1658`); `world`
overlay rows. *This is one of the five false negatives of rule v5; the other four are the contested
attribute case in D5.*

**D2 — LATENT — the one-rank flat-cooldown path has no sign floor.** `generic_spell_modifier` →
`flat_class_mask(cooldown = true)` applies no sign check at all, while the two-rank path
(`supports_ranked_flat_cooldown_source`) requires `< 0`. Four packages reach the cold contract with a
nonnegative cooldown flat — entry 135719 / provider 462762 at **+15,000 ms**, and entries 101836,
126928 and 127184 / provider 262624 at **0 ms** — which would compile a cooldown *increase*, or a
no-op, into a cooldown-reduction slot.

**No package is actually admitted this way**: all four are refused by final owner revalidation, for
the unrelated reason that their provider carries a `SpellAuraOptions` row. The floor is missing; an
unrelated check happens to be standing in front of it. If the `Absent` literal is relaxed as §12
recommends, this becomes live.

**D3 — LIVE (7 packages) — the spell-modifier path skips the neutrality shell every other role enforces.**
`basic_modifier_source` (`selected_trait_package.rs:1055-1069`) checks only kind, subtype, period
and misc0. It does not check `EffectTriggerSpell` or `EffectAmountFacts`. Three admitted providers
carry a trigger spell (382550 → 132158, 372618 → 6807, 1261448 → 436336) and four carry
`PvpMultiplier != 1.0` (423701, 449707, 450875 ×2). **Both fields are in fact inert here** —
subtypes 107/108/118/218/219/334/344/638 all dispatch to `AuraEffect::HandleNoImmediateEffect`
(`SpellAuraEffects.cpp:179, 180, 190, 290, 291, 406, 416, 710`) and `SpellInfo.cpp:5041` consumes
`TriggerSpell` only when `!effect.ApplyAuraName`; `PvpMultiplier` is loaded
(`DB2Structure.h:3914`, `SpellMgr.cpp:2777`) but has **no gameplay consumer anywhere in
`src/server/game`**. So Core is not *wrong* to admit them — it is **internally inconsistent**,
because `EffectAmountFacts::NEUTRAL` rejects `PvpMultiplier` for the non-modifier roles. The
inconsistency is confirmed from the other side too: `passive_damage_modifier.rs` **does** enforce
amount-fact neutrality, and refuses provider 390667 (entries 103872 and 134844) for a
`PvpMultiplier` of 0.667 — the same fact the spell-modifier path waves through. Pick one.

**D4 — LATENT — `HealingReceived` has no state-side atomicity arm** (§10), with a constructible split that
is currently unreachable only because two call sites hardcode `ActorId::Player`.

**D5 — LIVE (4 packages) — the owner-attribute policy is not uniform across compilers.** Provider 378004 carries owner
attributes 4 (`Ability`) and 416 (`AllowClassAbilityProcs`), both catalogued `disabled` in Core's own
`SPELL_ATTRIBUTE_REQUIREMENTS`. `passive_spell_modifier.rs` would refuse it;
`critical_modifier.rs:936` admits it because that compiler uses a **three-name blacklist** instead of
the catalog check. Four admitted variants depend on the difference. Independently, attribute 416's
block is itself over-conservative for a Passive owner: its only Trinity consumer
(`SpellAuras.cpp:1859`) reads it off the **triggering** spell, never the aura owner, and a
permanently applied passive is never that spell — 85 effect-clean variants across 37 spells are
blocked by attribute 416 and nothing else. Make the policy uniform first, correct second.

**I1 — LIVE (60 packages) — detached-entry policy is inconsistent.** Core fail-closes Heart's detached twin by pinning a
definition, and admits Ephemeral Bond's detached twin by pinning only a spell (§9). **60 of the 204
admitted variants sit on detached entries** — packages no host can select.

## 14. Conservative refusals worth reconsidering

Track B decomposed Core's source refusals; several are conservative rather than required.

- **`MaxRanks ≤ 2` is a Core cap, not a source boundary.** 1,068 entries have `MaxRanks ≥ 3`
  (largest 999), and **24 of them carry a real rank table that satisfies `exact_rank_curve_points`
  verbatim at their own `MaxRanks`** — e.g. entry 123788 / curve 76784 `[(1,5),(2,10),(3,15)]`,
  entry 128428 with `MaxRanks 70` and a 70-point curve. The source authors integer-rank curves well
  past two.
- **`OperationType == -1`** (7 entries) is `TraitPointsOperationType::None`
  (`DBCEnums.h:2907-2912`); `SpellInfo.cpp:550-552` falls through and keeps the authored value.
  Core already represents "no shaping" for unpointed effects, so the refusal is not required.
- **`VisibleSpellID != 0` refuses 123 entries on a presentation field** that cannot change an
  amount.
- **5 of the 44 `UnsupportedCurvePoints` refusals are conservative** (three-point rank tables on a
  two-rank entry — both reachable ranks still have an exact point). The other 25 defective rows are
  semantically right (24 start at `Pos_0 == 0`, one has two points at `Pos_0 == 1`).
- **`Curve.Flags` is checked but never nonzero** across all 1,356 referenced curves — an
  unexercised guard.
- **The `TraitSubTreeID != 0` branch is dead code in this snapshot**: all 140 nonzero rows also
  carry `TraitDefinitionID == 0`, so `MissingDefinition` always fires first.

## 15. Cross-build discipline

Track I's audit, with the caveat that Trinity evidence describes *this pinned private server* and
never Retail.

- **There is no schema skew.** All 26 `Trait*` tables have a Trinity struct and load info with
  matching column counts. The "missing loader" hypothesis is dead.
- **`NodeEntryType` 11/12/13 are unknown to Trinity** (enum stops at 10, `DBCEnums.h:2876-2889`),
  130 rows, **118 of them Core-admissible** — and Phalanx's admitted entry 137000 is type **13**.
  These are **not** newer-client skew: they exist with identical counts in a build inside the family
  the pinned Trinity claims to support. It is a stale server enum.
- **`SPELL_AURA_MOD_TRAIT_NODE_ENTRY_RANK` (224) is NYI** in Trinity (`SpellAuraDefines.h:311`,
  `SpellAuraEffects.cpp:296` → `HandleNULL`). 387 effect rows across 217 spells use it. The earlier
  figure "105 rows on Core-admitted providers" was **mislabelled**: it is 105 rows on *selected*
  providers, and **0 on Core-admitted** ones. Of the 256 distinct `TraitNodeEntryID` values these
  effects target, **1 is resolvable and 0 are admitted**. The mechanism by which one passive raises
  another entry's rank therefore exists in the data but touches nothing Core compiles today — a
  reopen condition, not a current threat to the acquisition boundary.
- **`GrantedRanks`** are server-synthesised from `TraitConditionType::Granted`
  (`TraitMgr.cpp:766-820`, 7,650 rows) and folded into the same scalar.
- Trinity **learns** the provider (`Player.cpp:29406`, `LearnSpell(SpellID, dependent, PlayerSpellTrait{DefinitionId, Rank})`);
  it never casts the entry or adds an aura from the trait path. Rank is a **curve X, not a stack
  count** (`SpellInfo.cpp:530-553`) — exactly Core's model.
- 24 of 173 aura subtypes used by admissible providers dispatch to `HandleNULL`/`HandleUnused`;
  auras 380, 493 and 531 have no name in Trinity at all. Only **37 of 5,525 providers are genuinely
  newer** than the pinned build.
- Unread columns that could matter: `TraitNode.Flags` (13 distinct values, no enum; all 118 type-13
  entries sit on flags 256/264), `TraitTree.PlayerConditionID` (140 trees, never read anywhere),
  `TraitCondFlags::IsAlwaysMet`.

**Acquisition verdict.** Explicit host-selected `(entry, rank)` is a safe acquisition boundary for
the *amount package*, but **not** for the aura lifetime: learning is not application, and
`HandlePassiveSpellLearn` gates the resulting aura on shapeshift and equipment. Core's existing
shapeshift/equipment/duration refusals are therefore correct and load-bearing, not conservative.

## 16. Unknowns and reopen conditions

    python3 selected_package.py unknowns

**338 unresolved items**: 127 with `core` evidence (an aura subtype present on a selected provider
whose Core catalog class explains the refusal) and **211 `unknown`** (uncataloged owner attributes,
and aura subtypes absent from Core's catalog). Each carries a stable id, source coordinates,
evidence class, blocker and reopen condition. Track D records 120 providers whose proc verdict is
`unknown` and fails closed on all of them.

Open questions this pass could not settle:

1. Is `require_physical_shell` a property of the **role** (auto-attack damage is physical by
   nature) or of the **owner**? It currently blocks the only real auto-attack-damage package
   (§9). *Reopens when:* a decision is made about Nature-school carriers of physical bonuses.
2. Can the proc predicate be made expressible without the world DB? *Reopens when:* Core ingests
   server-overlay evidence, or decides DBC-only proc policy is acceptable with a stated risk.
3. Are the 73 unreviewed multi-effect compositions legitimate? Each needs a composition semantic
   that does not exist yet; they are listed individually in `false-positives.json`.

## 17. Reproduction

**One command** regenerates all 17 corpora and verifies byte-stability:

    cd scripts/research
    python3 tools/regen_selected_package.py --check

It runs the oracle's own corpora, then each track's builder
(`selected_package.rankprov`, `tools/gen_proc_policy_build.py`, `tools/gen_proc_policy_ce.py`,
`tools/gen_track_corpora.py` for topology / sidecars / sole authorities), hashes the corpus
directory before and after, and exits 1 on any drift. Verified: **17 files, byte-stable.**
A missing track module is reported as `SKIPPED`, never silently, so an incomplete run cannot
look like a clean one.

Individual queries:

    python3 selected_package.py explain 115483 2      # complete evidence, fail-closed
    python3 selected_package.py classify 137000 2
    python3 selected_package.py census
    python3 selected_package.py admitted
    python3 selected_package.py authorities
    python3 selected_package.py false-positives --rule v5
    python3 selected_package.py false-negatives --rule v5
    python3 selected_package.py ledger
    python3 selected_package.py owner-policy
    python3 selected_package.py proc-policy
    python3 selected_package.py unknowns

**Tests.** `scripts/research/tests/test_sp_*.py` — **192 tests, all passing**:
`test_sp_a_oracle.py` 33 (substrate, Core baseline, rules),
`test_sp_b_rankprov.py` 41 (rank/effect-point provenance),
`test_sp_c_owner.py` 30 (sidecar policy),
`test_sp_d_procpolicy.py` 36 (AuraOptions / effective proc),
`test_sp_e_topology.py` 23 (topology, over-admission, atomicity),
`test_sp_g2_sole.py` 29 (sole-effect authorities).

`pytest` is **not installed for this machine's system `python3`**, and neither is `pip`. Run the
suite through `uv`:

    uv run --no-project --with pytest --with hypothesis python -m pytest tests/ -q

Two of the tests are worth naming because they guard conclusions rather than code:
`test_every_rule_dimension_is_reachable_except_the_proved_redundancy` fails if `rank_amounts` stops
being vacuous, and `test_shaping_uniformity_is_falsified` fails if Improved Vivify ever stops
refuting the uniformity dimension.

## 18. Hostile review

Three independent hostile reviews were run against the finished report, oracle and corpora, each
briefed to break a different claim and told that finding nothing is a failed review. Their
deliverables are in `bag/selected-package-pass/reviews/`. **Sixteen defects were confirmed and every
one is fixed or recorded below**; the numbers in this report are post-review.

### R1 — false positives (4 confirmed)

| # | finding | resolution |
| --- | --- | --- |
| 1 | `effects.py` gave roles to raws 52/57/183/187/197, which `critical_modifier.rs:1059-1070` proves have **no selected-trait activation**. Nine exact effects classified with zero blockers; raw 52 is in Trinity's `isTriggerAura[]` (`SpellMgr.cpp:1724`). | **Fixed** — they now produce an explicit blocker. Package counts did not move (each sat beside a failing sibling), which is why it was latent at package level and real at effect level. Test added. |
| 2 | The claim that raw 422 has "no consumer anywhere in `src/server/game`" is false — `Unit::SpellAbsorbBonusTaken`, `Unit.cpp:7715`. | **Fixed**; the conclusion is now stated on the stronger ground that the consumer never reads the misc value. |
| 3 | The headline attributed v1's false-positive breakdown to v5, and one of those classes cannot exist under v5 because `effect_ceiling` is one of its own dimensions. | **Fixed** throughout. |
| 4 | The sole-effect false-positive verdict string claimed an "ordinary authority compiler" for records that are cross-product cells Core reaches only inside a named branch. | **Fixed** in `rule.py`. |

R1 attacked and could **not** break: the proc/script escape hunt (13 counterexample classes × 213
admitted providers, zero overlap, plus a fresh sweep of `spell_linked_spell` and eight other world
tables from the TDB dump — zero hits); the 79/87 school-mask selector; trigger-inertness;
`PvpMultiplier`; owner-policy table coverage; and the four-effect homogeneity claim, re-derived at
five rule cut-offs (438 over-cap, 26/25/24 clean, 100% homogeneous `SpellModifier` every time).

### R2 — false negatives and overfitting (2 confirmed)

| # | finding | resolution |
| --- | --- | --- |
| 1 | The per-subtype modifier operation table was **overfitting**: Core's own `SelectedSpellModifierValue` is a free cross product, and the missing cells were exactly the ones no reference package uses. Cost: 18 owner-clean packages refused by nothing else. | **Fixed** — the candidate rule now uses the cross product (§5). All five reference packages classify identically; admissions rose by 18. |
| 2 | `TRIGGER_INERT_SUBTYPES` was the reference set wearing a Trinity citation; raws 87, 308 and 422 also dispatch to `HandleNoImmediateEffect`. | **Fixed**, with per-subtype handler citations. Raws 79/193/290/319 have real handlers and are deliberately left out — fail closed. |

R2 also forced the adjudication rewrite in §6: four of the five false negatives are **contested**,
not "Core's fault", because the rule's own `owner.py` blocks on attribute 416 which Trinity makes
unreachable for a proc-inert Passive owner (`Aura::GetProcEffectMask`, `SpellAuras.cpp:1834-1839`
gates the whole proc-attribute block behind `if (!procEntry) return 0;`). The Pyrogenics
adjudication is likewise softened: the script never touches the compiled effect's amount, stacks,
charges or lifetime. **R2 confirmed that "remaining named exceptions: zero" is defensible** — the
rule admits 72 variants across 29 multi-effect role multisets Core has never composed (Core composes
20), and 4 of the 5 same-provider twins Core splits, refusing the fifth on a positive cooldown flat.

### R3 — ownership and lifecycle (10 confirmed, 3 downgrades)

The three downgrades matter more than the defects, and all are applied above:

- **Provider-identity exploitation is unreachable** (§10). The collision is real; no host can select
  two differing entries of one provider, and Core's catalog layer refuses duplicates anyway. The
  finding is a latent representational defect, not a live bug.
- **The `HealingReceived` split is unconstructible** (§10/D4), not merely unreached:
  `passive_healing_received.rs` and `spell_modifier_authority.rs:302-307` both hard-refuse a
  non-Player actor.
- **"105 raw-224 rows on Core-admitted providers" was mislabelled** (§15) — it is 0 on admitted
  providers.

R3 also corrected the `CumulativeAura` argument (`Aura::ModStackAmount` **is** capped by it, so the
conclusion now rests on the empirical zero rather than a wrong mechanism), the atomicity-domain
count (5,511, which R3 and the lead derived independently and agreed), the observation that the
actor axis is closed by the **effect-kind** conjunct rather than the implicit-target one, and the
claim that "every compiler already holds the value" — `critical_block_amount` holds nothing.

### What the reviews did not change

The headline result survived all three: a two-dimension rule reproduces Core's admitted population
with no package names. What the reviews changed is its *precision* — the role table is wider, five
subtypes are excluded, two dimensions are proved redundant, four disagreements are contested rather
than resolved, and two findings are downgraded from live to latent.

## 19. What this gives the Core agent

Ordered by evidence strength, not by appeal. Nothing here is a plan; each item is a finding with
the evidence attached.

### Do first — these are defects today, independent of any refactor

1. **D1 Pyrogenics**: a DBC-only view of proc policy is unsound. `SelectedAuraOptionsContract::Absent`
   must become *no `SpellAuraOptions` row **and** no effective proc entry **and** no
   `spell_script_names` binding*. 17 selected providers violate the current spelling.
2. **D2**: give the one-rank flat-cooldown path the same sign floor the two-rank path has.
3. **D3**: make the neutrality shell uniform. Either the spell-modifier path gains the
   `EffectAmountFacts`/trigger checks or the other roles drop them — currently
   `passive_damage_modifier.rs` refuses a `PvpMultiplier` of 0.667 that
   `passive_spell_modifier.rs` waves through.
4. **D5**: make the owner-attribute policy uniform (`critical_modifier.rs` uses a three-name
   blacklist where every other compiler uses the catalog), *then* correct it — attribute 416 is
   over-conservative for a Passive owner and blocks 85 effect-clean variants by itself.
5. **I1**: pick one detached-entry policy. Core currently fail-closes Heart's detached twin and
   admits Ephemeral Bond's; 60 of 204 admitted variants are on entries no host can select.

### Then — the generalization, in the order the evidence supports

6. **Keep the exhaustive-pattern shape.** Whatever replaces the named branches must classify the
   *complete* source effect slice and reject on any unclassified sibling. 1,151 variants are waiting
   behind a recognised-effects-only filter, **546 of them same-authority** (§8). This is the single
   most important constraint in the report.
7. **Assign roles per effect, never positionally** (§8). Attuned to the Dream 376930 already carries
   Ephemeral Bond's multiset in a different order.
8. **Unify the sole-effect authorities first.** They are already generic and they are 184 of the 204
   admitted packages. Six of their mutual disagreements have no semantic content (§4); the shaping
   clause is vacuous in all five spellings; and `points = rank_one` versus `points = authored` is a
   latent contradiction that would reject Core's own Static Charge witness if applied uniformly.
9. **Retain `(entry_id, effective_rank)` as immutable package provenance** on each compiled
   definition — one six-byte field every compiler already holds and then drops — and key state-side
   atomicity on it instead of the provider `SpellId`. That removes the `MixedPassiveBindingRole`
   enum and the four-arm dispatch, and closes the `HealingReceived` gap (D4) by construction rather
   than by adding a fifth arm. Prerequisite: a per-owner accessor for the healing-received
   authority, which does not exist today (§10).
10. **The multi-effect composition is the only real hardcoding target**, and it is nine branches in
    one file. Improved Vivify and Phalanx already share a role multiset and differ only in shaping;
    the direct+periodic branch alone leaves 34 semantically identical packages on the floor (§7).
11. **Drop the school test in `require_physical_shell`** and keep its complete-effect-set half. The
    value it reads is the owner aura's dispel school and never reaches the auto-attack damage
    school; it is the only thing preventing the `auto_attack_damage` branch from admitting anything
    (§9).
12. **Raise or remove `MAX_PACKAGE_EFFECTS`.** 26 otherwise-clean packages exceed it, all homogeneous
    `SpellModifier`, and neither the compile nor the binding path is arity-bounded (§7). Note that
    `MAX_RANK_SHAPED_EFFECTS` is also 4, so above four effects mixed shaping becomes structurally
    unavoidable — a second reason the uniformity dimension must stay dead.

### Do not do

13. **Do not add a "one effect per role" or "uniform shaping" invariant.** Both are falsified by
    packages Core already admits (§6, §7).
14. **Do not treat provider-spell identity as a package key** anywhere new. It is already wrong in
    two places (§10).

### A note on the test suite

Every id-pinned shape currently has a fixture reproducing the same real ids, so **deleting a literal
fails nothing** and adding a second real entry of the same shape is covered by nothing. Any refactor
should land a test that the fixtures cannot pass by accident — the natural one is a census assertion
over real data, of the kind `test_sp_a_oracle.py` uses.

### Honest summary of the result

Zero additional packages would have been an acceptable outcome. What the evidence actually supports
is **87 likely-legitimate generalizations** under the final rule, **one named literal (Furious
Blows) proved to cost a real package**, and **five Core defects** — three live, two latent — that
the generic rule catches and Core does not. Against that, **82 multi-effect compositions remain
unreviewed** —
they are listed individually in `false-positives.json`, each needs a composition semantic that does
not exist yet, and none of them should be admitted on the strength of this report alone.
