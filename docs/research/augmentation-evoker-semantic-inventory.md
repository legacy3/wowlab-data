# Retail Augmentation Evoker semantic inventory

## Scope and evidence rules

This is a read-only evidence survey, not an implementation plan. It inventories identities and
semantic families important to simulating current Retail Augmentation Evoker.

- **Current-data fact (D):** reproduced from the `wowlab-data` CSV corpus for Retail build
  **12.1.0.69497**. The corpus metadata reports 1,090/1,090 tables downloaded successfully from
  `wago.tools`. Repository revision: `88469d69b5a603df9e34de31478ca49d669751a7`
  (2026-09-09).
- **Executable/source-consumer semantic (C):** reproduced from SimulationCraft revision
  `b48def9c26d7532db2e612d97d433ec66bd8eede` (2026-08-27), principally
  `engine/class_modules/sc_evoker.cpp`, or from TrinityCore revision
  `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` (2026-07-14). These sources show how a current
  consumer interprets the data; they are not server authority.
- **Plausible (P):** a relationship supported by current rows or a consumer but not completely
  encoded or independently reproduced.
- **Unknown (U):** current data and inspected consumers do not settle the behavior.

The current authoritative trait traversal starts at `ChrSpecialization.ID=1473` (Augmentation),
`TraitTreeLoadout.TraitTreeID=872`, and the Aug specialization condition uses `SpecSetID=174`.
The Aug-capable hero subtrees in that tree are `TraitSubTreeID=36` (Scalecommander) and `38`
(Chronowarden). Flameshaper (`37`) is not selected for Augmentation. `MasterySpellID=406380`.

The spell graph surveyed here contains the selected specialization-tree roots, both selected hero
subtrees, the specialization passives 396186 and 1256942, the mastery spell, and the child/buff/
damage records reached through trigger fields, descriptions, labels, and the current executable
consumer. Generic class-tree spells were followed where an Aug mechanic modifies them.

## Current trait roots

### Augmentation specialization tree (D)

The current `TraitNode -> TraitNodeEntry -> TraitDefinition` traversal, filtered through the Aug
condition, yields these roots:

`1268881 Improved Defy Fate`; `410261 Inferno's Blessing`; `395152 Ebon Might`; `351338 Quell`;
`395160 Eruption`; `369908 Power Nexus`; `408002 Tectonic Locus`; `412733 Unyielding Domain`;
`396286 Upheaval`; `459120 Rumbling Earth`; `406904 Volcanism`; `408004 Momentum Shift`;
`406659 Ricocheting Pyroclast`; `360827 Blistering Scales`; `409329 Reactive Hide`;
`406907 Regenerative Chitin`; `410643 Molten Blood`; `375796 Hoarded Power`; `410260 Overlord`;
`410685 Symbiotic Bloom`; `407243 Aspects' Favor`; `403208 Draconic Attunements`;
`375722 Essence Attunement`; `396187 Essence Burst`; `407814 Pupil of Alexstrasza`;
`410784 Echoing Strike`; `404195 Defy Fate`; `407869 Anachronism`; `454983 Arcane Reach`;
`407866 Plot the Future`; `1250914 Clairvoyant`; `407876 Accretion`;
`409267 Motes of Possibility`; `408083 Font of Magic`; `404977 Time Skip`;
`403631 Breath of Eons`; `409676 Chrono Ward`; `410253 Perilous Fate`; `409311 Prescience`;
`410687 Prolong Life`; `414969 Dream of Spring`; `412710 Timelessness`; `412774 Fate Mirror`;
`408775 Ignition Rush`; `412713 Interwoven Threads`; `412723 Tomorrow, Today`;
`408233 Bestow Weyrnstone`; `459537 Imminent Destruction`; `1291457 Mighty Inferno`;
`1259173 Duplicate`; `1259174 Duplicate`; `1259175 Duplicate`.

### Current hero roots (D)

- **Scalecommander 36:** 441176 Melt Armor, 441212 Extended Battle, 441219 Diverted Power,
  441180 Hardened Scales, 441181 Menacing Presence, 441246 Unrelenting Siege, 434300
  Bombardments, 436335 Mass Disintegrate, 433871 Maneuverability, 441253 Nimble Flyer, 441257
  Slipstream, 441245 Onslaught, 441705 Might of the Black Dragonflight, 441206 Wingleader,
  438587 Mass Eruption, 1261452 Refined Essence, 1261448 Concentrated Power, and 1260745
  Command Squadron.
- **Chronowarden 38:** 431615 Reverberations, 431875 Afterimage, 431484 Instability Matrix,
  431874 Double-time, 431984 Time Convergence, 431873 Temporality, 432008 Motes of Acceleration,
  432004 Golden Opportunity, 431715 Nozdormu Adept, 429483 Warp, 431657 Primacy, 431442 Chrono
  Flame, 431695 Temporal Burst, 1260647 Overclock, 1291522 Chronal Dynamo, and 1260484
  Chronoboon.

## Semantic-family reduction

| Semantic family | Relevant raw identities | Representative current spells | Why it matters |
|---|---|---|---|
| Ally primary-stat modification | Effect 6; Aura 540, `misc0=1`; area-ally targets 18/31 | Ebon Might 395152 | Another actor's later output changes from Aug-owned state. The amount depends on the Aug actor's primary stat. |
| Ally secondary-stat modification | Effect 6; Aura 540, `misc0=5` and `6` | Prescience buff 410089; Shifting Sands 413984 | Crit and Versatility are carried by the same raw support-stat aura with distinct property IDs. |
| Source-relative periodic recomputation | Aura 226; 1,000 ms period | Ebon Might 395152/395296; Shifting Sands 413984 | The raw rows provide a periodic driver. The consumer uses it to refresh values from the source Aug's live Intellect/Mastery. |
| Ally targeting and caps | Targets 18/31 and 21; `MaxTargets` 30, 2, or 1 | Ebon Might, Inferno's Blessing, Prescience, Shifting Sands, Blistering Scales | Area selectors and caps are distinct from role preference and explicit-target policy. |
| Delayed accumulated damage | Aura 226 plus proc metadata; Effect 2 child | Temporal Wound 409560 -> Breath of Eons damage 409632 | Damage done during a window is accumulated and released later, with causal Aug provenance and contributor attribution. |
| Proc-driven ally damage/healing | Aura 4 drivers, proc masks, Effect 2/10 children | Inferno's Blessing 410263/410265; Fate Mirror 410089/404908/413786 | An ally's later action triggers a payload whose magnitude or ownership depends on Aug state. |
| Retaliatory ally state | Aura 22/142; Effect 2 child | Blistering Scales 360827/360828 | Aug armor modifies an ally; melee contact produces an Aug-derived explosion. |
| Ally-triggered split damage | Aura 4 drivers; Effect 2; targets 53/16 | Bombardments 434473/443788/434481 | Aug marks a target, then Aug or allies may trigger damage split among nearby enemies. |
| Source-scoped damage amplification | Aura 271 | Melt Armor 441172 | The enemy takes more damage from the caster's selected spells; it is not a universal target multiplier. |
| Spell history replay | Aura 4/332; Effect 3/2 children | Chrono Flame 431442/431443/431583 | A later action repeats a fraction of the Aug's damage/healing to the target during a prior time window. |
| Empower stages | `SpellEmpower`/`SpellEmpowerStage`; EffectAttribute 18 | Fire Breath 357208/357209; Upheaval 396286/396288 | Release stage changes duration/front-loading or area payload, and empower completion drives Shifting Sands and Ebon extension. |
| Buff extension and duration mutation | Aura 226; SpellAttribute 436; explicit values 1,000/2,000/5,000 ms | Sands of Time 395153; Prolong Life; Double-time; Extended Battle; Mighty Inferno; Duplicate extension | Remaining lifetime is mutated rather than merely refreshed. Several different target auras are affected. |
| Label-scoped modifiers | Aura 143, 218, 219, 537; SpellModOp values | Time Skip 404977; Interwoven Threads 412713; Might of the Black Dragonflight 441705; Fire Breath 357209 | Applicability is expressed by authoritative `SpellLabel` membership rather than names. |
| Class-mask-scoped modifiers | Aura 107/108 and four-word effect masks | Essence Burst 359618; Ebon self aura 395296; specialization passive 396186 | Applicability is a raw Evoker spell-family bitset, distinct from labels. |
| Cooldown/charge mutation | Effect 291; Aura 143, 148, 453, 454 | Accretion 407876/411932; Time Skip; Interwoven Threads; Nozdormu Adept; Warp | Several namespaces coexist: spell labels, spell-family masks, and charge-category IDs. |
| Essence and free-cost state | `PowerType=19`; Aura 108 `misc0=14`; Aura 418 `misc0=19` | Eruption 395160; Essence Burst 359618; Power Nexus 369908 | Essence cost, maximum resource, free-cast state, and stack behavior are separate records. |
| Periodic damage | Aura 3; periods 2,000 ms; periodic coefficient | Fire Breath 357209; Melt Armor 441172 | Fire Breath trades DoT duration for direct damage by empower level; Melt Armor also carries a periodic damage effect. |
| Guardian/pet payload | Effect 28; Effect 202; Aura 429/531 in spec passive | Duplicate 1259171; Command Squadron 1260745/1236970; Prescience 410089 | Guardians cast child spells; Prescience has an explicit summons-area-aura effect; some proc consumers accept pet events. |
| Other long-lived ally state | Aura 10, 69, 118, 129, 133 | Timelessness, Molten Blood, Symbiotic Bloom, Bronze/Black Attunement | Threat, absorbs, healing received, movement speed, and maximum health are distinct ally-affecting families. |
| Triggered cooldown reduction | Aura 42 -> child Effect 291 | Accretion 407876 -> 411932 | A proc aura invokes a child whose raw effect directly modifies cooldowns by family/mask coordinates. |

## Exact current-data spell/effect inventory

Notation: `Ei` is zero-based `EffectIndex`; `E` is raw `SpellEffect`; `A` is raw
`AuraSubtype`; `m0/m1` are the two misc values; `T` is `ImplicitTarget_0/1`; `trig` is
`EffectTriggerSpell`; `coef` is `EffectBonusCoefficient`; `period` is milliseconds. An omitted field
is zero. Raw values are reported without assigning semantics where the data does not provide them.

### Ally stat and ally-state families (D)

| Spell | Exact effect rows | Other reproduced facts |
|---|---|---|
| **395152 Ebon Might** | E0 `E6/A226`, base 8, T18/31, period 1000; E1 `E6/A540`, `m0=1`, T18/31; E2 `E3`, base 2 | Duration 10,000 ms; cooldown 30,000 ms; mana 1%; target restriction `MaxTargets=30`; current text says damage-dealing allies gain primary stat from the Aug and the effect is reduced above E2 other allies. Spell family 224. |
| **395296 Ebon Might (self)** | E0 `E6/A108`, base 20, op 0, mask `[1,0,0,0]`; E1 `A108`, op 0, mask `[0,67108872,0,0]`; E2 `A108`, op 22, same mask; E3 `A286`, mask `[32768,134217728,0,0]`; E4 `A148`, `m0=2456`; E5 `A108`, base 20, op 22, mask `[1,0,0,0]`; E6 `A226`, period 1000 | Duration 10,000 ms. The masks are exact; `67108872=0x04000008`, `134217728=0x08000000`. |
| **409311 Prescience** | E0 `E3`, base 3, T18/31, EffectAttr raw 12; E1 `E3`, base 25, T21, EffectAttr raw 21 | Charge category 2207: two charges, 12,000 ms recovery. Target restriction `MaxTargets=1`. Current text: +3% crit for 18 sec; without an explicit ally, chooses nearest within 25 yd and prefers damage dealers with active cooldowns. The effect's secondary radius row resolves to 26.5 yd, so text and radius data should both be retained. |
| **410089 Prescience buff** | E0 `E6/A540`, base 3, `m0=5`, T21; E1 `E202/A4`, T21; E2 `E3`, base 10 | Duration 18,000 ms. AuraOptions: chance 25, proc mask 2,446,676. `SpellLabel 5959` has this record as its sole member. |
| **406380 Mastery: Timewalker** | E0 `E6/A4`, coef 0.272; E1 `E6/A108`, `m0=1`, coef 0.5, effect mask word 3 = 8; E2 `E6/A4`, base 1, `trig=413984` | Current text explicitly grants Shifting Sands to one ally, prefers damage dealers, scales Versatility with mastery, and increases helpful-aura durations. |
| **413984 Shifting Sands** | E0 `E6/A540`, `m0=6`, T18/31, coef 0.272, EffectAttr raw 16; E1 `E6/A226`, T18/31, period 1000 | Duration 10,000 ms; `MaxTargets=1`. |
| **360827 Blistering Scales** | E0 `E6/A22`, `m0=1`, T21, EffectAttr raw 7; E1 `E6/A142`, base 20, `m0=1`, T21, EffectAttr raw 7 | Duration 3,600,000 ms; `MaxTargets=1`; category recovery 30,000 ms; AuraOptions charges 15, chance 100, proc mask 40. Current text: 20% of Aug armor and one target at a time. |
| **360828 Blistering Scales damage** | E0 `E2`, T18/16, EffectAttr raw 12 | No DBC coefficient is present. The current consumer uses a tested/hard-coded 0.3 spell-power coefficient and a 2 sec internal cooldown. |
| **403264 Black Attunement** | E0 `E6/A133`, base 2, T1; E1 `E6/A226`, T1, period 2000 | Indefinite duration; current text applies the max-health state to self and three nearest allies. |
| **403265 Bronze Attunement** | E0 `E6/A129`, base 10, T1; E1 `E6/A226`, T1, period 2000 | Indefinite duration; current text applies movement speed to self and four nearest allies. |
| **410651 Molten Blood** | E0 `E6/A69`, `m0=127`, T21 | Duration 30,000 ms. Current text makes the absorb larger at lower target health. |
| **410686 Symbiotic Bloom** | E0 `E6/A118`, `m0=127`, T21 | Duration 10,000 ms; current text is +3% healing received. `SpellLabel 2663` contains only this spell. Prolong Life extends it whenever Ebon Might is extended. |
| **412710 Timelessness** | E0 `E6/A10`, base -30, `m0=127`, T57 | Duration 3,600,000 ms; `MaxTargets=1`; current text says reduced threat, with a smaller effect on tanks. |

### Cross-actor, delayed, proc, and history families (D unless marked C)

| Spell family | Exact effect/proc facts | Reproduced semantic relationship |
|---|---|---|
| **403631 Breath of Eons** | E0 `E3`, T87; E1 `E6/A263`; E2 `E6/A33`, base -200; E3 `E6/A147`, `m0=2360`; E4 `E6/A488`; E5 `E6/A485`, base -100. Duration 6,000 ms; cooldown 120,000 ms; target flags 64. | Current text applies Temporal Wound for 10 sec, grants/extends Ebon by 5 sec, and stores 15% of damage from Ebon-buffed allies, reduced above two other Ebon allies. Flight/control auras are part of the cast record, not the delayed-damage record. |
| **409560 Temporal Wound** | E0 `E6/A226`, base 15, T78/16, period 1000. Duration 10,000 ms. AuraOptions chance 100, proc mask 664,232. | **C:** SimC suppresses ordinary periodic ticks, accepts qualifying positive damage from the Aug or actors carrying that Aug's Ebon, buckets amounts by contributing actor, and on expiry executes one 409632 action per contributor. |
| **409632 Breath of Eons damage** | E0 `E2`, T6; no DBC coefficient. | **C:** the action is instantiated on the contributing actor while its custom state retains the Aug source. This is explicit split provenance: apparent/log owner and causal support source are not the same field. |
| **410263 Inferno's Blessing buff** | E0 `E6/A4`, T18/31, EffectAttr raw 16. Duration 8,000 ms; `MaxTargets=2`; AuraOptions chance 101, PPM ID 404 (`BaseProcRate=10`, flags 1), proc mask 332,116. | Current text says Fire Breath grants the buff to self and a nearby ally. **C:** ally/pet proc events are allowed; 410265 is executed as an action of the buffed actor while the state retains the Aug source. |
| **410265 Inferno's Blessing damage** | E0 `E2`, T6; no DBC coefficient. | **C:** SimC uses a hard-coded 2.69 spell-power coefficient because the current DBC child has none. |
| **412774 Fate Mirror talent** | E0 `E6/A4`, base 15, EffectAttr raw 7. | The selected talent text gives Prescience a chance to echo damage or healing at 15% power. |
| **404908 / 413786 Fate Mirror** | 404908 E0 `E2`, T6; 413786 E0 `E10`, T21; neither has a DBC coefficient. | **C:** Prescience's chance-25/proc-mask carrier triggers damage for hostile targets or healing for friendly/self damage; it copies `result_amount`, permits pet procs, attributes the action to the buffed player, and retains the Aug in custom state. |
| **434300 / 434473 / 443788 Bombardments** | 434300 E0 `E6/A4`, base 1, `trig=434473`; 434473 E0 `E6/A4`, T6, duration 6,000 ms, chance 30, proc mask 664,232; 443788 E0 `E6/A4`, T6, indefinite duration, chance 100, proc-category recovery 2,000 ms, same mask. | Current text: Mass Eruption marks the primary target; Aug and allies can proc a Bombardment. Extended Battle adds 1 sec per Essence ability. Two distinct current proc carriers exist. |
| **434481 Bombardments damage** | E0 `E2`, T53/16, coef 4.75; radius 8 yd; SpellClassOptions mask `[0,0,16384,0]`. | Current text says damage is split among nearby enemies. **C:** SimC constructs the action on the triggering actor and retains the Aug source; pet procs are deliberately not enabled. Its external-ally cadence is partly synthetic rather than wholly DBC-driven. |
| **441172 Melt Armor** | E0 `E6/A3`, T6, period 2000, coef 1.4; E1 `E6/A271`, base 20, T6, effect class mask `[0,0,64,0]`. Duration 12 sec in the current description. | The target takes 20% more damage from the caster's selected Bombardment/Essence payloads. This is source-scoped (`FROM_CASTER`), not generic raid amplification. |
| **431442 Chrono Flame** | E0 `E6/A4`, base 15; E1 `A4`, base 5; E2 `A4`, base 25, coef 2; E3 `E6/A332`, base 431443, `m1=2`, effect mask word 1 = 1,048,576. 431443 E0 is `E3`, T25; 431583 E0 is `E2`, T6, variance 0.05. | Current text repeats 15% (Aug branch) of damage/healing done to the target in the last 5 sec. **C:** SimC keeps five one-second history buckets and executes the repeat later. The exact server history accounting and attribution are not encoded in DBC. |
| **409676 Chrono Ward** | Root has an `E3` plus an `E6/A4` carrier in the selected tree. | Current text says allies who contribute Temporal Wound damage receive an absorb equal to 100% of that damage, capped at 30% of the Aug's maximum health. This joins contributor provenance, delayed damage, and an ally absorb. |

### Empower, periodic, Essence, and guardian families (D unless marked C)

| Spell family | Exact current facts | Semantic consequence |
|---|---|---|
| **357208 / 357209 Fire Breath** | Charge spell 357208 has `SpellEmpowerID=1`: stage 0 = 1000 ms, stage 1 = 750, stage 2 = 750. Font override 382266 uses ID 15 and adds stage 3 = 750. Release 357209: E0 `E2`, T47/104, coef 1.63408994675, variance .05; E1 `E6/A3`, same targets, coef .39483401179, period 2000; E2 `E6/A4`, base 1; E3/E4 `E6/A537`, `m0=1464/1468`; E5 `E38`, base 2, `m0=1`. All six release effects carry EffectAttr raw 18. | Current text says higher empower converts periodic damage to immediate damage and the cone is reduced beyond five targets. **C:** SimC subtracts 6 sec of DoT duration per stage above 1 and adds the removed ticks' damage to direct damage; it models five-target reduced-AoE behavior. Fire Breath also triggers Inferno's Blessing and extends Ebon. |
| **396286 / 396288 Upheaval** | Charge has `SpellEmpowerID=31` with 1000/750/750 ms stages; Font override 408092 uses ID 39 with an additional 750 ms stage. Release E0 `E3`, T6; E1 `E2`, T53/16, coef 4.30000019073, variance .05, EffectAttr raw 18; E2 `E144`, base 120, `m0=-20`, T53/16, mechanic 6; E3 `E144`, base 120, T6, mechanic 6; E4 `E6/A4`, T53/16. | Current text says radius grows with empower stage and the cast extends Ebon. The raw stage timing is reproduced, but exact stage-to-radius mapping is not exposed by these effect rows. |
| **395160 Eruption** | E0 `E2`, T53/16, coef 2.79999995232, EffectAttr raw 6. `PowerType=19`, cost 3. | Damage is split among the target and nearby enemies. Sands of Time extends active Ebon by 1,000 ms per cast, with the separate extension-crit rule below. |
| **359618 Essence Burst buff** | E0 `E6/A108`, base -100, `m0=14` (`PowerCost0`), effect class mask `[9437248,0,0,0]` (`0x00900040`). AuraOptions `CumulativeAura=1`, chance 100, proc mask 81,920. | It makes the selected Essence spender free. Essence Attunement 375722 is `A107`, base 1, op 37 (`MaxAuraStacks`) and raises the stack count; Hoarded Power is a separate dummy chance not to consume it. |
| **Power Nexus 369908** | E0 `E6/A418`, base 1, `m0=19`. | `PowerType 19` is Essence. `PowerType.csv` gives maximum/default 5 and peace/combat regeneration 0.2; Power Nexus adds one maximum Essence. |
| **1259171 Duplicate** | E0 `E28`, base 20, `m0=253466`, `m1=6515`, T80; E1 `E6/A4`, base 75; E2 `E6/A108`, base 25, mask `[0,71303168,0,0]`. Duration 20,000 ms. | Current text says the future self casts Eruption, Fire Breath, and Upheaval; sibling talents extend its duration by 50% of Ebon extensions and raise Ebon's granted stats by 75% while active. 1259172 Eruption is `E2`, T53/16, coef 2.0. Duplicate Fire Breath charge 1283718 has no current `SpellEmpowerStage` rows. **C:** SimC implements Duplicate as a guardian with its own child actions. |
| **1260745 Command Squadron / 1236970 Pyre** | Talent raw values are 10, 2, and 8. Child Pyre 1236970 E0 `E2`, T53/16, coef 2.51999998093, variance .05, EffectAttr raw 6. | Current text summons Dracthyr assistance during Breath of Eons/Deep Breath, up to 8 Pyres. **C:** SimC models the squadron as guardians, making ordinary player ownership insufficient for attribution. |

### Duration and cooldown mutation (D)

- **395153 Sands of Time:** E0/E1/E2/E3 are `E6/A4`, bases 1,000, 2,000, 5,000,
  and 50. Current text maps these to Eruption, empower spells, Breath of Eons/Deep Breath, and
  a critical extension adding 50% respectively.
- **404977 Time Skip:** E0/E3/E4/E5 are `E6/A143`, base 1,000, with `m0` label IDs
  1216/1425/1523/1862. E1/E2/E6/E7 are `E6/A148`, base 1,000, with `m0` charge-category
  IDs 1948/2100/2207/2456. Duration 2,000 ms; cooldown 180,000 ms.
- **412713 Interwoven Threads:** E0/E3/E4/E5 are `E6/A218`, base -10, op 11
  (`Cooldown`), labels 1216/1425/1523/1862. E1/E2/E6/E7 are `E6/A454`, base -10, with
  charge categories 1948/2100/2207/2456.
- **407876 Accretion:** root E0 is `E6/A42`, `trig=411932`. Child 411932 E0 is
  **SpellEffect raw 291**, base -1,000, `m0=224`, `m1=60`, T1. Trinity's current enum calls raw
  291 `SPELL_EFFECT_MODIFY_COOLDOWNS` and documents `misc0=SpellFamily`, `misc1=family-flag bit`.
- **431715 Nozdormu Adept:** the Aug branch is E2 `E6/A453`, base -2,000,
  `m0=2207` (Prescience charge category), plus E3 `E6/A107`, base 1, op 3 with mask word 2
  `512`, matching the current +1% Prescience crit text. Other effects on the record serve the other
  hero-spec branch.
- Charge-category identities are not SpellLabels: 1948 = Hover (1 charge/35,000 ms), 2100 =
  Obsidian Scales (1/90,000), 2207 = Prescience (2/12,000), and 2456 = Fire Breath
  (1/30,000). The coincidence that a numeric value may also exist as a LabelID does not merge the
  namespaces.

## Cross-actor provenance and attribution (C unless marked D)

| Flow | Provenance retained by the inspected consumer | Attribution consequence |
|---|---|---|
| Aug -> Ebon on ally -> ally attacks/heals | Target data is scoped to the source Aug; active allies are tracked per Aug. The buff value is computed from that Aug's live primary stat, excluding allied Ebon contributions. | The ally owns the later ordinary action; the Aug-owned modifier must remain separately identifiable. |
| Aug -> Shifting Sands on ally -> ally acts | Target buff is source-Aug scoped and periodically re-evaluates the Aug's current mastery. | Ordinary output belongs to the ally, but its Versatility contribution depends on Aug state. |
| Aug -> Temporal Wound on enemy -> multiple actors deal damage -> delayed release | Per-contributor accumulated amounts plus a retained Aug pointer. Pet events are accepted. | One delayed action is created per contributor; consumer action owner is the contributor, causal state is the Aug. |
| Aug -> Prescience/Fate Mirror on ally -> ally or pet deals damage/healing | Callback is attached to the buffed ally, accepts pet procs, copies event result amount, and retains Aug state. | Echo action is owned by the buffed actor in the consumer, not simply by the Aug. |
| Aug -> Inferno's Blessing on ally -> ally or pet acts | Same external-action pattern; pet procs allowed; target follows triggering action. | Extra damage action belongs to buffed actor while scaling/state points to Aug. |
| Aug -> Blistering Scales on ally -> melee attacker hits ally | Callback lives on the ally, permits pet events, stores Aug, and damages the attacking actor. | Retaliation is an external action on the buffed ally with Aug-derived spell power/armor state. |
| Aug -> Bombardments mark -> ally attacks marked enemy | Callback gets/creates a Bombardments action on the triggering player and stores Aug. Pet procs are not enabled. | Triggering ally is consumer action owner; Aug is causal source. Exact log ownership remains unverified. |
| Aug -> Melt Armor on enemy -> later Essence/Bombardment hit | **D:** Aura 271 is explicitly “damage from caster's spells,” with an effect class mask. | Applicability is source-relative; same-named damage from another source does not automatically qualify. |
| Aug -> Chrono Ward -> contributors deal Temporal Wound damage -> shields | **D text:** each contributing ally gains an Aug-capped shield. | Requires contributor identity to survive the delayed-damage aggregation. Exact log fields are unknown. |

The SimC external-action base deliberately separates the actor that owns the action from an
`evoker_t*` stored in action state and uses the latter for source statistics and multipliers. That is
direct executable evidence that ordinary single-owner spell semantics cannot represent all current
Aug mechanics. It is not proof of Blizzard combat-log field assignment.

## Target selection, target caps, refresh, and extension

### Direct current-data facts

- Ebon Might uses T18/31 (caster destination -> destination-area ally), a 30 yd radius record, and
  `MaxTargets=30`; E2 raw base 2 is the threshold referenced by the current split text.
- Prescience has an area-search effect T18/31 plus explicit ally T21 and `MaxTargets=1`; its
  current text supplies nearest-within-25-yards, damage-dealer preference, and active-cooldown
  preference.
- Shifting Sands uses T18/31 and `MaxTargets=1`; its mastery text supplies damage-dealer
  preference.
- Inferno's Blessing uses T18/31, a 100 yd radius record, and `MaxTargets=2` (self plus one ally in
  the text).
- Blistering Scales and Timelessness each have `MaxTargets=1` and direct ally/raid target forms.
- Fire Breath uses front destination/cone targets T47/104; the description supplies reduction above
  five targets. Upheaval and split-AoE payloads use T53/16.
- Ebon and Shifting carry periodic dummy effects. Ebon, its self aura, and Fire Breath have
  SpellAttribute raw 436 (`PeriodicRefreshExtendsDuration`).

### Consumer semantics, not data authority

- SimC's Ebon auto-selection excludes pets and non-DPS roles, handles self first, and tracks all
  source-owned active Ebon buffs. It extends each active instance and performs the 50% Sands of Time
  extension-crit roll.
- SimC's Shifting selection excludes pets and prioritizes DPS and targets not already carrying the
  source's Shifting Sands.
- SimC's Fire Breath at current empower release gives Inferno's Blessing to self and a random member
  of `allies_with_my_ebon` when one exists.
- SimC's Motes of Possibility randomly selects among Inferno's Blessing, Prescience, Shifting Sands,
  and Symbiotic Bloom with role-sensitive rerolls. The source contains TODO/modeling comments, so
  its precise random target policy is not authoritative.

### Unknowns

- The complete server tie-breaker when multiple equal eligible allies exist, especially at range
  boundaries or with overlapping Aug sources.
- Whether the 26.5 yd radius row or the formatted 25 yd Prescience text is the operative cutoff in
  every context.
- Exact stage-to-radius values for Upheaval. Current text says the radius grows; the effect rows and
  inspected consumer do not reproduce a complete spatial mapping.
- Refresh versus extend caps for every combination of mastery duration scaling, Sands of Time,
  Double-time, Prolong Life, Mighty Inferno, Extended Battle, and Duplicate.

## Spell labels, class masks, and modifier operations

### Authoritative label membership (D)

`SpellLabel.csv` is authoritative membership. Human-readable names for a few broad labels below are
reproduced from the current source consumer and should not be generalized beyond membership.

| LabelID | Membership fact | Representative relevant members / use |
|---:|---|---|
| 16 | 16,546 members; broad “Class Spells” consumer name | Nearly every surveyed class spell; not Aug-specific. |
| 292 | 21,239 members; broad shared namespace | Nearly every surveyed spell; not a useful Aug-only discriminator. |
| 1216 | 752 members; “Evoker Spells” | Used by Time Skip and Interwoven Threads. |
| 1219 | 24 members | Includes 360828 Blistering Scales damage and 410265 Inferno's Blessing damage. |
| 1464 | 34 members; “Red Evoker Spells” | 357208/357209 Fire Breath, 431443/431583 Chrono Flame records. |
| 1467 | 64 members; “Bronze Evoker Spells” | Prescience, Breath/Temporal Wound, Fate Mirror, Timelessness, Bronze Attunement. |
| 1468 | 37 members; “Black Evoker Spells” | Ebon Might, Eruption, Upheaval, Blistering Scales, Black Attunement, Bombardments damage. Used by Might of the Black Dragonflight. |
| 588 | 48 members | Includes 396288 Upheaval release. |
| 2695 | 10 members | Includes 403631 Breath of Eons. |
| 2706 | 23 members | Includes 357209 Fire Breath release. |
| 3022 | 813 members | Shared by Bombardments root/debuff/driver/damage, but too broad to call a Bombardments-only label. |
| 2663 | exactly 1 member | 410686 Symbiotic Bloom. |
| 3885 | exactly 3 members | 408233 Bestow Weyrnstone, 412710 Timelessness, 1289631 Timelessness; used by Arcane Reach's label modifier. |
| 5098 | exactly 3 members | 436335/436336 Mass Disintegrate and 438588 Mass Eruption; used by Concentrated Power. |
| 5137 | exactly 1 member | 431442 Chrono Flame; used by Overclock. |
| 5959 | exactly 1 member | 410089 Prescience buff. |
| 6674 | exactly 1 member | 412774 Fate Mirror. |
| 5607 / 5608 | exactly 1 member each | Current Scalecommander 11.2 set 2pc/4pc records; used by effects 8/9 of specialization passive 396186. |

Fire Breath 357208/357209 has labels 16, 292, 1216, 1464 (release also 2706). Ebon Might
395152 has 16, 292, 1216, 1468. Breath of Eons 403631 has 16, 292, 690, 930, 991,
1216, 1467, and 2695. Temporal Wound 409560 has 16, 292, 1216, 1467. Prescience buff
410089 has 16, 292, 1216, 1467, and 5959. Fate Mirror talent 412774 has 16, 292,
1216, 1467, and 6674.

### Exact modifier/property operations (D identity; C names)

Trinity's current `SpellModOp` enum supplies the reproduced names. The operation number is stored in
`misc0` for Aura 107/108 and Aura 218/219. Aura 107/108 use four-word class masks; Aura 218/219 use
`misc1` as a `SpellLabel` ID.

| Raw op | Reproduced source name | Representative Aug use |
|---:|---|---|
| 0 | HealingAndDamage | Ebon self modifiers; Refined Essence; Might of the Black Dragonflight label modifier. |
| 1 | Duration | Golden Opportunity's alternate-spec branch. |
| 3 | PointsIndex0 | Specialization passive/set labels; Nozdormu Adept Prescience crit; Hardened/Nimble; Concentrated Power. |
| 5 | Range | Arcane Reach. |
| 6 | Radius | Arcane Reach. |
| 7 | CritChance | Unyielding Domain effect. |
| 10 | ChangeCastTime | Chronal Dynamo. |
| 11 | Cooldown | Interwoven Threads label modifiers; Chronoboon class-mask modifier. |
| 12 | PointsIndex1 | Specialization set label; Golden Opportunity. |
| 14 | PowerCost0 | Essence Burst; one Nozdormu Adept branch. |
| 17 | ChainTargets | Mass Disintegrate child 436336. |
| 22 | PeriodicHealingAndDamage | Ebon self; specialization passive; Refined Essence; Might of the Black Dragonflight. |
| 23 | PointsIndex2 | Overclock label 5137. |
| 37 | MaxAuraStacks | Essence Attunement. |

Aura 143 is label-scoped recovery-rate modification but does not use `SpellModOp`; its `misc0` is the
label. Aura 148/453/454 use charge-category IDs. Aura 540 uses its own support-stat property namespace
(`1`, `5`, `6` here), not `SpellModOp` and not a label.

### Relevant class-mask namespaces (D)

- All listed Evoker records with `SpellClassOptions` use `SpellClassSet=224`.
- Representative four-word `SpellClassOptions` masks: Fire Breath charge 357208
  `[32768,0,0,0]`; Fire Breath release `[1,8,0,0]`; Essence Burst `[0,2048,0,0]`;
  Blistering Scales `[4,0,24,0]`; Ebon Might `[4,16777216,10,0]`; Ebon self
  `[0,16777216,12,0]`; Eruption `[13,4194368,0,0]`; Upheaval charge
  `[0,134217728,0,0]`; Upheaval release `[17,67108864,0,0]`; Breath of Eons
  `[0,0x80000000,0,0]`; Temporal Wound `[0,16777216,136,0]`; Prescience root
  `[0,0,520,0]`; Prescience/Inferno/Shifting buffs `[0,0,8,0]`; Bombardments damage
  `[0,0,16384,0]`; Melt Armor `[1,4,0,0]`; Command Squadron Pyre `[33,64,0,0]`.
- Effect-local masks are independent of `SpellClassOptions`. Important examples are Essence Burst
  `[0x00900040,0,0,0]` for op 14; Ebon self op 0/22
  `[0,0x04000008,0,0]`; Ebon cooldown mask `[0x00008000,0x08000000,0,0]`; Melt Armor
  Aura 271 `[0,0,0x00000040,0]`; Chrono Flame override `[0,0x00100000,0,0]`.

No evidence supports substituting spell names for either label membership or class-mask membership.
The two namespaces overlap semantically but are encoded and consumed differently.

## “Threads of Fate” finding

There are two distinct families and they must not be conflated.

1. **Interwoven Threads 412713 is a currently selected Aug talent (D).** Its exact effects are the
   cooldown-by-label and charge-recovery-by-category rows listed above.
2. **Thread of Fate 431716 is present in the current spell tables but is not selected by the current
   Aug trait graph (D).** It has E0 `E6/A4`, base 15, T18/31, duration 10,000 ms,
   `MaxTargets=1`; AuraOptions chance 33 and proc mask 2,446,676. Current rows 432895 (`E2`, T6)
   and 432896 (`E10`, T21) are its damage/heal children. 431840 Master of Destiny says Essence
   spells extend all Threads by 1 sec, with modifier record 431846. Labels on 431716 include 16 and
   292.

The current Chronowarden node 94947 instead selects **431715 Nozdormu Adept**. No current
`TraitDefinition` traversal selects 431716, 431840, 431846, 432895, or 432896, and the inspected
SimC Evoker module does not activate the Thread proc family. It initializes a talent by the old
Master of Destiny name, but current trait lookup leaves it inactive. Therefore:

- **D:** these spell rows exist in current build data;
- **D:** they are not authoritative current Aug talent members;
- **P:** they may be dormant/compatibility residue;
- **U:** whether any server-side hidden path still invokes them outside the current trait graph.

## Core raw-identity coverage

The catalog audit inspected the current files in `crates/dbc/src`: `spell_effect.rs`,
`aura_subtype.rs`, `spell_attribute.rs`, `effect_attribute.rs`, and `implicit_target.rs`. Statuses
below are copied from the current requirement catalogs. No status change is proposed.

### Raw identities already on Core's map

#### SpellEffect

| Raw | Core identity | Status | Representative spell |
|---:|---|---|---|
| 0 | None | Ignored | Bombardments root E1 and tooltip-value effects |
| 2 | SchoolDamage | Implemented | Eruption, Fire Breath, delayed Breath, Bombardments |
| 3 | Dummy | Unimplemented | Ebon threshold, Prescience targeting, empower roots |
| 6 | ApplyAura | Implemented | Most state carriers |
| 10 | Heal | Implemented | Fate Mirror heal |
| 28 | Summon | Unimplemented | Duplicate, Defy Fate child |
| 38 | Dispel | Disabled | Fire Breath release E5 |
| 68 | InterruptCast | Disabled | Quell |
| 144 | DirectionalKnockback | Unimplemented | Upheaval release E2/E3 |
| 202 | ApplyPlayerPetAura | Disabled | Prescience buff E1; Trinity calls raw 202 `APPLY_AREA_AURA_SUMMONS` |

#### AuraSubtype

| Status | Raw identities in the surveyed current graph |
|---|---|
| Implemented | 3 PeriodicDamage; 69 AbsorbDamage; 118 ModHealingReceivedPercent; 133 IncreaseHealthPercent; 148 ModChargeCooldownRechargeRateCategory; 271 ModDamageTakenFromCasterSpells; 286 ModCooldownRechargeRatePercent; 418 ModMaxResource; 453 ModRechargeTimeCategory; 454 ModRechargeTimePercentCategory |
| Disabled | 42 ProcTriggerSpell; 107 AddFlatModifier; 108 AddPercentModifier; 147 CreatureImmunities; 218 AddPercentLabelModifier; 219 AddFlatLabelModifier; 226 PeriodicDummy; 263 DisableAbilities; 316 AbsorbOverkillDamageFromSchool; 332 OverrideActionSpell; 379 ModManaRegenPercent; 429 ModPetDamageDone; 531 ModGuardianDamageDonePercent; 537 ModDamageTakenFromCasterSpellsLabel |
| Unimplemented | 4 Dummy; 10 Threat; 33 ModDecreaseMovementSpeed; 129 ModIncreaseMovementSpeedStacking; 142 ModBaseResistance |
| Ignored | 188 ModUiHealingRange |

#### EffectAttribute

Effect attributes are flattened as **one-based** raw identities (`bit + 1`) in Core.

| Raw | Core identity | Status | Representative spells |
|---:|---|---|---|
| 6 | AlwaysAoeLineOfSight | Disabled | Eruption 395160, Command Squadron Pyre 1236970, Duplicate Eruption 1259172 |
| 7 | SuppressPointsStacking | Implemented | Blistering Scales, Fate Mirror, Overclock/Mass Disintegrate child |
| 12 | AddTargetCombatReachToAoe | Disabled | Blistering Scales damage, Prescience search |
| 16 | ComputePointsOnlyAtCastTime | Disabled | Inferno's Blessing buff, Shifting Sands |
| 18 | AreaEffectsUseTargetRadius | Disabled | Fire Breath release, Upheaval release |
| 21 | DoNotFailSpellOnTargetingFailure | Disabled | Prescience explicit-target effect |

No surveyed exact-effect attribute is absent from Core.

#### SpellAttribute

All current cataloged bits found anywhere in the surveyed graph are listed, grouped by current Core
status:

- **Implemented:** 6 Passive; 93 CannotCrit; 114 AlwaysHit; 135 AllowCastWhileCasting;
  169 TickOnApplication; 173 SpellHasteAffectsPeriodic; 219 IgnoreHealingModifiers;
  221 IgnoreCasterDamageModifiers; 265 PeriodicCanCrit; 436 PeriodicRefreshExtendsDuration.
- **Disabled:** 4 Ability; 16 NotShapeshifted; 34 Channeled; 37 AllowWhileStealthed;
  38 SelfChanneled; 39 NoReflection; 66 IgnoreLineOfSight; 105 NotAProc;
  112 SuppressCasterProcs; 113 SuppressTargetProcs; 136 DisableTargetMultiplier;
  151 SuppressWeaponProcs; 186 RequiresLineOfSight; 273 HasteAffectsDuration;
  285 MasteryAffectsPoints; 308 AllowCastWhileChanneling; 321 DisableTargetPositiveMultiplier;
  415 OnlyProcFromClassAbilities; 416 AllowClassAbilityProcs; 428 DoNotConsumeAuraStackOnProc.
- **Unimplemented:** 24 AllowWhileMounted; 27 AllowWhileSitting; 29 NoImmunities; 31 NoAuraCancel;
  35 NoRedirection; 42 NoThreat; 49 NoAutoCastAi; 92 NotAnAction; 115 InstantTargetProcs;
  116 AllowAuraWhileDead; 134 CannotBeStolen; 147 AllowProcWhileSitting; 148 AuraNeverBounces;
  149 AllowEnteringArena; 163 AllowWhileStunned; 177 AllowWhileFleeing; 178 AllowWhileConfused;
  204 AllowWhileRidingVehicle; 214 AbsorbCannotBeIgnored; 215 TapsImmediately;
  218 VehicleImmunityCategory; 226 DisableAuraWhileDead.
- **Ignored:** 7 Hidden; 8 DoNotLog; 18 DoNotSheath; 60 NoAuraIcon;
  72 IncludeAdvancedCombatLog; 103 DotStackingRule; 126 DoNotDisplayRange;
  143 NotInSpellbook; 202 NoAuraLog; 268 AuraPointsOnClient;
  269 NotInSpellbookUntilLearned; 310 SpellcastOverrideInSpellbook;
  333 ResetCooldownOnEncounterEnd; 355 DoNotLogOnLearn; 423 NameplatePersonalAura;
  426 NameplateEnemyDebuff; 441 DoNotDisplayCastTime.

#### Implicit targets

Every target raw observed is present in Core's complete 0..152 identity and semantic catalog:

`0 None`; `1 UnitCaster`; `6 UnitTargetEnemy`; `16 UnitDestinationAreaEnemy`;
`18 DestinationCaster`; `21 UnitTargetAlly`; `25 UnitTargetAny`;
`31 UnitDestinationAreaAlly`; `47 DestinationCasterFront`; `53 DestinationTargetEnemy`;
`57 UnitCasterTargetRaid`; `78 DestinationDestinationFront`;
`80 DestinationDestinationRight`; `87 DestinationDestination`;
`104 UnitConeCasterToDestinationEnemy`.

Core explicitly documents `IMPLICIT_TARGET_SEMANTICS` as descriptive and says it does **not** declare
runtime support. Consequently these rows have no Implemented/Disabled/Unimplemented/Ignored
requirement status to report; their exact current status is **cataloged semantic tuple, runtime support
N/A**. Raw 0's selection component is `NotImplemented`; that is a semantic tuple field, not a
requirement-catalog status.

### Raw identities missing from Core's map

#### Mechanically central missing identities

| Domain | Raw | Reproduced source name / exact use |
|---|---:|---|
| SpellEffect | **291** | Trinity `SPELL_EFFECT_MODIFY_COOLDOWNS`; Accretion child 411932 E0, base -1000, `m0=224`, `m1=60`. |
| AuraSubtype | **22** | Trinity `SPELL_AURA_MOD_RESISTANCE`; Blistering Scales E0. |
| AuraSubtype | **143** | `MOD_RECOVERY_RATE_BY_SPELL_LABEL`; Time Skip label-scoped recovery. |
| AuraSubtype | **203** | `PREVENT_INTERRUPT`; Unyielding Domain. |
| AuraSubtype | **269** | `MOD_IGNORE_TARGET_RESIST`; Menacing Presence debuff 441201. |
| AuraSubtype | **485** | `MOD_MOVEMENT_FORCE_MAGNITUDE`; Breath of Eons flight state. |
| AuraSubtype | **488** | `DISABLE_GRAVITY`; Breath of Eons flight state. |
| AuraSubtype | **540** | `MOD_SUPPORT_STAT`; Ebon (`m0=1`), Prescience (`5`), Shifting Sands (`6`). |
| AuraSubtype | **647** | `ADD_PCT_PVP_MODIFIER`; specialization passive 1256942. |

There are no missing surveyed EffectAttribute identities and no missing surveyed implicit-target
identities.

#### SpellAttribute bits absent from Core

Names below are reproduced from Trinity's current enum when known; `UNK` names remain unknown and
are not renamed. Representative records demonstrate occurrence, not necessarily mechanical
importance.

| Raw | Reproduced source identity | Representative surveyed record(s) |
|---:|---|---|
| 23 | ALLOW_CAST_WHILE_DEAD | 409632 delayed Breath damage |
| 26 | AURA_IS_DEBUFF | 409560 Temporal Wound |
| 51 | EXCLUDE_CASTER | 395152 Ebon Might; 408233 Weyrnstone |
| 57 | AURA_STAYS_AFTER_COMBAT | 396286 Upheaval |
| 61 | NAME_IN_CHANNEL_BAR | Fire Breath, Upheaval, Time Skip |
| 62 | DISPEL_ALL_STACKS | Blistering Scales |
| 68 | USE_SHAPESHIFT_BAR | Black/Bronze Attunement |
| 78 | ALLOW_WHILE_INVISIBLE | Defy Fate child |
| 94 | ACTIVE_THREAT | Scales/Inferno/Chrono Flame damage |
| 104 | ONLY_ON_PLAYER | Ebon, Prescience, Shifting Sands |
| 124 | IGNORE_CASTER_AND_TARGET_RESTRICTIONS | delayed Breath damage |
| 142 | REACTIVE_DAMAGE_PROC | Blistering Scales damage |
| 155 | FORCE_DISPLAY_CASTBAR | Fire Breath, Upheaval |
| 165 | LIMIT_N | Blistering Scales, Ebon, Timelessness |
| 168 | NOT_ON_PLAYER_CONTROLLED_NPC | Timelessness |
| 171 | IMPLIED_TARGETING | Blistering Scales |
| 183 | NO_PARTIAL_RESISTS | delayed Breath damage |
| 185 | ALWAYS_LINE_OF_SIGHT | Breath of Eons |
| 194 | NOT_AN_ATTACK | Defy Fate child |
| 198 | FLOATING_COMBAT_TEXT_ON_CAST | Essence Burst |
| 216 | CAN_TARGET_UNTARGETABLE | Prescience buff |
| 225 | NO_TARGET_DURATION_MOD | Temporal Wound |
| 252 | DO_NOT_COUNT_FOR_PVP_SCOREBOARD | Defy Fate |
| 261 | ALLOW_WHILE_CHARMED | Fire Breath/Upheaval release; Defy Fate child |
| 264 | USE_TARGETS_LEVEL_FOR_SPELL_SCALING | Timelessness |
| 280 | HEAL_PREDICTION | Chrono Flames override |
| 299 | ALLOW_WHILE_BANISHED_AURA_STATE | Defy Fate child |
| 320 | UNK0 | delayed Breath damage |
| 332 | USE_SPELL_BASE_LEVEL_FOR_SCALING | Upheaval release; Command Squadron Pyre |
| 342 | UNK22 | Defy Fate child |
| 362 | UNK10 | Black/Bronze Attunement |
| 374 | UNK22 | Breath of Eons |
| 378 | UNK26 | Defy Fate child |
| 395 | UNK11 | Breath of Eons |
| 417 | UNK1 | Defy Fate child |
| 419 | UNK3 | Quell |
| 430 | UNK14 | Upheaval; Chrono Flames override |
| 443 | DO_NOT_ALLOW_DISABLE_MOVEMENT_INTERRUPT | Fire Breath/Upheaval charge, Duplicate Fire Breath |
| 444 | UNK28 | Black/Bronze Attunement |
| 450 | UNK2 | Chrono Flame talent |
| 456 | UNK8 | Bombardments driver 443788 |
| 458 | UNK10 | Breath of Eons |

## Required raw-identity quick inventory

### SpellEffect

`raw 0 — None — tooltip/no-op value rows`; `2 — SchoolDamage — direct, periodic-release, delayed,
and proc payloads`; `3 — Dummy — empower and targeting drivers`; `6 — ApplyAura — all state
families`; `10 — Heal — Fate/Thread heal`; `28 — Summon — Duplicate/Defy Fate child`;
`38 — Dispel — Fire Breath`; `68 — InterruptCast — Quell`; `144 — DirectionalKnockback —
Upheaval`; `202 — ApplyPlayerPetAura/Core, APPLY_AREA_AURA_SUMMONS/Trinity — Prescience`;
`291 — MODIFY_COOLDOWNS — Accretion`.

### AuraSubtype

`3 PeriodicDamage`; `4 Dummy`; `10 Threat`; `22 ModResistance`; `33 ModDecreaseMovementSpeed`;
`42 ProcTriggerSpell`; `69 SchoolAbsorb`; `107 AddFlatModifier`; `108 AddPctModifier`;
`118 ModHealingPct`; `129 ModSpeedAlways`; `133 ModIncreaseHealthPercent`;
`142 ModBaseResistancePct`; `143 ModRecoveryRateBySpellLabel`; `147 MechanicImmunityMask`;
`148 ModChargeRecoveryRate`; `188 ModUIHealingRange`; `203 PreventInterrupt`;
`218 AddPctModifierBySpellLabel`; `219 AddFlatModifierBySpellLabel`; `226 PeriodicDummy`;
`263 DisableCastingExceptAbilities`; `269 ModIgnoreTargetResist`; `271 ModSpellDamageFromCaster`;
`286 ModRecoveryRate`; `316 SchoolAbsorbOverkill`; `332 OverrideActionbarSpells`;
`379 ModManaRegenPct`; `418 ModMaxPower`; `429 ModSummon/PetDamage`; `453 ChargeRecoveryMod`;
`454 ChargeRecoveryMultiplier`; `485 ModMovementForceMagnitude`; `488 DisableGravity`;
`531 raw 531 / Core ModGuardianDamageDonePercent`; `537 ModSpellDamageFromCasterByLabel`;
`540 ModSupportStat`; `647 AddPctPvpModifier`.

### Exact-effect attributes

`raw 6 AlwaysAoeLineOfSight — Eruption/Pyre`; `7 SuppressPointsStacking — Scales/Fate`;
`12 AddTargetCombatReachToAoe — Scales damage/Prescience`; `16 ComputePointsOnlyAtCastTime —
Inferno/Shifting`; `18 AreaEffectsUseTargetRadius — Fire Breath/Upheaval`;
`21 DoNotFailSpellOnTargetingFailure — Prescience`.

### Implicit targets

`raw 0 — None`; `1 — caster unit`; `6 — explicit enemy unit`; `16 — enemy units in destination
area`; `18 — caster destination`; `21 — explicit ally unit`; `25 — any explicit unit`;
`31 — ally units in destination area`; `47 — destination in front of caster`;
`53 — enemy target destination`; `57 — caster's raid target`; `78 — destination in front of a
destination`; `80 — destination to the right of a destination`; `87 — existing destination`;
`104 — enemy cone from caster toward destination`.

### Modifier/property operations

`raw 0 HealingAndDamage`; `1 Duration`; `3 PointsIndex0`; `5 Range`; `6 Radius`;
`7 CritChance`; `10 ChangeCastTime`; `11 Cooldown`; `12 PointsIndex1`; `14 PowerCost0`;
`17 ChainTargets`; `22 PeriodicHealingAndDamage`; `23 PointsIndex2`; `37 MaxAuraStacks`.

### Labels and masks

The exact relevant label identities are 16, 292, 588, 690, 930, 991, 1216, 1219, 1425,
1464, 1467, 1468, 1523, 1862, 2663, 2669, 2695, 2706, 2840, 3022, 3885, 5098,
5137, 5607, 5608, 5959, and 6674. Charge categories 1948, 2100, 2207, and 2456 are a
separate namespace. Spell-family masks use class set 224 and four 32-bit words; representative exact
masks are listed above.

## Plausible relationships requiring further verification

- The periodic dummy effects on Ebon and Shifting align with SimC's live source-stat refresh, but
  DBC alone does not prove whether Blizzard snapshots, re-evaluates every tick, or updates on another
  event.
- `ApplyPlayerPetAura`/`APPLY_AREA_AURA_SUMMONS` on Prescience strongly links the buff to controlled
  summons, and SimC allows pet-driven Fate Mirror events. Exact guardian inclusion/exclusion rules
  for every pet type remain unverified.
- Bombardments' 434473 30% proc row and 443788 100%/2-sec driver likely partition local and external
  triggering contexts. The inspected consumer also treats them differently, but the exact server
  division is not reproduced.
- `MOD_SUPPORT_STAT` property values 1/5/6 align with current descriptions and SimC assignments to
  primary hybrid stat/Crit/Versatility. No inspected DBC enum authoritatively names those three
  `misc0` values.
- Duplicate's summon row and the current consumer establish a guardian family, but the exact
  ownership/logging of each copied Eruption, Fire Breath, and Upheaval event remains unverified.

## Unknowns

- Blizzard combat-log attribution fields for Temporal Wound, Fate Mirror, Inferno's Blessing,
  Blistering Scales, Bombardments, Chrono Ward, Duplicate, and Command Squadron. SimC's owner/source
  split is evidence of necessary provenance, not a combat-log specification.
- Exact proc eligibility filters behind raw proc masks 664,232; 2,446,676; and 332,116 for every
  action/result combination, including absorbs, overheal, periodic events, multistrikes, pets, and
  proc-from-proc recursion.
- Exact DBC-free coefficients for Blistering Scales (consumer 0.3) and Inferno's Blessing (consumer
  2.69), and whether those constants vary by hidden tuning records.
- Exact Bombardments external cadence and whether pet/guardian attacks can trigger it in all current
  contexts.
- Server target ordering and replacement rules for overlapping Ebon, Prescience, Shifting Sands,
  Inferno's Blessing, and Motes from multiple Augmentation Evokers.
- Full server treatment of Ebon contributions to a second Aug's own primary-stat calculation. SimC
  explicitly excludes allied Ebon contributions, but the current DBC rows do not encode this.
- Exact empower hold/release timing beyond the stage-duration rows. SimC uses levels 1-4, nominal
  cumulative times 1000/1750/2500/3250 ms plus a maximum hold window, but source comments retain
  timing TODOs.
- Whether dormant Thread of Fate records have any non-trait hidden activation in live server code.

## Evidence locations

- Build metadata: `wowlab-data/changes/metadata/12.1.0.69497.json`
- Current tables: `wowlab-data/data/tables/ChrSpecialization.csv`, `Trait*.csv`,
  `SpellEffect.csv`, `SpellMisc.csv`, `SpellAuraOptions.csv`, `SpellTargetRestrictions.csv`,
  `SpellCooldowns.csv`, `SpellCategories.csv`, `SpellCategory.csv`, `SpellPower.csv`,
  `PowerType.csv`, `SpellEmpower.csv`, `SpellEmpowerStage.csv`, `SpellClassOptions.csv`, and
  `SpellLabel.csv`
- Executable Aug consumer: `simc/engine/class_modules/sc_evoker.cpp`
- Consumer identity names and operation semantics:
  `TrinityCore/src/server/game/Miscellaneous/SharedDefines.h`,
  `TrinityCore/src/server/game/Spells/Auras/SpellAuraDefines.h`, and
  `TrinityCore/src/server/game/Spells/Auras/SpellAuraEffects.cpp`
- Core catalogs: `core/crates/dbc/src/{spell_effect,aura_subtype,spell_attribute,effect_attribute,implicit_target}.rs`
