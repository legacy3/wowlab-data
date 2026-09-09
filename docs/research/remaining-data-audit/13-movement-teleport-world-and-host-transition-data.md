# Movement, teleport, world, and host-transition data

## Scope and result

This audit covers movement-bearing spell effects, their payloads and selectors, `Map`, `MapDifficulty`,
`Location`, `SeamlessSite`, phase relations, taxi nodes/paths/path nodes, transport animation/physics/
rotation, and scene-package references. The decisive boundary is that DB2 supplies destination and motion
parameters, while executable runtime code supplies path validation, collision/LOS, transfer admission,
transport attachment, acknowledgements, phase/world membership, and transactional authority.

Evidence versions are Wago `12.1.0.69497` (`2ddced452a6f9076de5c86bc92f73de5b60f8556`), Core
`9bd3b8ed6f0f57587f443b857204318e7339b038`, TrinityCore
`7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f`, and SimC
`b48def9c26d7532db2e612d97d433ec66bd8eede`. DuckDB 1.5.5 evaluated the complete current population.

## Movement-effect census

| Effect raw | Conservative family | Rows | Spells | Nonzero misc0 / misc1 / trigger |
|---:|---|---:|---:|---:|
| 13 | teleport to aura-stored return point | 23 | 23 | 23 / 0 / 0 |
| 15 | teleport visual/loading transition | 826 | 824 | 717 / 593 / 2 |
| 29 | leap | 103 | 103 | 0 / 1 / 0 |
| 41, 42 | jump to unit / destination | 374 / 1,729 | 372 / 1,720 | 114/122/186; 801/689/757 |
| 43 | teleport facing caster | 65 | 65 | 0 / 0 / 0 |
| 50 | transmitted/game-object creation, not actor transfer | 1,034 | 1,015 | 1,031 / 17 / 2 |
| 85 | summon player | 1 | 1 | 0 / 0 / 0 |
| 96, 149 | charge / charge destination | 746 / 653 | 743 / 651 | 3/108/297; 0/8/323 |
| 98, 144 | knockback / knockback destination | 5,315 / 1,778 | 5,042 / 1,637 | 4,534/516/55; 1,632/256/15 |
| 124, 145 | pull toward unit / destination | 145 / 30 | 144 / 30 | 98/0/1; 25/0/0 |
| 138 | leap back | 311 | 310 | 270 / 30 / 5 |
| 213 | second jump-destination form | 142 | 142 | 17 / 45 / 10 |
| 252 | teleport units variant | 7,373 | 7,359 | 438 / 8 / 47 |
| 254 | jump/charge with parameter record | 1,530 | 1,520 | 1,527 / 33 / 27 |
| 120 | send to graveyard | 2 | 2 | 0 / 0 / 0 |
| 123, 154 | send taxi / discover taxi node | 397 / 959 | 396 / 192 | 392/0/0; 954/0/0 |
| 191 | dig-site operation | 2 | 2 | 0 / 0 / 0 |
| 195, 198 | scene package / play scene | 10 / 245 | 10 / 245 | 10/0/0; 244/0/0 |
| 227 | LFG/world transition family | 356 | 269 | 136 / 353 / 4 |

The heterogeneous payload occupancy is first-class evidence. The same numeric column is a height/speed
parameter in one movement operation, a path/node/package identity in another, and a server-side parameter
record in another. No global `misc0 -> table` contract exists.

## Teleport and transfer semantics

**Strongly verified — destination selection and transfer execution are separate.** Trinity's teleport-unit
handler requires a hit unit and selected destination, chooses the current map when the authored map is an
invalid/current-map sentinel, permits player cross-map transfer, and restricts ordinary creatures to the
same map. The handler delegates to `Player::TeleportTo` or near teleport; it does not itself define the
whole transition.

`Player::TeleportTo` validates map, coordinates, disabled/expansion access, battleground state, and other
runtime constraints; interrupts movement; handles vehicles, transports, duels, pets, and combat; chooses
near versus far transfer; and participates in client acknowledgement/state update. These checks establish
that a static destination row is neither proof of admissibility nor an atomic committed position change.
Failure can occur after metadata lookup because host/world state is mutable.

**Strongly verified — same-map state retention is conditional.** Transport attachment and selected combat
state behaviors are retained or cleared according to same-map and flag conditions in the executable
path. A compiler may prebind destination metadata but cannot decide those mutations without current actor,
map, vehicle, transport, and host state.

**Strongly verified — visual loading is presentation timing.** Effect 15 uses a delay payload and a
`SpellVisualKit` identity, scheduling a delayed visual/loading event. Of 593 nonzero misc1 values, 589 join
the current visual-kit table and four do not. This operation does not itself prove local displacement or
successful world transfer. Treating visual/load presentation as movement is rejected.

**Strongly verified — effect 13 returns a player to an aura-stored location.** Its 23 nonzero misc0
values all join current `Spell`. Trinity uses misc0 as the spell ID of the aura that stored the return
location, then looks up mutable player state keyed by that ID; the referenced spell is not itself a static
destination record. The join is operation-local evidence, not a global type for `EffectMiscValue_0`.

**Rejected interpretation — effect 50 is an actor/world transfer because its historical raw name says
`TRANS_DOOR`.** Trinity's current generic `EffectTransmitted` handler interprets misc0 as a server
game-object-template entry and creates that object at an explicit or caster-relative location. That
template store can combine `GameObjects` DB2 rows with server SQL definitions, but none of the 1,031
current nonzero references (866 distinct keys) joins current `GameObjects.ID`. The keys therefore remain
unresolved in the Wago snapshot, while the executable path directly contradicts treating the operation as
a teleport.

## Jump, charge, knockback, and pull

**Strongly verified — jump geometry is contextual.** For the ordinary jump effects, Trinity interprets
misc payloads as vertical/horizontal height-like values divided by ten, can multiply speed by
`EffectAmplitude`, derives a trajectory from source/destination, and can execute
`EffectTriggerSpell` on arrival. Facing attributes can change final orientation. The authored tuple is a
trajectory input, not an instantaneous coordinate assignment.

**Strongly verified — charge adds navigation and collision policy.** Charge starts from spell speed or a
default, applies path/LOS/collision checks, and has an attribute mode in which speed is interpreted as
travel time. Arrival can initiate attack and/or cast a trigger spell. This proves composition between
effect kind, selectors/destination, `SpellMisc.Speed`, amplitude/payload, attributes, current geometry,
and trigger metadata.

**Strongly verified — knockback and pull are not signed variants of one operator.** Knockback consumes
horizontal magnitude from misc0 divided by ten and vertical magnitude from calculated effect value divided
by ten, checks root/boss/other runtime constraints, and emits knockback proc context. Pull computes motion
toward a selected point/unit and uses a distinct speed/default path. Direction, owner, admission, and proc
side effects differ.

**Partially characterized — effect 254 parameter records.** Trinity interprets misc0 as a key into its
server `jump_charge_params` data, with speed/time, heights, unlimited-distance mode, visual, curve, and
arrival trigger fields. That table is a server supplement, not a Wago CSV relation. Current Wago proves
1,527 nonzero keys but cannot reconstruct the operation without the host dataset. Preserve the raw key and
make the missing dependency explicit.

## Taxi, scenes, phase, and world relations

The current world tables include 1,184 maps, 1,908 map-difficulty rows, 140,959 locations, 26,489 phases,
2,458 phase-to-phase-group edges, 297 seamless sites, 1,464 taxi nodes, 8,690 taxi paths, 127,438 taxi path
nodes, and 24,280 transport animation rows.

**Strongly verified — taxi payloads are operation-local foreign keys.** Effect 123 has 392 nonzero misc0
rows; 391 join `TaxiPath.ID`, leaving one outlier. Effect 154 has 954 nonzero misc0 rows and every one joins
`TaxiNodes.ID`. Trinity starts the path or discovers the node through separate player/taxi paths. A taxi
path is an ordered world journey with player/host state, not a direct teleport coordinate.

`TaxiPath` contains 4,516 rows with populated endpoint pairs, all of whose nonzero source and destination
nodes join. The remaining 4,174 rows have zero in each endpoint field. Zero endpoints must be retained as
an explicit authored shape; inventing an implicit node from path nodes would require separate proof.

**Strongly verified — phase membership is a relation, not local geometry.** Every current
`PhaseXPhaseGroup.PhaseID` joins `Phase.ID`. A phase/group edge can constrain visibility/world membership,
but it does not itself move an actor or authorize the host to change phase. Position and phase must remain
separate state dimensions.

**Partially characterized — scene payloads.** All ten effect-195 misc0 values join
`SceneScriptPackage.ID`, supporting that exact package relationship. Effect 198 has 244 nonzero values but
only 137 join the same table, decisively rejecting a universal package foreign key for that effect. The
remaining namespace may include scene identities or host-side records; names alone cannot distinguish it.

**Rejected inference — `SeamlessSite.MapID` is a total current `Map` foreign key or an executable transfer
contract.** Only 258 of 273 nonzero rows join the current map table (104 of 115 distinct values). Trinity
loads/stores the DB2 relation but no generic current consumer was found that defines preload, cancel,
commit, rollback, or actor mutation. The table is useful site/map context metadata; exact transition
behavior remains unknown.

**Partially characterized — transport data.** Animation, physics, and rotation tables provide static
motion parameters keyed to transport/game-object context. Runtime passenger attachment, map handoff,
collision, interpolation clock, and authoritative position remain host state. The available sources do not
establish a complete generic reconstruction from these CSVs alone.

## Rejected and unknown conclusions

**Rejected inference — every transition is a teleport.** Jump/charge, knockback/pull, taxi travel,
transport motion, scene presentation, visual loading, phase changes, and cross-map transfer have different
owners and completion protocols.

**Rejected inference — a successful numeric join proves the whole payload type.** Effect 198's partial
scene-package join, effect 123's taxi outlier, visual-kit misses, and SeamlessSite map misses are concrete
counterexamples. Joins are recorded per operation and population.

**Unknown after exhaustive available evidence — client semantics for effects 191 and 227 and the complete
misc payloads of teleport variants.** Trinity maps both the dig-site operation and the named LFG teleport
operation to `EffectNULL`; it supplies no current executable LFG/world path. SimC intentionally omits most
world transitions. Client
executable traces or an authoritative field specification are the smallest resolving evidence.

**Unknown after exhaustive available evidence — transaction ordering on failed cross-map transfer.** The
server path exposes many mutable preconditions and side effects, but it is not proof of retail client/host
atomicity, rollback, reconnect, or persistence guarantees. Those require protocol/host traces.

## Core-facing conclusion

Prebind effect identity, source/destination selector, exact misc/amplitude/speed/trigger tuple, and any
operation-specific table reference that passes a typed join. Keep caster, moved unit, destination anchor,
map/phase/site, transport/vehicle, path result, and transition authority distinct. Local motion may be
simulated only with an explicit world-query/host interface; cross-map, taxi, phase, scene, and persistence
remain host-owned. Unknown raw references should fail closed or be surfaced, never converted to current
map, zero motion, or a generic teleport.

## Reproduction notes

DuckDB grouped the complete set of movement effect kinds and payload occupancy, anti-joined operation-local
candidate tables, and reconciled all world-table counts. The durable facts and semantic build diff are in
`scripts/research/{remaining_data_audit.py,wago_research.py}` and run under `uv`.

Executable evidence was traced through Trinity `SpellEffects.cpp`, `Spell.cpp`, `Player.cpp`, movement
generators, taxi handlers, DB2 structures, and current history. SimC was searched independently; its lack
of host/world simulation is treated as scope, not negative semantic evidence.
