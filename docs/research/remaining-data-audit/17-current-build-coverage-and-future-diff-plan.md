# Current-build coverage and future diff plan

## Result

The current research snapshot is complete at the census level. All 1,090 requested Wago CSV tables
were downloaded, all 1,090 were parsed by the durable DuckDB inventory, and the corpus contains
15,829,875 rows in 747,682,965 CSV bytes. The build metadata reports no download failures. The
semantic self-diff is clean on all 34 configured surfaces: every surface has exactly zero added,
removed, or changed keys; table additions/removals and newly populated fields are empty.

This is **strongly verified** reproducibility for the current corpus. It is not proof that every
field has gameplay semantics, that the comparison engines have retail parity, or that the current
diff keys will remain valid after a schema change. Reports 01–15 establish those narrower semantic
boundaries, and report 16 preserves every material partial, rejected, and unknown inference.

## Exact provenance

| Input | Research evidence baseline |
|---|---|
| Wago retail build | `12.1.0.69497` |
| `wowlab-data` | `2ddced452a6f9076de5c86bc92f73de5b60f8556` |
| reviewed Core | `9bd3b8ed6f0f57587f443b857204318e7339b038` |
| TrinityCore | `7f3d43b7c8dd1bbb413d7acbd057e58e9d048a8f` |
| SimulationCraft | `b48def9c26d7532db2e612d97d433ec66bd8eede` |
| DuckDB | 1.5.5 |
| Python used by the durable driver | 3.12.3 |

There is one important provenance wrinkle. During final validation, sibling `../core` advanced
externally from the frozen research baseline to
`443cd0e71bcf0140b9a5507accc85661a821b1c0` at 2026-09-07 20:42:04 UTC. The reviewed exclusion
sets, and reports that consult Core, use `9bd3b8e`; `443cd0e` was not part of their semantic evidence. The
validation-time `inventory` and `facts` JSON record the later HEAD because provenance is sampled at
execution. The corpus diff itself does not read Core, so this change does not affect its clean
result. TrinityCore, SimC, and `wowlab-data` remained at the hashes above.

The build record `changes/metadata/12.1.0.69497.json` states 1,090 requested, 1,090 downloaded, zero
failed, and an empty `failures` list. The independently generated inventory also contains exactly
1,090 table records. These are separate checks of acquisition and parse coverage.

## Current population reconciliation

The following counts are the common population anchors used across reports. Reuse of a table in
multiple reports is deliberate; it does not create additional source rows.

| Report | Domain | Reconciled current populations |
|---:|---|---|
| 01 | proc and aura options | `SpellAuraOptions` 32,162 rows / 31,849 spells; 295 PPM definitions; 821 PPM modifiers |
| 02 | cooldown/category/charge | `SpellCooldowns` 36,331 / 36,259 spells; `SpellCategories` 93,201 / 93,116; 1,109 category definitions |
| 03 | power and cost | `SpellPower` 5,713 rows / 5,434 spells; 25 difficulty overlays |
| 04 | applicability | `SpellClassOptions` 26,406 rows/spells; `SpellLabel` 142,908 rows / 52,106 spells; 1,661 observed family/bit pairs |
| 05 | mechanic/dispel/immunity/school | `SpellCategories` 93,201; `SpellEffect` 629,298 / 413,805 spells; `SpellMisc` 417,571 / 410,591 |
| 06 | difficulty | 60 difficulty definitions; 1,257,384 rows across the six configured difficulty-topology tables |
| 07 | scaling | `SpellEffect` 629,298 rows; `SpellScaling` 8,707 rows/spells |
| 08 | duration/periodic/refresh | 435 duration definitions; `SpellMisc` 417,571; `SpellEffect` 629,298 |
| 09 | target/range/radius/restrictions | 629,298 effects; 48,821 target-restriction rows; 222 ranges; 375 radii; 272 cast-time definitions |
| 10 | form/equipment | 6,431 shapeshift rows; 64 form definitions; 3,758 equipment rows |
| 11 | threat/combat state | 629,298 effects; 32,162 aura options; 122,150 interrupt rows / 122,099 spells |
| 12 | item/enchant/bonus | 61,778 item effects; 60,278 item-effect edges; 5,342 enchants; 20,224 bonus entries; 199,940 item/tree edges |
| 13 | movement/world | 1,184 maps; 140,959 locations; 26,489 phases; 8,690 taxi paths; 127,438 path nodes; 24,280 transport animations |
| 14 | derived classification | 417,571 misc rows, 629,298 effect inputs, and 48,821 authored target-restriction rows |
| 15 | cross-surface model | no additional source population; composes the typed populations above |

The difficulty total is not a new table. It is the exact union count of `SpellEffect` 629,298,
`SpellMisc` 417,571, `SpellAuraOptions` 32,162, `SpellCategories` 93,201,
`SpellCooldowns` 36,331, and `SpellTargetRestrictions` 48,821. Likewise, `SpellMisc` has 6,982
nonbase rows but only 6,980 rows beyond one per spell because two spells have no base coordinate.
This reconciles the superficially different counts in report 06.

### Exact raw-identity coverage

- Aura subtypes: 366,080 effect rows use raw zero and 263,218 use a nonzero aura. The 521 current
  nonzero raws partition exactly into 151 reviewed-Core raws and 370 audited unknown raws.
- Spell attributes: all 17 words produce 5,209,395 set row/bit occurrences and 489 distinct current
  flattened raws. They partition into 120 reviewed-Core raws and 369 audited unknown raws. The
  other 55 raws in the current theoretical 0..543 domain are unset.
- Exact-effect attributes: 28 set bit identities occur in the current `EffectAttributes` column.
- Implicit targets: 147 raws, including zero, occur across the two selector columns. Six of the
  dense 0..152 domain are unused.
- Applicability: all 128 class-mask bit positions are used, but only the 1,661 observed
  `(SpellClassSet, bit)` pairs are valid namespaced identities. There are 3,465 used spell-label IDs.

### Terminal conclusion counts

Counts are reported only where the report defines mutually exclusive units. A source paragraph may
verify an operation while rejecting a broader interpretation, so mechanically counting every bold
phrase would double-count conclusions.

| Explicit unit ledger | Strongly verified | Partially characterized | Rejected interpretation | Unknown after exhaustive evidence | Total |
|---|---:|---:|---:|---:|---:|
| 370 unknown aura-subtype raws | 262 | 70 | 3 | 35 | 370 |
| 369 unknown spell-attribute raws | 150 | 101 | 0 | 118 | 369 |
| 39 current proc-event raws | 35 | 2 | 0 | 2 | 39 |
| 8 current PPM modifier types | 8 | 0 | 0 | 0 | 8 |
| 7 current `SpellCategory.Flags` bits | 4 | 2 | 0 | 1 | 7 |
| 147 populated implicit-target raws | 137 | 0 | 0 | 10 | 147 |
| 9 form/equipment terminal rows in report 10 | 6 | 2 | 0 | 1 | 9 |
| 11 current `ItemEffect.TriggerType` raws | 10 | 0 | 0 | 1 | 11 |

Report 16 uses a different, deliberately pessimistic unit: one row per material non-strong semantic
boundary, often grouping many raw identities. Its 89 rows split into 28 partial, 38 rejected, and
23 unknown conclusions. They are 56 P0, 31 P1, and 2 P2; therefore 87 are high-risk. These numbers
must not be added to the raw-identity ledger above.

| Subsystem | Partial | Rejected | Unknown | Ledger rows | P0 + P1 |
|---|---:|---:|---:|---:|---:|
| 01 proc/aura options | 2 | 2 | 3 | 7 | 7 |
| 02 cooldown/category/charge | 2 | 1 | 3 | 6 | 6 |
| 03 power/cost | 3 | 2 | 1 | 6 | 6 |
| 04 applicability | 1 | 3 | 1 | 5 | 5 |
| 05 mechanic/dispel/immunity/school | 3 | 2 | 1 | 6 | 6 |
| 06 difficulty | 0 | 3 | 1 | 4 | 3 |
| 07 scaling | 2 | 2 | 2 | 6 | 6 |
| 08 duration/periodic/refresh | 2 | 4 | 1 | 7 | 7 |
| 09 targets/ranges/restrictions | 1 | 3 | 2 | 6 | 6 |
| 10 shapeshift/equipment | 2 | 2 | 1 | 5 | 4 |
| 11 threat/combat state | 4 | 3 | 1 | 8 | 8 |
| 12 item/enchant/bonus | 2 | 3 | 2 | 7 | 7 |
| 13 movement/world | 2 | 3 | 1 | 6 | 6 |
| 14 derived classification | 1 | 3 | 2 | 6 | 6 |
| 15 cross-surface model | 1 | 2 | 1 | 4 | 4 |
| **Total** | **28** | **38** | **23** | **89** | **87** |

Strong results are intentionally absent from this second table: reports 01–15 do not define one
uniform strong-finding unit. The raw ledgers and selected typed sets above are the exact strong
counts that can be stated without inventing a denominator.

## Durable `uv` tooling contract

`scripts/research/remaining_data_audit.py` is a PEP 723 `uv` script requiring Python 3.12 or newer
and pinning `duckdb==1.5.5`. It imports `scripts/research/wago_research.py`, which provides corpus
discovery, stable CSV relations, inventory/provenance, flattened-bit queries, and semantic diffs.
Both are read-only with respect to the Wago corpus; output is written only to explicitly requested
paths.

The driver has four commands:

- `inventory` scans every `*.csv` in sorted table-name order. Its JSON contains exact provenance,
  aggregate table/row/byte counts, and one record per table with row count, byte count, literal
  header columns, and SHA-256 of the header line. Its Markdown is a short group summary.
- `facts` emits provenance, selected table populations, every table carrying `DifficultyID`, and
  configured candidate-reference joins with zero, nonzero, matched-row, and matched-value counts.
- `diff OLD NEW` compares tables and configured semantic surfaces. `--counts-only` suppresses changed
  row payloads; it does not change counts.
- `validate-reports` requires the exact reports 00..17, verifies that report 00 links every Markdown file,
  and checks that every document distinguishes verified, partial, rejected, and exhaustive-unknown
  terminal vocabulary.

The aura/attribute identity audit remains reproducible through
`scripts/research/aura_attribute_audit.py`, also a Python 3.12+/DuckDB 1.5.5 `uv` script. It parses
the reviewed Core enums, computes the two anti-join queues, builds full populations, and emits JSON
or the durable identity document.

### Deterministic JSON and Markdown

`dump_json` uses two-space indentation, sorted object keys, `default=str`, and one trailing newline.
Table manifests, added/removed table names, semantic surfaces, changed rows, and Markdown surface
rows have explicit sorted iteration or `ORDER BY`. A direct table row is normalized to its declared
key plus a JSON object containing every non-key column in CSV header order. The comparison is a full
outer join with statuses `added`, `removed`, `changed`, and `same`; changed-row JSON retains the key,
old payload, and new payload. Derived surfaces group their raw identity keys before comparison.

The JSON is authoritative. Markdown deliberately contains only old/new corpus paths, the surface
count matrix, table additions/removals, newly-populated-field count, and a reminder that row payloads
are in JSON. Determinism is for identical content at identical resolved corpus paths: `old` and
`new` are stored as absolute paths, so moving an otherwise identical corpus changes the serialized
artifact and its hash.

There are two future-facing preconditions the code does not enforce. Direct diff keys must be
non-null and unique in each corpus, and key columns must retain compatible meaning. Current
self-diff counts equal current table row counts on every direct surface, so the configured keys are
unique for this build. A future run must check nulls/duplicates before interpreting results. A
schema change can also alter the normalized JSON shape and mark every surviving row changed; the
inventory header hashes/column lists must be reviewed before row changes.

## Exact clean self-diff

The final self-diff was generated with `data/tables` as both old and new and `--counts-only`.
`/tmp/wago-self-diff-final.json` and `.md` exactly match an independent second run. Their SHA-256
digests were respectively
`477bf9a03081b21bd09c780113bf5e00a77c5634f9d6b86bb1e8c4fa75ded7af` and
`8e5444ccc21328e88a492aed0eac8dfc932a3f461e9553a94dd7ea2689913b8e`.

| Surface | Added | Removed | Changed | Same |
|---|---:|---:|---:|---:|
| aura raws | 0 | 0 | 0 | 521 |
| category definitions | 0 | 0 | 0 | 1,109 |
| class masks | 0 | 0 | 0 | 26,406 |
| cooldowns | 0 | 0 | 0 | 36,331 |
| difficulty definitions | 0 | 0 | 0 | 60 |
| difficulty topology | 0 | 0 | 0 | 1,257,384 |
| duration definitions | 0 | 0 | 0 | 435 |
| exact-effect attribute bits | 0 | 0 | 0 | 28 |
| equipment requirements | 0 | 0 | 0 | 3,758 |
| gem/enchantment edges | 0 | 0 | 0 | 2,409 |
| implicit targets | 0 | 0 | 0 | 147 |
| item bonus entries | 0 | 0 | 0 | 20,224 |
| item bonus sequence spells | 0 | 0 | 0 | 98 |
| item bonus tree edges | 0 | 0 | 0 | 199,940 |
| item bonus tree nodes | 0 | 0 | 0 | 19,095 |
| item/effect edges | 0 | 0 | 0 | 60,278 |
| item effects | 0 | 0 | 0 | 61,778 |
| item enchantments | 0 | 0 | 0 | 5,342 |
| item-set spells | 0 | 0 | 0 | 2,971 |
| labels | 0 | 0 | 0 | 142,908 |
| mechanics/categories | 0 | 0 | 0 | 93,201 |
| PPM definitions | 0 | 0 | 0 | 295 |
| PPM modifiers | 0 | 0 | 0 | 821 |
| proc tuples | 0 | 0 | 0 | 32,162 |
| radius definitions | 0 | 0 | 0 | 375 |
| referenced identities | 0 | 0 | 0 | 75,137 |
| scaling | 0 | 0 | 0 | 8,707 |
| shapeshift requirements | 0 | 0 | 0 | 6,431 |
| spell attribute bits | 0 | 0 | 0 | 489 |
| spell effects | 0 | 0 | 0 | 629,298 |
| spell misc | 0 | 0 | 0 | 417,571 |
| spell power | 0 | 0 | 0 | 5,713 |
| spell-power difficulty | 0 | 0 | 0 | 25 |
| target restrictions | 0 | 0 | 0 | 48,821 |

Added tables, removed tables, and `newly_populated_fields` are all empty. There are no unavailable
configured surfaces. This exact result also proves that signed-bit normalization is stable under a
self comparison; the dedicated unit test supplies the stronger behavioral check for signed bit 31.

## Sentinel and parsing-failure status

| Concern | Current verified result | Required interpretation |
|---|---|---|
| acquisition | 1,090 requested/downloaded, zero failed, empty failure list | complete for the recorded build |
| CSV parsing | all 1,090 tables and 15,829,875 rows scanned with `sample_size=-1` | no current parser failure; future type inference can still change |
| aura zero | 366,080 effect rows; excluded from the 521 nonzero-raw diff | explicit sentinel/no-aura population, not an unknown subtype |
| all-zero spell attributes | 27,789 rows / 27,565 spells | explicit no-set-bit population |
| signed 32-bit masks | flattened via `value & 0xffffffff`; signed bit-31 unit test passes | preserve the unsigned bit pattern, not numeric sign |
| form/class masks | current negative source words and `-1` all-bits masks expand correctly | `-1` is not a negative selector |
| duration values | Trinity treats exactly `-1` as permanent; current `-600000` is not that sentinel | never classify all negative durations as permanent |
| floating outliers | no current negative-zero scaling fields; very large/negative values remain | absence is build-specific; do not clamp or normalize on ingestion |
| missing candidate joins | present in spell, item, scene, taxi, and world references | evidence/outliers, not CSV parse failures |
| generic newly-populated test | compares string forms against only empty, `0`, and `0.0` | triage signal, not field-specific sentinel semantics |

`read_csv_auto(..., header=true, sample_size=-1)` bases inference on the full table, eliminating
sample truncation but not cross-build type drift. The inventory's literal header and header hash are
therefore part of the contract. Candidate-reference tests always join to parent `ID`; reports 03
and 10 prove that some semantic domains instead use fields such as `PowerTypeEnum` or `ClassID`.
A successful or failed generic candidate join is not proof of field type.

The newly-populated-field detector examines columns shared by both builds and treats null/empty,
`0`, and `0.0` as empty. It intentionally treats `-1`, negative values, nonzero masks, text, and
other representations as populated. It does not report an added column directly. Header changes,
added columns, quoted/special values, and field-specific sentinels must be reviewed from the two
inventories before using that signal.

## Regeneration commands

Run from the `wowlab-data` root. Temporary outputs stay outside repository architecture.

```bash
uv run scripts/research/remaining_data_audit.py inventory \
  --corpus data/tables \
  --json /tmp/wago-inventory.json \
  --markdown /tmp/wago-inventory.md

uv run scripts/research/remaining_data_audit.py facts \
  --corpus data/tables \
  --json /tmp/wago-facts.json

uv run scripts/research/remaining_data_audit.py diff \
  data/tables data/tables \
  --counts-only \
  --json /tmp/wago-self-diff.json \
  --markdown /tmp/wago-self-diff.md

uv run --with duckdb==1.5.5 python -m unittest discover \
  -s scripts/research -p 'test_wago_research.py' -v

uv run scripts/research/aura_attribute_audit.py validate
uv run scripts/research/remaining_data_audit.py validate-reports
```

The focused tests currently verify signed word-0 bit 31 flattening and a synthetic corpus change
that adds/removes aura and spell-attribute identities and adds an exact-effect attribute bit. Both
tests pass. The first attempted bare `uv run -m unittest` did not inherit the driver's inline PEP
723 dependency and failed to import DuckDB; the explicit `--with duckdb==1.5.5` form above is the
reproducible test invocation.

## Future two-build procedure

1. Keep both corpora immutable in different directories or sibling Git worktrees. Record each
   Wago build metadata file and the exact `wowlab-data`, Core, TrinityCore, and SimC commit before
   analysis. Do not overwrite the old corpus in place.
2. Run `inventory` and `facts` separately for old and new. Require requested = downloaded, zero
   failures, and manifest count = downloaded. Compare table names, column arrays, header hashes,
   row counts, and parser success before interpreting a semantic diff.
3. Preflight every configured direct key in `TABLE_SURFACES` for nulls and duplicates in both
   builds. If a key fails, classify the diff surface as structurally unresolved and revise its
   table-specific key only after establishing the new source coordinate.
4. Run the real diff without `--counts-only`, preserving deterministic JSON rows:

   ```bash
   old_tables=/absolute/path/to/old/data/tables
   new_tables=/absolute/path/to/new/data/tables
   uv run scripts/research/remaining_data_audit.py diff \
     "$old_tables" "$new_tables" \
     --json /tmp/wago-old-to-new.json \
     --markdown /tmp/wago-old-to-new.md
   sha256sum /tmp/wago-old-to-new.json /tmp/wago-old-to-new.md
   ```

5. Treat table additions/removals, unavailable surfaces, header changes, and newly populated fields
   as schema/layout work queues. Do not interpret a mass `changed` result until payload column/type
   changes have been separated from actual row-value changes.
6. Treat added/removed `aura_raws`, `spell_attribute_bits`, `effect_attribute_bits`, and
   `implicit_targets` as identity work queues. Reparse the reviewed Core enums at the chosen new
   baseline; a raw moving into Core changes the exclusion set but does not retroactively change the
   old audit.
7. Route changed direct rows by subsystem: proc/PPM to 01, cooldown/category to 02, power to 03,
   class masks/labels to 04, difficulty topology to 06, scaling/duration/radius/targeting to 07–09,
   form/equipment to 10, item graphs to 12, and movement/world reference changes to 13. Use
   `referenced_identities` only to locate candidate relationship changes.
8. For every affected conclusion, rerun its complete new population and outlier queries, then
   recheck exact executable consumers and history at the newly pinned comparison commits. A
   removed consumer can weaken a formerly strong result; a new name alone cannot strengthen one.
9. Update report 16 first for any changed P0/P1 boundary, then update the owning domain report and
   this coverage matrix. Record both build IDs, both provenance sets, output hashes, and whether a
   disposition was retained, strengthened, weakened, or newly introduced.
10. Finish with the synthetic tests, an old-vs-old and new-vs-new clean self-diff, the real
    old-vs-new diff, aura/attribute queue validation in each build worktree, and
    `validate-reports`. The two self-diffs must be clean; the cross-build diff is expected to be
    nonempty and is the research queue, not a test failure.

## Rejected guarantees and remaining limits

**Rejected — a clean self-diff proves semantic completeness.** It proves deterministic equality of
configured keys and payloads against themselves. It cannot find a missing surface, wrong semantic
key, stale comparison label, or an operation the tool never modeled.

**Rejected — a row-count change identifies its cause.** Schema changes, difficulty variants,
duplicate/changed keys, retained historical rows, and real content additions can all change counts.
The full keyed payload and table manifest must be examined.

**Partially characterized — cross-machine byte-for-byte stability.** JSON ordering is explicit and
the current repeated run is byte-identical. Absolute paths, Python/DuckDB serialization, inferred
CSV types, and a dependency/version change can alter bytes without changing semantic rows. Pin the
driver versions and paths when a hash is used as an artifact identity.

**Unknown after exhaustive available evidence — authoritative future schema and semantic changes.**
No current repository can predict which Wago tables, keys, sentinels, or host-owned operations a
future build will change. The safe contract is detection, opaque preservation, explicit
reclassification, and evidence-versioned reports—not automatic promotion of a new raw or field.
