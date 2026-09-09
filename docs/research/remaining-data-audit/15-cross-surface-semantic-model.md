# Cross-surface semantic model

## Result

The current corpus supports a staged semantic interpretation, but not a single flattened spell record.
Facts become valid at different times and have different owners:

1. a raw source row is identified by table, row ID, spell/effect coordinate, and difficulty;
2. independently optional components and indexed children are selected along the requested difficulty
   route;
3. immutable predicates and definition edges can be prebound;
4. an effect/aura/item operation is assembled without inventing runtime values;
5. cast, target, proc, amount, duration, and cooldown behavior is evaluated against mutable actor/event/
   world state;
6. inventory, map transfer, persistence, service, and collection mutations remain host-authoritative.

This layered boundary is **strongly verified** across Wago `12.1.0.69497`, TrinityCore's generic consumers,
SimulationCraft's deliberately narrower execution model, and the complete-population outliers in reports
01–14. It is a research model and evidence map, not a production architecture proposal.

## What can be resolved before runtime

| Fact | Safe static operation | Required provenance |
|---|---|---|
| difficulty graph | validate and construct ordered fallback route | requested difficulty and every traversed edge |
| singleton spell component | select first available row on the route | selected source difficulty and row ID |
| effects | select independently per effect index | effect index, source difficulty, row ID |
| powers | select independently per order index | order index, mapped difficulty, row ID |
| family/class mask | operation-specific predicate; spellmods require exact family equality and count intersecting mask bits | owning spell membership plus selecting effect/aura and all mask words |
| label/category membership | exact set membership | edge row and definition identity |
| school/mechanic masks | bit intersection or typed union where the consumer proves it | source field, owner, and component route |
| effect/aura/attribute identities | exact raw identity | spell/effect owner and raw namespace |
| range/radius/duration definitions | bind exact referenced record, retaining zeros/outliers | source reference and definition row |
| item/bonus/set edges | bind static graph reachability and selectors | source item/definition/edge identity |

Prebinding means recording a validated relationship, not executing it. For example, a family-mask
intersection can be represented statically, but whether an aura modifier currently applies still depends
on aura ownership, stacks, effect mask, and the action being evaluated. A teleport destination can be
looked up, but world admission and transfer cannot.

## Orthogonal selectors and apparent duplication

**Strongly verified — selectors form predicates over different domains.** Family masks select spell-family
bits; labels and categories are exact memberships; mechanics can be a spell/effect union; schools are bit
intersections; effect/aura raws select operation kinds; implicit targets select objects/destinations;
explicit target masks specify cast payload requirements; equipment/form fields constrain the actor; power
type selects a resource; attributes introduce exceptions. Co-occurrence does not collapse any pair.

**Strongly verified — spell and effect ownership cannot be erased.** Category mechanic is spell-owned
fallback while effect mechanic is effect-owned and can override the singular query. `SpellMisc`
attributes classify the spell coordinate while exact-effect attributes classify one effect. Owner family
membership in `SpellClassOptions` and selector masks on individual modifier/proc effects are distinct.
Target restrictions can be
spell-level while selectors and radii are effect-level.

**Strongly verified — zero is domain-specific.** A zero label list means no membership; a zero family name
in Trinity's generic `IsAffected` path acts as unscoped, and under a nonzero family a zero generic mask is
family-wide; spellmods are different and a zero spellmod class mask matches zero bits. A zero target
selector may defer to the other selector/effect contract; zero max targets
means no cap from that field; zero charge category means no charge key; zero duration/radius reference is
absence, not infinity. Global normalization of zero would change semantics.

**Rejected inference — correlation transfers ownership.** Proc spells often carry aura subtypes and
passive attributes, item spells correlate with trigger types, and periodic auras correlate with duration/
amplitude fields. The executable consumers still read distinct fields at distinct phases. An attribute
that bypasses a rule is not the owner of the underlying aura, and an aura correlation is not the meaning
of an attribute.

## Runtime composition boundaries

### Admission and targeting

Cast admission composes actor form/equipment, area/focus/facing, aura states, cooldown/charge state, power,
movement/combat constraints, and attributes. Target admission then composes explicit payload type,
implicit selector relation, alive/dead/hostility/creature rules, range/LOS/visibility/phase, immunity, and
scripts. Hit resolution and aura application are later phases. Bypassing one layer does not prove that
later layers are bypassed.

The derived explicit target mask is built from implicit selectors, effect requirements, and authored
`SpellTargetRestrictions.Targets`. Range selects friendly/hostile distance columns, while radius/cone/
line/width define effect search geometry. These facts reject a monolithic “target rule.”

### Amount, cost, and duration

Amount assembly chooses a scaling branch, may consume a variance RNG draw, then applies resource points,
mastery/caster modifiers, and operation-dependent rounding. SP, AP, budget, legacy-level, PvP, group-size,
and chain channels must remain separate until a consumer proves composition.

Power rows are conditional ordered terms selected per difficulty/order index. Flat, percent, optional, and
periodic costs use different bases/phases; signed negative totals can grant resources. Form/resource state
and required aura are runtime inputs. Payment, insufficient-resource failure, periodic aura removal, and
refund behavior are mutations, not source metadata.

Duration definition, calculated max duration, current aura duration, periodic timer, stack count, charges,
refresh policy, haste, and partial-tick policy are separate state. `SpellDuration` and
`EffectAuraPeriod` alone do not prove refresh or final-tick behavior.

### Proc, cooldown, and threat state

Proc eligibility combines immutable aura options/class masks/attributes with mutable aura owner, event
actor/action/target, hit result, event phase, proc history, charges, internal cooldown, and RNG. A source
proc mask is not an event occurrence. Chance and PPM selection can consume RNG; charge/cooldown mutation
ordering must be preserved from executable evidence rather than optimized as pure predicates.

Cooldown state has independent per-spell, shared-category, start-recovery, charge-queue, and aura-lock
owners. Their category references sometimes share numeric IDs but use different maps and mutation phases.
Threat similarly separates amount/multiplier, source actor, victim threat manager, engagement/combat
state, forced target/taunt, AI selection, and client feedback. “No threat” does not imply no damage,
targeting, or combat transition.

## Mutable and host-owned facts

| Runtime/local state | Host/world/inventory authority |
|---|---|
| current health/resources, auras, stacks, charges | item ownership, trade target, inventory slots, persistence |
| cooldown/category deadlines and recharge queue | cross-map transfer, reconnect/acknowledgement, service admission |
| cast/action target and hit/proc event | authoritative phase, visibility, map and transport membership |
| periodic timer and current/max aura duration | taxi/LFG/scene/world-service orchestration |
| local threat list and selected victim where modeled | collection/entitlement/learned-spell persistence |
| local trajectory inputs and collision/path query result | atomic commit/rollback of item and world mutations |

**Strongly verified — source item, caster, target item, and owner can differ.** Enchanting during trade is a
concrete executable example. Item effects also distinguish base item definition, concrete instance,
triggering event, and actor. Any early conversion to “spell belongs to actor” loses required provenance.

**Strongly verified — world transitions are not local position assignments.** Teleport admission depends
on map and actor state and has near/far paths; taxi and transport are journeys; scene/visual loading is
presentation; phase changes alter world membership. Static metadata can parameterize a host request but
cannot assert completion.

## Metadata that is unsafe to discard early

- selected source difficulty and raw row ID, including misses and fallback provenance;
- all attribute words and exact-effect bits, including currently unconsumed bits;
- both target selectors, both radii, complete range/radius records, explicit target mask, cone/width/cap;
- spell/effect mechanics separately, raw dispel/immunity payloads, school mask, prevention/diminish fields;
- family identity and every class-mask word, labels, categories, and their owning edge;
- all power rows/order indices and signed fields, even zero-payload/display-only rows;
- raw floating coefficient fields and outlier bit patterns before rounding;
- duration, period, amplitude, stack/proc/charge tuples as separate facts;
- item-effect trigger role, source item edge, bonus-list order/tree path, enchant slot tuple, set threshold;
- movement misc tuple, destination reference, trigger spell, scene/taxi/site reference and join status.

An unconsumed field is not synonymous with presentation-only or irrelevant. Conversely, a field read in a
packet/UI or AI-hint path is not automatically combat behavior. The consumer and phase are part of every
classification.

## RNG, ordering, and purity

**Strongly verified — several semantic operations are ordering-sensitive.** Scaling variance draws before
later amount modifiers; proc chance/PPM draws occur after event eligibility in the generic server path;
bonus lists execute ordered type-specific transformations; charge recharges form a serial queue; periodic
cost and aura ticks mutate state over time. Reordering independent-looking records can change results.

**Partially characterized — universal RNG stream parity.** Trinity and SimC establish concrete subsystem
orders but not a single retail-client RNG stream or deterministic relationship across server, host, and
simulation operations. A compiler should make random draws explicit and preserve source order, while
claiming retail draw-count parity only where executable evidence proves it.

**Rejected inference — failed operations are universally pure.** Cast admission can be pure in narrow
paths, but proc attempts, resource payment, cooldown starts, movement interruption, item mutation, and
world transitions have different staging and failure points. A generic rollback guarantee is unsupported.

## Derived classification boundary

Passive and channel families have explicit attribute predicates; many other classes are composites.
Positive/negative derives recursively from attributes, target hostility, effect/aura semantics, amount
sign, and triggered spells plus exceptions. Area, movement, summon, crowd-control, damage/heal, dispel,
item origin, and player/content cohorts are useful queries but not necessarily exclusive stored taxonomies.
The safe representation records evidence predicates and supports overlapping classifications rather than
forcing every spell into one label.

## Partial and unknown model edges

**Partially characterized — client-only fields with strong feature-family evidence.** PvP/group-size
scaling, several display/resource selectors, weekly category limits, diminish type, newer item bonuses,
and SeamlessSite all have populated structured data but incomplete generic executable behavior. They can
be retained as typed/opaque metadata with provenance; they cannot yet drive universal operations.

**Unknown after exhaustive available evidence — one complete universal lifecycle.** Available sources do
not establish retail ordering/atomicity across cast, proc, aura, inventory, world-service, presentation,
and persistence systems. The smallest resolving evidence is subsystem-specific client/host executable
tracing or authoritative protocol/formula documentation, not more cross-table correlation.

## Reproduction notes

The second-order pass used the complete aura/attribute/effect/selector censuses from the prior audits plus
the current report populations and reusable DuckDB facts. Cross-surface claims were accepted only when an
executable consumer established the predicate, owner, and phase; co-occurrence was used to find consumers,
not as semantic proof. The deterministic surfaces in `scripts/research/wago_research.py` retain the source
coordinates needed to repeat this model against a future build.
