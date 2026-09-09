# Remaining Wago data semantic audit

## Audit status

This ordered corpus completes the defined remaining-data audit for Wago retail build
`12.1.0.69497`. It covers all 15 requested semantic domains, a cross-surface model, a centralized
pessimistic ledger, and a reproducible current/future-build coverage plan. Population statements come from
complete DuckDB queries; identities and joins were not promoted to behavior without executable or otherwise
independent evidence.

The corpus contains 1,090/1,090 successfully downloaded CSV tables, 15,829,875 rows, and 747,682,965 bytes.
The main spell population is 413,971 `Spell` rows, 629,298 `SpellEffect` rows for 413,805 spells, and
417,571 `SpellMisc` rows for 410,591 spells. Zero/sentinel populations, missing current joins, disabled
effects, difficulty variants, and contradictory outliers are reported rather than filtered away.

The terminal vocabulary is consistent throughout:

- **Strongly verified / verified semantic** means an operation, predicate, or narrow identity survived
  complete-population falsification and has strong executable or independent evidence.
- **Partially characterized** means a useful domain or branch is proved but the generic contract is
  incomplete.
- **Rejected inference** means a tempting, historical, adjacent, or comparison-engine interpretation is
  contradicted or too strong.
- **Unknown after exhaustive available evidence** means current Wago, Core, TrinityCore, SimC, and relevant
  local history do not establish a safe generic semantic; the reports name the smallest missing evidence.

## Ordered reports

1. [Proc and aura options](01-proc-and-aura-options.md) — 32,162 aura-option rows; all 39 current flattened
   proc-event raws; chance versus RPPM, typed modifiers, internal cooldown, stacks/charges, mutation order,
   and unknown current raws 35/37.
2. [Cooldown, category, and charge topology](02-cooldown-category-and-charge-topology.md) — 36,331 cooldown
   rows, 93,201 category-link rows, 1,109 definitions; proves independent individual/shared/start-recovery/
   charge/aura-lock ownership.
3. [Spell power and cost semantics](03-spell-power-and-cost-semantics.md) — all 5,713 conditional ordered
   power rows, difficulty slots, signed grants, optional spend, periodic drain, resource/display identities,
   and unproved legacy/max-percent fields.
4. [Applicability selectors](04-applicability-selectors.md) — all 26,406 class-option rows and 142,908
   label edges; family-namespaced 128-bit intersection, spellmod popcount multiplicity, exact label
   membership, and valid empty selector results.
5. [Mechanic, dispel, immunity, and school semantics](05-mechanic-dispel-immunity-and-school-semantics.md)
   — separate spell/effect mechanic ownership, typed dispel/immunity channels, seven-bit school masks,
   prevention/diminish boundaries, and stale names/high payloads.
6. [Difficulty resolution](06-difficulty-resolution.md) — the complete 60-node/22-edge acyclic fallback
   graph and every difficulty-bearing spell table; proves per-component/per-index selection and rejects
   union/base-only/whole-spell replacement.
7. [Scaling and coefficient provenance](07-scaling-and-coefficient-provenance.md) — every amount,
   coefficient, variance, scaling-class, amplitude, and `SpellScaling` row; branch order, RNG, rounding, and
   unconsumed PvP/group-size channels.
8. [Duration, periodic, and refresh semantics](08-duration-periodic-and-refresh-semantics.md) — all 435
   duration definitions, 47,952 nonzero period rows, sentinel/interpolation behavior, tick scheduling,
   refresh policies, and unresolved PvP duration/raw-489 lifecycle.
9. [Target, range, radius, and cast restrictions](09-target-range-radius-and-cast-restrictions.md) — every
   selector occurrence, 48,821 target restrictions, all referenced range/radius/cast-time definitions,
   geometry/caps, owner/phase admission, and ten populated reserved selectors.
10. [Shapeshift, form, and equipment requirements](10-shapeshift-form-and-equipment-requirements.md) — all
    6,431 two-word form masks and 3,758 equipment rows; exclusion/include precedence, attribute bypasses,
    class-relative masks, target-item versus equipped/proc checks.
11. [Threat, aggro, and combat-state metadata](11-threat-aggro-and-combat-state-metadata.md) — flat/scaled/
    redirected threat, taunt priority, no-threat variants, threat-list targeting, combat transitions, and
    historical names rejected by current execution.
12. [Item, spell, enchant, and bonus relationships](12-item-spell-enchant-and-bonus-relationships.md) —
    item-effect, gem/enchant, ordered bonus-list/tree, and set-threshold graphs; source/target/owner and
    host-persistence boundaries.
13. [Movement, teleport, world, and host-transition data](13-movement-teleport-world-and-host-transition-data.md)
    — complete movement-effect populations and operation-local joins; trajectory versus teleport/taxi/
    scene/phase/transport, with map transfer explicitly host-owned.
14. [Derived static spell classification](14-derived-static-spell-classification.md) — direct attribute
    aliases versus recursive positivity, target-mask derivation, overlapping structural cohorts, trigger
    cycles, and limits of a Wago-only taxonomy.
15. [Cross-surface semantic model](15-cross-surface-semantic-model.md) — second-order ownership,
    predicate, difficulty, runtime, host, provenance, and RNG/order boundaries across every audited surface.
16. [Unresolved and rejected inferences](16-unresolved-and-rejected-inferences.md) — numbered centralized
    ledger with conflicting evidence, smallest resolving evidence, risk, and priority.
17. [Current-build coverage and future diff plan](17-current-build-coverage-and-future-diff-plan.md) —
    reconciled counts, parsing/sentinel coverage, deterministic JSON/Markdown two-build diff, tests, and
    regeneration procedure.

## Current-build census anchors

| Surface | Current population |
|---|---:|
| CSV tables / total rows | 1,090 / 15,829,875 |
| spell effects / owning spells / difficulties | 629,298 / 413,805 / 36 |
| nonzero aura subtype identities | 521 |
| set flattened spell-attribute identities | 489 |
| set exact-effect attribute identities | 28 |
| populated implicit-target identities | 147 |
| aura options / distinct owning spells | 32,162 / 31,849 |
| cooldown rows / category rows / category definitions | 36,331 / 93,201 / 1,109 |
| power rows / owning spells / difficulty overlays | 5,713 / 5,434 / 25 |
| class options / label edges / label identities | 26,406 / 142,908 / 3,465 |
| duration definitions / nonzero-period effect rows | 435 / 47,952 |
| target restrictions / radius definitions / range definitions | 48,821 / 375 / 222 |
| item rows / item effects / item-effect edges | 213,349 / 61,778 / 60,278 |
| maps / phases / taxi paths / transport animations | 1,184 / 26,489 / 8,690 / 24,280 |

The self-diff independently balances these semantic surfaces: current versus current reports zero added,
removed, or changed records and zero newly populated fields. Report 17 includes the full per-surface
same-count table and commands.

## Highest-value Core-relevant conclusions

- Difficulty is an acyclic graph applied independently per component and per indexed child. Every derived
  fact must retain requested and selected source difficulty; unioning variants is unsound.
- Applicability requires typed predicates. Family/mask matching is namespaced, spellmods can apply once per
  intersecting bit, labels are exact one-count membership, and school/mechanic/category/target/form/
  equipment selectors remain orthogonal.
- Cooldown state needs independent spell deadline, shared category deadline, start-recovery key, serial
  charge queue, and aura lock. Equal numeric category IDs do not merge those roles.
- Proc source metadata, mutable aura stacks/charges/cooldown, triggering event provenance, and RNG are
  separate. PPM replaces rather than composes with chance in the traced generic path.
- Cost and amount computation are ordered contextual programs, not products of similarly named columns.
  Signed costs can grant resources; optional and periodic costs have different phases; variance consumes
  RNG before later amount modifiers.
- Duration, periodic timer, current/max aura duration, stack/charge state, and refresh policy are distinct.
  Range and radius likewise govern different stages of targeting.
- Item and movement data cross host boundaries. Source item, item instance, caster, target item, owner,
  destination, moved unit, map/phase/transport, and mutation authority must remain separate provenance.
- Positivity and explicit/required target masks are versioned executable derivations. Wago supplies inputs,
  not authoritative output booleans.

## Unresolved high-risk semantics

The centralized ledger contains 89 terminal rows: 56 P0, 31 P1, and two P2. Under its explicit
high-risk definition (`P0 + P1`), **87 unresolved or rejected semantics are high risk**. The authoritative
numbering and priority rubric are in [report 16](16-unresolved-and-rejected-inferences.md). The most consequential families are: unknown current
proc events 35/37; extreme proc-stack/charge encodings; unproved cooldown category expiry and weekly/
flag behavior; contradictory or unconsumed cost/scaling fields; recursive positivity parity and server
correction dependence; populated reserved target selectors; exceptional immunity payloads; duration/PvP/
refresh lifecycle; threat redirect/ignore-combat lifecycle; unknown item trigger/enchant/bonus types; and
host atomicity for inventory and world transitions. These are terminal results, not queued guesses.

## Exact provenance

| Source/tool | Frozen audit baseline |
|---|---|
| Wago retail build | `12.1.0.69497` (1,090 requested, 1,090 downloaded, 0 failed) |
| wowlab-data | `2ddced452a6f9076de5c86bc92f73de5b60f8556` |
| Core comparison baseline | `9bd3b8ed6f0f57587f443b857204318e7339b038` |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` |
| SimulationCraft | `b48def9c26d7532db2e612d97d433ec66bd8eede` |
| DuckDB | `v1.5.5` (`d8cdaa33fd`) |
| Python / uv | `3.12.3` / `0.12.4` |

During final validation, the clean sibling `../core` worktree had externally advanced to
`443cd0e71bcf0140b9a5507accc85661a821b1c0`. The audit did not modify it, and that later commit is not
silently substituted for the frozen comparison baseline used by the reports.

## Reproduction

From the repository root:

```bash
uv run scripts/research/remaining_data_audit.py inventory \
  --json /tmp/wago-inventory.json --markdown /tmp/wago-inventory.md
uv run scripts/research/remaining_data_audit.py facts --json /tmp/wago-facts.json
uv run scripts/research/remaining_data_audit.py diff OLD/data/tables NEW/data/tables \
  --json /tmp/wago-diff.json --markdown /tmp/wago-diff.md
uv run scripts/research/remaining_data_audit.py validate-reports
uv run --with duckdb==1.5.5 python -m unittest discover \
  -s scripts/research -p 'test_*.py' -v
```

The tools write only requested analysis output. They do not modify the Wago CSV corpus, Core, TrinityCore,
SimC, runtime code, schemas, or catalogs.
