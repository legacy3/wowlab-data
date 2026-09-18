"""Core / research boundary map for the aura-lifecycle pass (track K).

**Navigation only.**  Every record says where a lifecycle concept lives in Core
at the pinned commit, which research question (tracks A–I) it touches, and
whether Core *represents* it, *refuses* it (compile-time fail-closed), silently
*assumes* it (host-authored or single-mode, no source check), or has it
*absent*.  Records are reopen opportunities, never prescriptions and never new
findings: the Core semantic boundary audit owns findings, and each record cites
its ids (``CSA-*``, ``UNK-*``, ``LC-*``, ``NUM-*``, ``FF-*``).

Three mechanical layers back the curated registry:

* :func:`verify_core_coords` -- Core must be at the pin with a clean tree
  (``core_audit.pins.verify_core``) and every cited ``path:first-last`` range
  must contain its anchor text.  A Core change therefore shows up as a failed
  anchor, not as silently stale navigation.
* :func:`verify_trinity_coords` -- the few Trinity lines K quotes as the other
  side of a boundary are anchor-checked the same way.
* :func:`attribute_table` -- lifecycle-bearing spell attributes: Core's own
  catalog support class (parsed from ``crates/dbc/src/spell_attribute.rs`` via
  :mod:`selected_package.coreref`), the Trinity ``SharedDefines.h`` name for
  the same flattened bit (``raw = word*32 + bit``), and provider-carrier counts
  per named population.

The attribute *selection* is a navigation list (attributes whose Trinity or Core
consumer touches aura lifetime, stacking, periodic cadence, removal or death),
not a rule; the selection criterion is stated per row in ``why``.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from . import CORE_ROOT, TC_ROOT, FailClosed

STATUSES = ("represented", "refused", "assumed", "absent")

QUESTIONS = {
    "A": "identity / application: which mutable aura/application object exists and what it is keyed by",
    "B": "duration / refresh / pandemic: how the lifetime initialises and what reapplication does to it",
    "C": "stacks / charges / proc state: initial stacks, capacity, stack and charge mutation",
    "D": "periodic / snapshotting: cadence, initial and terminal ticks, what is captured vs read live",
    "E": "removal / dispel / death: removal paths, callbacks, remove reasons, death",
    "F": "area aura recipients: recipient-map changes during an aura's lifetime",
    "G": "passive / active / acquisition: passives as auras, mutability of passives",
    "H": "scripts / world overlays / external policy",
    "I": "numeric boundaries / same-timestamp ordering / generation safety",
}

C = "crates/combat/src/"
D = "crates/data/src/"
B = "crates/dbc/src/"

# ---------------------------------------------------------------------------
# Curated navigation registry.  ``anchors`` maps each Core coord to a substring
# that must occur inside that line range at the pin.  ``tc_anchors`` does the
# same for Trinity coords (relative to TrinityCore/src/server/game/).
# ---------------------------------------------------------------------------

REGISTRY: list[dict] = [
    {
        "id": "AL-K-K-01", "topic": "identity-key", "questions": ["A"], "status": "assumed",
        "anchors": {
            "crates/model/src/aura.rs:17-21": "affected: ActorId",
            C + "aura_state.rs:1225-1231": "left.affected()",
            C + "program/aura_lifetime.rs:16": "SpellId::new(aura.id().get())",
        },
        "observation": "Core has one mutable cell per exact AuraKey (aura = spell id, source actor, affected actor). "
                       "There is no effect-mask, cast-item or per-cast component, and there is no shared caster-ANY slot: "
                       "two casters applying the same aura to one recipient always own two distinct cells.",
        "research_ref": ["A: Trinity one-slot lookup Unit::_TryStackingOrRefreshingExistingAura (caster-ANY when "
                         "IsStackableOnOneSlotWithDifferentCasters); effect-mask mismatch recreates the aura"],
        "tc_anchors": {"Spells/SpellInfo.cpp:1808-1811": "SPELL_ATTR3_DOT_STACKING_RULE"},
        "audit_refs": ["UNK-K-001"],
        "reopen_condition": "Track A's identity corpus names a stacking class whose Trinity key is not (spell, caster, "
                            "recipient) -- StackAmount>1 without ATTR3_DOT_STACKING_RULE (shared slot) or effect-mask "
                            "recreation -- on a family Core admits; then UNK-K-001's two-caster probe becomes decisive.",
    },
    {
        "id": "AL-K-K-02", "topic": "recipient-topology", "questions": ["A", "F"], "status": "absent",
        "anchors": {
            C + "state.rs:1593-1706": "fn compile_aura_plan(",
            C + "state/topology.rs:8-30": "pub(super) fn mutation_reaches_key(",
            C + "aura_state.rs:116-132": "AuraPlanBuildError::DuplicateKey",
        },
        "observation": "Every reachable aura cell is enumerated once at CombatState construction from the program topology x "
                       "actors x host-declared target relations. There is no runtime recipient discovery, no area-aura "
                       "owner/application split and no periodic target-map update; the recipient set of an aura is fixed "
                       "for the whole state lifetime.",
        "research_ref": ["F: Aura::UpdateTargetMap every 500 ms adds/removes applications during the lifetime",
                         "targeting-recipient-policy-archaeology.md (aura target maps)"],
        "tc_anchors": {},
        "audit_refs": ["UNK-X-001"],
        "reopen_condition": "Track F shows an admitted-shape (self or single explicit target) aura whose Trinity recipient "
                            "set changes mid-lifetime, or Core admits any area/persistent-area aura effect.",
    },
    {
        "id": "AL-K-K-03", "topic": "application-identity-binding", "questions": ["A"], "status": "assumed",
        "anchors": {
            C + "program.rs:1595-1618": "fn validate_aura_mutation_source(",
            D + "action/catalog/selected_owner.rs:38": "a program may apply an aura owned by another spell",
        },
        "observation": "An aura mutation step is checked only for its effect-kind family; the aura it applies/removes and its "
                       "count are host-resolved. A bare ApplyAura row of spell A may be declared as applying aura B (probe "
                       "k_aura_mutation_source::accepted_apply_aura_row_of_a_applies_foreign_aura_b).",
        "research_ref": ["A: application created by EffectApplyAura for the hit spell's own aura"],
        "tc_anchors": {},
        "audit_refs": ["CSA-K-01", "MUT-K-012"],
        "reopen_condition": "Track A/C find a real player spell whose ApplyAura effect legitimately creates an aura of a "
                            "different spell id (Trinity would then be the arbiter of the binding Core leaves to the host).",
    },
    {
        "id": "AL-K-K-04", "topic": "initial-stacks", "questions": ["C"], "status": "assumed",
        "anchors": {
            C + "program.rs:1650-1655": "NonZeroU16::new(input.count)",
            C + "aura_state.rs:729-745": "StackCountExceedsMaximum",
        },
        "observation": "Initial stacks of an application are the host-authored step count (nonzero, <= max stacks). Core never "
                       "derives them from source rows; a fresh application starts at exactly that count.",
        "research_ref": ["C: Trinity initial stacks = AuraCreateInfo::StackAmount (default 1, SpellAuras.cpp:481; spell "
                         "path SpellValue AuraStackAmount=1); CumulativeAura never read at construction"],
        "tc_anchors": {"Spells/Auras/SpellAuras.cpp:481": "m_stackAmount(createInfo.StackAmount)"},
        "audit_refs": ["CSA-K-01"],
        "reopen_condition": "Track C's stacks corpus finds a real player application whose Trinity initial stack count is not 1 "
                            "(SPELLVALUE_AURA_STACK, script) -- then the host's count is carrying source semantics Core does "
                            "not check.",
    },
    {
        "id": "AL-K-K-05", "topic": "stack-capacity", "questions": ["C"], "status": "assumed",
        "anchors": {
            D + "action/catalog/aura.rs:38-42": "max_stacks: u16",
            D + "action/catalog/aura.rs:183-185": "invalid_aura_max_stacks",
            C + "program/aura_reapplication/aura_unique.rs:57-67": "aura_unique_stack_limit_mismatch",
        },
        "observation": "Maximum stacks are host-resolved per aura; the only generic check is nonzero. The value is cross-checked "
                       "against SpellAuraOptions.CumulativeAura only for Aura Unique owners (max(cumulative,1)); family "
                       "compilers add their own single-stack gates.",
        "research_ref": ["C: Trinity capacity = SpellInfo::StackAmount (CumulativeAura); ModStackAmount treats 0 as 1"],
        "tc_anchors": {},
        "audit_refs": [],
        "reopen_condition": "Track C's capacity rule (CumulativeAura, 0->1, spellmods on StackAmount, script overrides) differs "
                            "from a host-resolved max_stacks on any admitted family.",
    },
    {
        "id": "AL-K-K-06", "topic": "stack-reapplication", "questions": ["C", "B"], "status": "represented",
        "anchors": {
            C + "aura_state.rs:737-745": ".min(definition.max_stacks.get())",
            C + "aura_state.rs:810-814": "AuraTransition::Reapplied",
        },
        "observation": "Active reapplication adds the application's stacks, saturating at max stacks, under every lifetime "
                       "policy; at the cap the lifetime policy still runs (RestartLifetime restarts at cap).",
        "research_ref": ["C: Trinity ModStackAmount(createInfo.StackAmount): refresh iff new>=old && (StackAmount || "
                         "!AURA_UNIQUE); cap only on increase (SpellAuras.cpp:1093-1127)"],
        "tc_anchors": {"Spells/Auras/SpellAuras.cpp:1114": "bool refresh = stackAmount >= GetStackAmount()"},
        "audit_refs": [],
        "reopen_condition": "Track C shows a reapplication that does not add the incoming count (e.g. mask-mismatch recreate, "
                            "AuraPointsStack base-amount accumulation) on a family Core admits.",
    },
    {
        "id": "AL-K-K-07", "topic": "stack-mutation-ops", "questions": ["C"], "status": "represented",
        "anchors": {
            C + "program/definition.rs:800-808": "RemoveStacks(NonZeroU16)",
            C + "aura_state.rs:838-874": "pub(crate) fn quote_add_stacks_from(",
            C + "aura_state.rs:937-966": "count.get() >= before.cell.stacks",
        },
        "observation": "Explicit stack operations are Add(n) (inactive -> unchanged, capped), RemoveStacks(n) (>= current -> "
                       "whole-aura removal) and Remove. There is no Set-stacks operation; source direction/mode/identity "
                       "of raw 164/203/289 rows is not bound to the host step.",
        "research_ref": ["C: EffectModifyAuraStacks MiscValue 0 Mod / 1 Set (SpellEffects.cpp:6127)"],
        "tc_anchors": {},
        "audit_refs": ["CSA-K-01", "MUT-K-009", "MUT-K-010"],
        "reopen_condition": "Track C's stacks corpus shows a player-reachable raw-289 Set-mode or negative-count row on an aura "
                            "Core admits (the Set-mode clause was rejected on real data by R1).",
    },
    {
        "id": "AL-K-K-08", "topic": "aura-proc-charges", "questions": ["C", "H"], "status": "absent",
        "anchors": {
            C + "program/aura_reapplication/aura_unique.rs:296-302": "options.proc_charges() == 0",
            C + "program/dispel_duration_modifier.rs:544": "options.proc_charges() == 0",
            C + "program/target_aura_state_modifier.rs:573": "options.proc_charges() == 0",
            C + "program/selected_trait_package.rs:466": "options.proc_charges() == 0",
            C + "program/damage_modifier/target_aura_mechanic.rs:258": "row.proc_charges() == 0",
        },
        "observation": "Aura proc charges have no runtime representation. Five admission sites require ProcCharges == 0; other "
                       "families do not read SpellAuraOptions charges at all. (Spell cooldown charges -- CSA-R3-02 -- are a "
                       "different mechanism.)",
        "research_ref": ["C: CalcMaxCharges (spell_proc Charges overrides DB2), ModCharges, ConsumeProcCharges / "
                         "USE_STACKS_FOR_CHARGES", "H: spell_proc.Charges override"],
        "tc_anchors": {},
        "audit_refs": ["CSA-K-05", "UNK-K-002"],
        "reopen_condition": "Any admitted Core family is found (track C/H census) with nonzero ProcCharges or a spell_proc "
                            "Charges override in the player population.",
    },
    {
        "id": "AL-K-K-09", "topic": "reapplication-policy-compiler", "questions": ["B"], "status": "represented",
        "anchors": {
            C + "aura_state.rs:685-690": "PreserveExistingLifetime,",
            D + "action/catalog/aura.rs:60-63": "CappedCarryover,",
            C + "program/aura_reapplication.rs:441-496": "(ResolvedAuraReapplication::CappedCarryover, true, false, None)",
        },
        "observation": "Four lifetime policies: RestartLifetime, CappedCarryover, IndependentStackDeadlines, "
                       "PreserveExistingLifetime. The host supplies only RestartLifetime or CappedCarryover; the compiler "
                       "combines it with raw 43/436/489/490 and fails closed on any other combination.",
        "research_ref": ["B: refresh.json (Trinity RefreshDuration / RefreshTimers / pandemic branch)"],
        "tc_anchors": {},
        "audit_refs": ["PL-E-001"],
        "reopen_condition": "Track B identifies a Trinity or simc refresh behaviour on an admitted-shape aura that none of the "
                            "four policies expresses (e.g. duration reset only when the new max differs, Spell.cpp:3294-3298).",
    },
    {
        "id": "AL-K-K-10", "topic": "restart-lifetime", "questions": ["B", "I"], "status": "represented",
        "anchors": {
            C + "aura_state.rs:1174-1200": "projected.max(before.expires_at.value()",
            C + "aura_state.rs:775-787": "GenerationExhausted",
            C + "aura_state.rs:798-809": "applied_at: if before.cell.is_active()",
        },
        "observation": "RestartLifetime sets expiry = now + projected application duration, which may shorten a later "
                       "existing deadline (only CappedCarryover / IndependentStackDeadlines take the max with the old "
                       "deadline). applied_at becomes now and the generation increments.",
        "research_ref": ["B: Trinity RefreshDuration -> SetDuration(GetMaxDuration()) also replaces the remaining time"],
        "tc_anchors": {},
        "audit_refs": [],
        "reopen_condition": "Track B shows a Trinity/simc refresh that never shortens (max with remaining) for non-pandemic "
                            "auras, or a duration modifier that makes the reapplied max shorter than the remaining time.",
    },
    {
        "id": "AL-K-K-11", "topic": "capped-carryover", "questions": ["B", "I"], "status": "represented",
        "anchors": {
            C + "aura_state.rs:18-19": "CARRY_DENOMINATOR: u64 = 10",
            C + "aura_state.rs:1153-1173": "remaining.as_millis().min(carry_limit)",
            C + "program/aura_reapplication.rs:455-457": "Ok(AuraReapplicationPolicy::CappedCarryover)",
        },
        "observation": "CappedCarryover (host CappedCarryover AND raw 436) computes expiry = max(now + D + min(remaining, "
                       "floor(3D/10)), old expiry) with D the projected application duration, integer ms. It preserves "
                       "periodic cadence. Arithmetic mirrored by core_projected_expiry().",
        "research_ref": ["B: pinned Trinity Spell.cpp:3284-3288 reads HitAura->GetDuration() after the refresh already set "
                         "it to max, so newDuration = min(D + D, trunc(D*130/100)) -- carry is ~always the full 30% "
                         "(B: likely Trinity defect from 92773e207c); simc action.cpp:4603 max(r, min(0.3D, r) + D)"],
        "tc_anchors": {"Spells/Spell.cpp:3284-3288": "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION"},
        "audit_refs": ["NUM-E-002", "FF-E-003", "REJ-E-005"],
        "reopen_condition": "Track B's probe confirms the pinned-Trinity carry model; Core then matches simc / pre-92773e207c "
                            "Trinity rather than the pinned consumer, and the timeline refresh-at-225-of-250 separates them "
                            "(Core 500 vs pinned Trinity 550).",
    },
    {
        "id": "AL-K-K-12", "topic": "preserve-existing-lifetime", "questions": ["B", "C"], "status": "represented",
        "anchors": {
            C + "program/aura_reapplication/aura_unique.rs:45-69": "Ok(cumulative_aura == 0)",
            C + "program/aura_reapplication.rs:427-453": "SpellAttributeKind::AuraDoesNotRefresh",
            C + "aura_state.rs:747-766": "AuraTransition::ReappliedPreservingDeadline",
        },
        "observation": "PreserveExistingLifetime keeps application time, deadline, generation and periodic cadence and only "
                       "adds capped stacks. It is compiled from raw 43 (Aura Unique) with authored CumulativeAura 0 on an "
                       "exact hostile single-effect periodic-damage owner, or from raw 489 on the application spell. Raw 189 "
                       "(ATTR5_AURA_UNIQUE_PER_CASTER), Trinity's second refresh-suppression bit, is uncatalogued in Core, "
                       "so a selected owner carrying it is refused.",
        "research_ref": ["B: Trinity ATTR1_AURA_UNIQUE / ATTR5_AURA_UNIQUE_PER_CASTER with StackAmount 0 -> ModStackAmount "
                         "refresh=false (SpellAuras.cpp:1114); Spell.cpp:3294-3298 still resets the duration when the hit "
                         "duration differs from the current max", "B/J: raw 489 = Trinity SPELL_ATTR15_UNK9, no Trinity "
                         "consumer; simc SX_AURA_DOES_NOT_REFRESH buff.cpp:739-744"],
        "tc_anchors": {"Spells/Spell.cpp:3294-3298": "hitInfo.AuraDuration != hitInfo.HitAura->GetMaxDuration()",
                       "Spells/Auras/SpellAuras.cpp:1114": "SPELL_ATTR5_AURA_UNIQUE_PER_CASTER"},
        "audit_refs": [],
        "reopen_condition": "Track B shows an Aura Unique reapplication whose hit duration differs from the current max "
                            "(duration modifiers, haste) -- Trinity then rewrites the deadline where Core preserves it; or a "
                            "raw-489 carrier's retail refresh behaviour (track L) contradicts simc.",
    },
    {
        "id": "AL-K-K-13", "topic": "independent-stack-deadlines", "questions": ["B", "C"], "status": "represented",
        "anchors": {
            C + "program/aura_lifetime.rs:11-35": "SpellAttributeKind::AsynchronousStackingBuff",
            C + "independent_stack.rs:190-233": "self.displace_earliest(&mut pool, displaced)",
            C + "state/independent_stack_lifetime.rs:114-158": "AuraStacksExpired",
        },
        "observation": "Raw 490 with max stacks > 1 gives each application its own deadline cohort; overflow and explicit "
                       "removal consume earliest cohorts; natural expiry removes only due stacks and exposes the latest "
                       "retained deadline. Independent expiry emits AuraStacksExpired and never runs expiry drivers. A "
                       "missing spell during lifetime compilation falls back to SingleDeadline under a debug_assert only.",
        "research_ref": ["B/J: raw 490 = Trinity SPELL_ATTR15_UNK10, no Trinity consumer (Trinity has one duration per "
                         "Aura); simc asynchronous buff stacks"],
        "tc_anchors": {},
        "audit_refs": ["LC-E-003", "LC-R3-002"],
        "reopen_condition": "Track B/C find a Trinity or script consumer of ATTR15 bit 10, or track L designs a retail "
                            "per-stack expiry experiment for a raw-490 carrier.",
    },
    {
        "id": "AL-K-K-14", "topic": "duration-source", "questions": ["B"], "status": "assumed",
        "anchors": {
            D + "action/input.rs:1120-1123": "Finite { duration_ms: NonZeroU32 }",
            D + "game_data.rs:363-385": "pub fn spell_has_duration_entry(",
            C + "aura_state.rs:55-61": "AuraPlanBuildError::InvalidDuration",
        },
        "observation": "The base lifetime is host-resolved (Finite{duration_ms} or Indefinite). GameData retains only whether a "
                       "spell names a nonzero SpellDuration entry, never Duration/MaxDuration/DurationPerResource, so Core "
                       "cannot cross-check the authored duration; zero duration is refused.",
        "research_ref": ["B: duration.json (SpellDuration topology, MaxDuration/combo points, MinDuration missile floor "
                         "Spell.cpp:888-896, PvPDurationIndex has no Trinity consumer)"],
        "tc_anchors": {},
        "audit_refs": [],
        "reopen_condition": "Track B's duration rules name a source-derived initial duration (DurationPerResource, MinDuration, "
                            "negative/indefinite encodings) for an aura shape Core admits.",
    },
    {
        "id": "AL-K-K-15", "topic": "duration-modifiers-at-application", "questions": ["B", "D"], "status": "represented",
        "anchors": {
            C + "execution/planning/aura_transition.rs:56-86": "projected_application_duration(",
            C + "state/modifiers.rs:72-108": "crate::dispel_duration::duration(",
            C + "periodic.rs:564-582": "let whole_periods = (duration_millis / period_millis).max(1);",
        },
        "observation": "The application duration is captured once per (re)application: authored -> mechanic-duration -> "
                       "dispel-duration modifiers of the recipient, then (only for the Venom 391428 raw-273 slice) aligned to "
                       "whole hasted periods. It is never rebased mid-lifetime. Preserve reapplication does not recompute it.",
        "research_ref": ["B: Spell.cpp ModSpellDuration 3262, haste/ATTR8 3270 (per-hit recomputation)",
                         "D: snapshot-matrix (duration inputs)"],
        "tc_anchors": {},
        "audit_refs": ["CSA-J-04"],
        "reopen_condition": "Track B/D show a lifetime input read live after application (e.g. a duration mod applied to an "
                            "active aura) on an admitted family.",
    },
    {
        "id": "AL-K-K-16", "topic": "indefinite-and-zero-lifetimes", "questions": ["B", "G"], "status": "refused",
        "anchors": {
            C + "aura_state.rs:1138-1150": "AuraQuoteError::IndefiniteDeadlinePolicy",
            C + "program/aura_reapplication.rs:370-380": "unsupported_aura_lifetime_error",
            C + "periodic.rs:758-767": "PeriodicQuoteError::MissingAuraDeadline",
        },
        "observation": "Indefinite auras exist, but CappedCarryover / IndependentStackDeadlines on them are refused at compile "
                       "time and periodic schedules require a finite aura deadline. A zero authored duration is a plan build "
                       "error rather than an instantaneous or indefinite aura.",
        "research_ref": ["B: Trinity duration 0 / -1 encodings", "G: passive/indefinite auras"],
        "tc_anchors": {},
        "audit_refs": [],
        "reopen_condition": "Track B/G find player periodic auras with indefinite lifetime (e.g. passive periodic triggers) "
                            "that a Core family would need.",
    },
    {
        "id": "AL-K-K-17", "topic": "periodic-cadence-on-refresh", "questions": ["D", "B"], "status": "assumed",
        "anchors": {
            D + "action/input.rs:1112-1115": "RestartCadence,",
            C + "periodic.rs:729-741": "ResolvedPeriodicCadenceReapplication::PreserveCadence",
        },
        "observation": "Whether an active reapplication preserves or restarts the tick grid is a host-authored per-schedule "
                       "choice (Preserve keeps the anchor and moves only the expiry; Restart re-anchors at now and recaptures "
                       "the period). No source field is consulted.",
        "research_ref": ["D/C: Trinity resetPeriodicTimer = StackAmount < 2 && !TRIGGERED_DONT_RESET_PERIODIC_TIMER "
                         "(Spell.cpp:3240); pandemic path overrides"],
        "tc_anchors": {"Spells/Spell.cpp:3240": "TRIGGERED_DONT_RESET_PERIODIC_TIMER"},
        "audit_refs": ["UNK-E-001", "PL-E-001"],
        "reopen_condition": "UNK-E-001's own reopen: once a host resolver/generator exists, check that it emits "
                            "PreserveCadence exactly for StackAmount >= 2 or triggered-dont-reset applications (track D rule).",
    },
    {
        "id": "AL-K-K-18", "topic": "tick-on-application", "questions": ["D"], "status": "represented",
        "anchors": {
            C + "periodic.rs:743-745": "PeriodicInitialPolicy::OnFreshApplication",
            B + "spell_attribute.rs:117": "TickOnApplication = 169,",
        },
        "observation": "Raw 169 (Trinity SPELL_ATTR5_EXTRA_INITIAL_PERIOD) yields one full initial occurrence only on a fresh "
                       "application; an active restart-cadence refresh does not re-tick (simc tick_on_application semantics).",
        "research_ref": ["D: Trinity ResetPeriodic(true) sets _periodicTimer so a refresh ticks immediately "
                         "(SpellAuraEffects.cpp:949-958); simc dot.cpp:972-975 ticks only on start"],
        "tc_anchors": {"Spells/Auras/SpellAuraEffects.cpp:949-952": "void AuraEffect::ResetPeriodic("},
        "audit_refs": ["UNK-E-002"],
        "reopen_condition": "Track D's periodic timeline for a raw-169 refresh confirms Trinity re-ticks on refresh, and track L "
                            "designs the retail discriminator.",
    },
    {
        "id": "AL-K-K-19", "topic": "tick-grid-and-terminal-partial", "questions": ["D", "I"], "status": "represented",
        "anchors": {
            C + "periodic.rs:523-548": "PeriodicTerminalPolicy::ProportionalNaturalExpiryPartial",
            D + "action/periodic_health.rs:43-46": "ProportionalNaturalExpiryPartial,",
        },
        "observation": "Regular ticks fall at anchor + k*period while <= the aura deadline (a tick exactly at expiry runs). A "
                       "proportional terminal partial occurrence at natural expiry exists only when the host selects "
                       "ProportionalNaturalExpiryPartial; explicit removal never produces one.",
        "research_ref": ["D: Trinity tick count / GetTotalTicks and whether any partial final tick exists"],
        "tc_anchors": {},
        "audit_refs": ["FF-E-003"],
        "reopen_condition": "Track D shows Trinity (or simc for the same shape) never emits a partial final tick, or emits it "
                            "on removal paths too.",
    },
    {
        "id": "AL-K-K-20", "topic": "period-haste-capture", "questions": ["D", "I"], "status": "represented",
        "anchors": {
            C + "periodic.rs:551-562": "PeriodicCadenceRate::SpellCastSpeed",
            B + "spell_attribute.rs:118": "SpellHasteAffectsPeriodic = 173,",
        },
        "observation": "With raw 173 the period is trunc(authored / spell-cast-speed) (f64), captured when an interval is "
                       "installed (fresh application / restart / after each occurrence); a pending interval is never rebased "
                       "by a mid-interval haste change.",
        "research_ref": ["D/I: Trinity int32(period * f32 ModCastingSpeed) (SpellAuraEffects.cpp:1006-1007)"],
        "tc_anchors": {},
        "audit_refs": ["CSA-J-02", "NUM-E-001", "UNK-J-002"],
        "reopen_condition": "Track I's numeric boundary corpus fixes the Trinity period arithmetic (binary32 multiply) and D shows "
                            "when Trinity recalculates the period (per tick vs on refresh).",
    },
    {
        "id": "AL-K-K-21", "topic": "periodic-amount-snapshot", "questions": ["D", "C"], "status": "assumed",
        "anchors": {
            D + "action/periodic_health.rs:62-65": "FreshAtTick,",
            D + "action/input.rs:1105-1107": "FreshAtTick,",
            B + "aura_subtype.rs:381": "using fresh power and effective point stacks",
        },
        "observation": "Periodic health / trigger amounts have a single snapshot mode, FreshAtTick: base, attack/spell power and "
                       "effective point stacks are read at each occurrence. There is no application- or refresh-time amount "
                       "snapshot.",
        "research_ref": ["D: Trinity AuraEffect::CalculateAmount at apply/stack change (amount *= stacks), caster bonuses "
                         "at tick (snapshot-matrix)", "C: CalculateAmount SpellAuraEffects.cpp:777 amount *= stacks unless "
                         "SuppressPointsStacking"],
        "tc_anchors": {},
        "audit_refs": [],
        "reopen_condition": "Track D's snapshot matrix classifies any input of an admitted periodic family as "
                            "application-snapshot or recalculated-on-refresh rather than dynamic-each-tick.",
    },
    {
        "id": "AL-K-K-22", "topic": "rolling-periodic-and-channels", "questions": ["D", "B"], "status": "refused",
        "anchors": {
            B + "spell_attribute.rs:467": "disabled(SpellAttributeKind::RollingPeriodic",
            B + "spell_attribute.rs:364": "disabled(SpellAttributeKind::Channeled",
            C + "program/spell_attribute.rs:66-75": "SpellAttributeSupport::Unimplemented",
        },
        "observation": "Owners carrying Rolling Periodic (raw 334) or channel attributes (raw 34/38) are refused at compile time "
                       "(Disabled support class), so rolled-over periodic amounts and channel-owned aura lifetimes never "
                       "reach AuraState.",
        "research_ref": ["D/B: Trinity ATTR10_ROLLING_PERIODIC adds old amount * remainingTicks/totalTicks during "
                         "SetStackAmount (SpellAuraEffects.cpp:829-840); channel duration Spell.cpp:4014"],
        "tc_anchors": {"Spells/Auras/SpellAuraEffects.cpp:829": "SPELL_ATTR10_ROLLING_PERIODIC"},
        "audit_refs": [],
        "reopen_condition": "Core admits a raw-334 or channelled aura owner.",
    },
    {
        "id": "AL-K-K-23", "topic": "same-timestamp-ordering", "questions": ["I", "D", "E"], "status": "represented",
        "anchors": {
            C + "state/transition.rs:22-27": "(tick.at(), 5, 0)",
            C + "state/transition.rs:54-74": "Self::AuraExpiry(expiry) => (expiry.at(), 6, expiry.sequence())",
            C + "deadline_queue.rs:222-224": "(left.at, left.sequence, left.slot)",
            C + "periodic.rs:282-286": "scheduled tick tie-break must remain its canonical schedule slot",
        },
        "observation": "Transitions due at one millisecond run in rank order: resource startup 0, passive flow 1, hard-cast "
                       "completion 2, fixed impact 3, white swing 4, periodic tick 5, aura / independent-stack expiry 6; "
                       "ties inside a rank use (at, sequence, slot). A tick due exactly at expiry precedes the expiry.",
        "research_ref": ["I: Trinity Unit::_UpdateSpells order (Aura::Update duration, UpdateTargetMap, AuraEffect::Update, "
                         "then expired sweep, Unit.cpp:2957-2987) and map update granularity",
                         "E/F: UpdateOwner order SpellAuras.cpp:817-853"],
        "tc_anchors": {},
        "audit_refs": ["UNK-E-003", "UNK-F-001"],
        "reopen_condition": "Track I's ordering corpus proves a Trinity call-path order for tick-vs-expiry or cast-vs-tick at one "
                            "timestamp that contradicts ranks 2/5/6.",
    },
    {
        "id": "AL-K-K-24", "topic": "generation-safety", "questions": ["I", "B"], "status": "represented",
        "anchors": {
            C + "aura_state.rs:607-617": "payload: quote.after.generation",
            C + "aura_state.rs:1030-1036": "generation: u64,",
            C + "state.rs:1368-1382": "aura expiry commit must match the exact peeked generation",
        },
        "observation": "Each cell carries a u64 generation incremented on every non-independent (re)application; expiry nodes "
                       "carry it and are upserted per slot, so a refreshed aura cannot be expired by its stale deadline. "
                       "Removal cancels the slot's node.",
        "research_ref": ["I: generation.json (Trinity identity is the Aura object; stale-pointer / removed-application "
                         "handling)"],
        "tc_anchors": {},
        "audit_refs": ["LC-H-032"],
        "reopen_condition": "Track I shows a Trinity observable that depends on application identity across refresh (e.g. a "
                            "callback keyed to the original application surviving a refresh).",
    },
    {
        "id": "AL-K-K-25", "topic": "natural-expiry-sequence", "questions": ["E", "D"], "status": "represented",
        "anchors": {
            C + "state.rs:1383-1394": "self.execute_expiry_drivers(expired, output);",
            C + "expiry_execution.rs:9-40": "ResolvedExpirySource::OriginalAuraSource",
        },
        "observation": "Natural expiry: quote removal -> commit dependent authorities -> cancel periodic cells -> AuraExpired "
                       "observation -> health thresholds -> TriggerSpellOnExpire drivers (source = recipient or original "
                       "caster). Expiry drivers run only on natural aggregate expiry, never on explicit removal or "
                       "independent-stack expiry.",
        "research_ref": ["E: Trinity removal with AURA_REMOVE_BY_EXPIRE after _UpdateSpells sweep (Unit.cpp:2987); "
                         "AfterEffectRemove hooks see the remove mode"],
        "tc_anchors": {},
        "audit_refs": ["CSA-E-01", "LC-H-010"],
        "reopen_condition": "Track E shows an expire-triggered effect that Trinity also runs on another remove mode, or runs "
                            "before dependent state is unapplied.",
    },
    {
        "id": "AL-K-K-26", "topic": "remove-reason", "questions": ["E", "H"], "status": "absent",
        "anchors": {
            C + "event.rs:68-76": "pub enum AuraChangeKind {",
            C + "event.rs:463-480": "AuraStacksExpired {",
        },
        "observation": "Core's observable removal vocabulary is AuraChangeKind::Removed (explicit mutation or dispel) plus "
                       "AuraExpired / AuraStacksExpired (natural). There is no remove mode (default / interrupt / cancel / "
                       "enemy spell / expire / death), so no downstream behaviour can branch on it.",
        "research_ref": ["E: AuraRemoveMode SpellAuraDefines.h:55-64", "H: LINK_REMOVE skips the linked cast when "
                         "removeMode == AURA_REMOVE_BY_DEATH (SpellAuras.cpp:1421)"],
        "tc_anchors": {"Spells/Auras/SpellAuraDefines.h:55-64": "AURA_REMOVE_BY_ENEMY_SPELL"},
        "audit_refs": [],
        "reopen_condition": "Track E/H find a lifecycle callback on an admitted family whose Trinity behaviour depends on the "
                            "remove mode.",
    },
    {
        "id": "AL-K-K-27", "topic": "death", "questions": ["E", "D", "F"], "status": "absent",
        "anchors": {
            C + "periodic_execution.rs:449-457": "if aura_recipient_is_dead {",
            B + "spell_attribute.rs:391": "unimplemented(SpellAttributeKind::AllowAuraWhileDead",
            B + "spell_attribute.rs:436": "unimplemented(SpellAttributeKind::DisableAuraWhileDead",
        },
        "observation": "Death is not a lifecycle event: auras stay active on a corpse, their deadlines and on-expire drivers "
                       "stay armed, and fresh applications onto a dead actor commit. Only the periodic executor cancels a "
                       "schedule when its recipient is dead at a due tick (aura stays). A dead source's periodic keeps "
                       "ticking. Raw 116/226 owners are refused. Probes e_death_lifecycle (5) and h_death_lifecycle (4) pass "
                       "at the pin.",
        "research_ref": ["E: RemoveAllAurasOnDeath Unit.cpp:4472 keeps IsPassive || IsDeathPersistent (ATTR3_ALLOW_AURA_"
                         "WHILE_DEAD); auras cast by the dying unit on others are not removed; ATTR7_DISABLE_AURA_WHILE_DEAD "
                         "only in UnitAura::FillTargetMap"],
        "tc_anchors": {"Entities/Unit/Unit.cpp:4472": "void Unit::RemoveAllAurasOnDeath()"},
        "audit_refs": ["CSA-E-01", "CSA-H-01", "CSA-H-02", "CSA-G-01", "CSA-R2-01", "LC-H-010", "LC-E-001",
                       "LC-H-026", "UNK-H-001"],
        "reopen_condition": "Any death-lifecycle milestone in Core: track E's lifetime.json (which auras survive, remove "
                            "mode DEATH, caster-death non-effect) is the source-side reference to compare against.",
    },
    {
        "id": "AL-K-K-28", "topic": "dispel", "questions": ["E", "C"], "status": "refused",
        "anchors": {
            D + "action/catalog/aura.rs:104-108": "DispelTypeId::from_raw(1)",
            C + "program/dispel.rs:238-249": "\"no periodic lifecycle fanout\"",
            C + "execution/planning/dispel.rs:84-120": "random.uniform_index(candidate_count)",
            C + "program/dispel_mechanic.rs:383-395": "finite single-stack self-only raw-87 applicand",
        },
        "observation": "Dispel admits only Magic-family, finite, single-stack, single-deadline, self-reachable auras without "
                       "periodic schedules; one attempt selects one candidate uniformly and removes the whole aura. Dispel "
                       "resistance, per-stack / per-charge dispel (ATTR1_DISPEL_ALL_STACKS, ATTR7_DISPEL_REMOVES_CHARGES), "
                       "OnDispel hooks and dispel of periodic auras are outside the admitted slice.",
        "research_ref": ["E: GetDispellableAuraList Unit.cpp:4735, RemoveAurasDueToSpellByDispel Unit.cpp:4006 "
                         "(ModCharges vs ModStackAmount), CalcDispelChance SpellAuras.cpp:1237; mechanic dispel rolls before "
                         "the mechanic filter SpellEffects.cpp:4236"],
        "tc_anchors": {"Spells/Auras/SpellAuras.cpp:1237": "Aura::CalcDispelChance("},
        "audit_refs": ["CSA-E-02", "CSA-R3-01", "UNK-I-002"],
        "reopen_condition": "Core widens the dispel slice beyond max_stacks 1 / non-periodic; then track E's dispel.json "
                            "(stack vs charge removal, chance roll, RNG draw count) is the comparison target.",
    },
    {
        "id": "AL-K-K-29", "topic": "cancel-and-interrupt-removal", "questions": ["E", "H"], "status": "absent",
        "anchors": {
            B + "spell_attribute.rs:363": "unimplemented(SpellAttributeKind::NoAuraCancel",
            D + "spell_external_policy.rs:22-34": "pub aura_interrupt_policy: SpellExternalPolicyPresence,",
        },
        "observation": "Player cancel, AuraInterruptFlags removal (damage taken, movement, casting, combat leave) and "
                       "leave-combat removal have no Core path. SpellAuraInterrupts only exists as an external-policy "
                       "presence column that rejects on the SelectedTrait route; No Aura Cancel (raw 31) owners are refused.",
        "research_ref": ["E: RemoveAurasWithInterruptFlags Unit.cpp:4240; cancel via RemoveOwnedAura "
                         "(SpellHandler.cpp:284, track F)"],
        "tc_anchors": {},
        "audit_refs": ["CSA-K-05", "UNK-K-002"],
        "reopen_condition": "Track E/J count admitted-shape player auras with nonzero AuraInterruptFlags; any such family "
                            "admitted by Core is removing on an event Core cannot see.",
    },
    {
        "id": "AL-K-K-30", "topic": "passive-as-non-aura", "questions": ["G", "C"], "status": "assumed",
        "anchors": {
            B + "spell_attribute.rs:351": "no aura lifecycle",
            C + "program/spell_attribute.rs:17-47": "uncataloged_selected_spell_attribute",
        },
        "observation": "Passive (raw 6) owners compile as immutable definitions with explicit actor bindings and no aura "
                       "lifecycle; simultaneous aura or action ownership rejects the flag. Passives are therefore never "
                       "AuraState cells (no stacks, no removal, no refresh).",
        "research_ref": ["G: passive-active.json (Trinity passives are Auras: multislot, never refreshed, removed on "
                         "unlearn/shapeshift, may stack -- Phalanx capacity 2)", "selected-passive-package-generalization.md"],
        "tc_anchors": {},
        "audit_refs": ["CSA-K-05", "UNK-I-002", "UNK-K-003"],
        "reopen_condition": "Track G finds an admitted-shape passive whose Trinity aura is mutated after learning (stacks, "
                            "charges, removal, re-application on shapeshift).",
    },
    {
        "id": "AL-K-K-31", "topic": "external-policy-boundary", "questions": ["H"], "status": "refused",
        "anchors": {
            D + "spell_external_policy.rs:22-34": "pub external_aura_stack_mutation: SpellExternalPolicyPresence,",
            C + "program/spell_attribute.rs:17-75": "external_spell_policy",
        },
        "observation": "Scripts, spell_proc rows, aura-interrupt policy and external aura-stack mutation are admitted only as "
                       "presence columns (Absent/Present/Unknown); Present or Unknown rejects on the SelectedTrait route. No "
                       "script-controlled lifecycle input is representable; the ordinary-Passive route does not consult "
                       "the columns.",
        "research_ref": ["H: external-policy.json (spell_linked_spell LINK_AURA/LINK_REMOVE, spell_proc charges, "
                         "AuraScript hooks)"],
        "tc_anchors": {},
        "audit_refs": ["CSA-K-05", "CSA-A-02", "UNK-K-002", "UNK-K-003"],
        "reopen_condition": "Track H names a player lifecycle surface on a Core-admitted spell whose only source is a world "
                            "overlay or script (the host catalog would have to mark it Present).",
    },
    {
        "id": "AL-K-K-32", "topic": "dot-stacking-rule", "questions": ["A"], "status": "assumed",
        "anchors": {
            B + "spell_attribute.rs:384": "ignored(SpellAttributeKind::DotStackingRule",
            C + "program/spell_attribute.rs:66": "SpellAttributeSupport::Ignored => {}",
        },
        "observation": "Raw 103 (ATTR3_DOT_STACKING_RULE) is catalogued Ignored and passes owner admission; Core's per-caster key "
                       "already separates casters, so the flag cannot change Core behaviour.",
        "research_ref": ["A: Trinity IsStackableOnOneSlotWithDifferentCasters and Aura::CanStackWith use the flag to "
                         "decide shared-slot vs per-caster coexistence"],
        "tc_anchors": {"Spells/SpellInfo.cpp:1808-1811": "IsStackableOnOneSlotWithDifferentCasters"},
        "audit_refs": ["UNK-K-001"],
        "reopen_condition": "UNK-K-001's reopen (two casters, same admitted non-periodic aura) combined with track A's "
                            "coexistence rule.",
    },
]

# Probes of the Core semantic boundary audit that touch the lifecycle.  Re-run by
# track K at the Core pin (22/22 pass); the command lives in the corpus.
PROBES: list[dict] = [
    {"file": "e_periodic_schedule.rs", "tests": 7, "records": ["AL-K-K-17", "AL-K-K-18", "AL-K-K-19", "AL-K-K-20", "AL-K-K-23"],
     "confirms": "exact-expiry tick precedes expiry; restart cadence discards partial period; preserve cadence keeps "
                 "anchor; tick-on-application only on fresh application; explicit removal leaves no stale tick; hasted "
                 "period trunc(f64 division) incl. 1100/1.1 -> 999"},
    {"file": "e_capped_carryover.rs", "tests": 2, "records": ["AL-K-K-11", "AL-K-K-19"],
     "confirms": "natural-expiry proportional partial (100/200 full, 250 half); refresh at 150 of 250 carries "
                 "min(100, 75) -> expiry 475 with cadence preserved and remainder moved"},
    {"file": "e_death_lifecycle.rs", "tests": 5, "records": ["AL-K-K-27", "AL-K-K-25"],
     "confirms": "expiry driver runs after recipient death (reapplies to corpse / fizzles on damage child); fresh "
                 "periodic application onto a dead recipient admitted; periodic trigger silently cancelled after recipient "
                 "death; dead source's periodic keeps ticking"},
    {"file": "h_death_lifecycle.rs", "tests": 4, "records": ["AL-K-K-27"],
     "confirms": "dead caster keeps casting; on-expire driver fires from an aura on a dead recipient; aura application "
                 "to a dead target commits; control: living recipient fires once"},
    {"file": "k_aura_mutation_source.rs", "tests": 4, "records": ["AL-K-K-03", "AL-K-K-07"],
     "confirms": "raw-289 direction/count/identity unbound; ApplyAura row of A applies foreign aura B; raw-289 Set mode "
                 "admitted as Add; raw-203 removes the host aura, not EffectTriggerSpell"},
]
PROBE_COMMAND = ("cd scripts/research/core_audit/probe && CARGO_TARGET_DIR=<scratch>/targets/K CARGO_BUILD_JOBS=4 "
                 "cargo test --offline --locked --test e_periodic_schedule --test e_capped_carryover "
                 "--test e_death_lifecycle --test k_aura_mutation_source --test h_death_lifecycle")

# Lifecycle-bearing attributes (flattened raw = word*32 + bit).  ``why`` states
# the navigation reason; the Core support class and Trinity name are extracted.
LIFECYCLE_ATTRIBUTES: dict[int, tuple[str, str]] = {
    0: ("C", "proc failure burns a charge"),
    6: ("G", "passive: Trinity aura vs Core non-aura definition"),
    31: ("E", "no aura cancel: cancel eligibility"),
    34: ("B", "channelled: channel-owned aura lifetime"),
    38: ("B", "self channelled"),
    43: ("B", "aura unique: refresh suppression / Core PreserveExistingLifetime"),
    57: ("E", "aura stays after combat: leave-combat removal"),
    62: ("E", "dispel all stacks"),
    64: ("E", "allow dead target: application onto the dead"),
    103: ("A", "DoT stacking rule: per-caster vs shared slot"),
    116: ("E", "allow aura while dead: death survival"),
    121: ("D", "treat as periodic"),
    134: ("E", "cannot be stolen: spellsteal"),
    169: ("D", "extra initial period (tick on application)"),
    173: ("D", "spell haste affects periodic"),
    189: ("B", "aura unique per caster: refresh suppression"),
    225: ("B", "no target duration mod"),
    226: ("E", "disable aura while dead"),
    234: ("C", "dispel removes charges"),
    265: ("D", "periodic can crit"),
    273: ("B", "haste affects duration"),
    278: ("D", "melee haste affects periodic"),
    334: ("D", "rolling periodic"),
    344: ("G", "update passives on apply/remove"),
    428: ("C", "do not consume aura stack on proc"),
    436: ("B", "periodic refresh extends duration (pandemic)"),
    489: ("B", "aura does not refresh (Trinity ATTR15 UNK9)"),
    490: ("C", "asynchronous stacking buff (Trinity ATTR15 UNK10)"),
}

_COORD = re.compile(r"^(?P<path>[^:]+):(?P<first>\d+)(?:-(?P<last>\d+))?$")
_TC_ENUM = re.compile(r"^\s*(SPELL_ATTR(?P<word>\d+)_\w+)\s*=\s*0x(?P<hex>[0-9A-Fa-f]+)")


def _range_text(root: Path, coord: str) -> str:
    m = _COORD.match(coord)
    if not m:
        raise FailClosed(f"malformed coord {coord!r}")
    path = root / m["path"]
    if not path.is_file():
        raise FailClosed(f"coord {coord!r}: no such file under {root.name}")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    first, last = int(m["first"]), int(m["last"] or m["first"])
    if first < 1 or last > len(lines) or last < first:
        raise FailClosed(f"coord {coord!r}: outside 1..{len(lines)}")
    return "\n".join(lines[first - 1:last])


def anchor_problems(root: Path, anchors: dict[str, str]) -> list[str]:
    out = []
    for coord, anchor in anchors.items():
        try:
            text = _range_text(root, coord)
        except FailClosed as exc:
            out.append(str(exc))
            continue
        if anchor not in text:
            out.append(f"{coord}: anchor {anchor!r} not in range")
    return out


def verify_core_coords() -> int:
    """Core at the pin, clean, and every registry anchor present.  Returns the anchor count.

    Fails closed (never returns partial navigation) when any check fails.
    """
    from core_audit.pins import verify_core
    verify_core()
    problems: list[str] = []
    count = 0
    for rec in REGISTRY:
        problems += [f"{rec['id']}: {p}" for p in anchor_problems(CORE_ROOT, rec["anchors"])]
        count += len(rec["anchors"])
    if problems:
        raise FailClosed("Core navigation anchors drifted:\n" + "\n".join(problems))
    return count


def verify_trinity_coords() -> int:
    root = TC_ROOT / "src" / "server" / "game"
    if not root.is_dir():
        raise FailClosed(f"TrinityCore not checked out at {TC_ROOT}")
    problems: list[str] = []
    count = 0
    for rec in REGISTRY:
        problems += [f"{rec['id']}: {p}" for p in anchor_problems(root, rec["tc_anchors"])]
        count += len(rec["tc_anchors"])
    if problems:
        raise FailClosed("Trinity anchors drifted:\n" + "\n".join(problems))
    return count


@cache
def trinity_attribute_names() -> dict[int, str]:
    """``SharedDefines.h`` SpellAttr enums flattened to ``word*32 + bit`` -> name."""
    path = TC_ROOT / "src" / "server" / "game" / "Miscellaneous" / "SharedDefines.h"
    if not path.is_file():
        raise FailClosed(f"missing {path}")
    out: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _TC_ENUM.match(line)
        if not m:
            continue
        value = int(m["hex"], 16)
        if value == 0 or value & (value - 1):
            continue
        raw = int(m["word"]) * 32 + value.bit_length() - 1
        if raw in out and out[raw] != m.group(1):
            raise FailClosed(f"SharedDefines: raw {raw} named twice ({out[raw]}, {m.group(1)})")
        out[raw] = m.group(1)
    if len(out) < 400:
        raise FailClosed(f"SharedDefines: only {len(out)} attribute bits parsed")
    return out


@cache
def core_attribute_support() -> dict[int, tuple[str, str]]:
    """Core ``SpellAttributeKind`` raw -> (variant, support class), from Core's own catalog."""
    from selected_package import coreref
    parsed = coreref._parse(CORE_ROOT / "crates/dbc/src/spell_attribute.rs", "SpellAttributeKind")
    return {raw: (s.name, s.support) for raw, s in parsed.items()}


@cache
def core_subtype_support() -> dict[int, tuple[str, str]]:
    from selected_package import coreref
    parsed = coreref._parse(CORE_ROOT / "crates/dbc/src/aura_subtype.rs", "AuraSubtypeKind")
    return {raw: (s.name, s.support) for raw, s in parsed.items()}


def catalog_row_line(kind: str, variant: str) -> int:
    """1-based line of Core's catalog row for ``<Kind>::<variant>``."""
    rel = "crates/dbc/src/spell_attribute.rs" if kind == "SpellAttributeKind" else "crates/dbc/src/aura_subtype.rs"
    text = (CORE_ROOT / rel).read_text(encoding="utf-8")
    # Rows are written on one line or wrapped; report the line of the support-class call.
    pat = re.compile(rf"\b(implemented|ignored|disabled|unimplemented)\(\s*{kind}::{variant}\s*,")
    m = pat.search(text)
    if not m:
        raise FailClosed(f"no catalog row for {kind}::{variant}")
    return text.count("\n", 0, m.start()) + 1


def core_admission(support: str | None) -> str:
    """Owner admission of a Core attribute support class.

    Mirrors: crates/combat/src/program/spell_attribute.rs:17-75 (audit of every selected spell owner --
    action, program and registry owners; an aura declared for another spell is not itself an owner):
    Implemented is audited, Ignored passes, Disabled / Unimplemented / uncatalogued reject the owner.
    """
    if support is None:
        return "refused (uncatalogued)"
    return {"implemented": "implemented-slice (catalog text bounds the admitted carriers)", "ignored": "admitted-ignored",
            "disabled": "refused", "unimplemented": "refused"}[support]


def spell_attributes(row: dict | None) -> set[int]:
    if not row:
        return set()
    out = set()
    for word in range(17):
        value = int(row.get(f"Attributes_{word}") or 0) & 0xFFFFFFFF
        for bit in range(32):
            if value >> bit & 1:
                out.add(word * 32 + bit)
    return out


def attribute_table(ctx) -> list[dict]:
    """Lifecycle attributes x Core support x Trinity name x provider-carrier counts (populations all/player/controlled)."""
    from .providers import populations
    pops = populations(ctx)
    attrs = {spell: spell_attributes(ctx.data.row("SpellMisc", spell)) for spell in pops["all"]}
    core = core_attribute_support()
    tc = trinity_attribute_names()
    rows = []
    for raw, (question, why) in sorted(LIFECYCLE_ATTRIBUTES.items()):
        variant, support = core.get(raw, (None, None))
        counts = {name: sum(1 for s in pop if raw in attrs[s]) for name, pop in sorted(pops.items())}
        rows.append({
            "raw": raw, "word": raw // 32, "bit": raw % 32, "question": question, "why": why,
            "trinity_name": tc.get(raw), "core_variant": variant, "core_support": support,
            "core_admission": core_admission(support),
            "core_coord": (f"crates/dbc/src/spell_attribute.rs:{catalog_row_line('SpellAttributeKind', variant)}"
                           if variant else None),
            "provider_spell_carriers": counts,
            "evidence": ["core-navigation", "db2-fact"],
        })
    return rows


def policy_trigger_counts(ctx) -> dict:
    """Provider spells meeting the DB2 side of Core's lifetime-policy triggers, per population.

    Proxies (db2 side only; the host-resolved max stacks and base policy are not in DB2):
    * PreserveExistingLifetime/AuraUnique: raw 43 and CumulativeAura == 0 (aura_unique.rs:45-69)
    * PreserveExistingLifetime/AuraDoesNotRefresh: raw 489 (aura_reapplication.rs:427-453)
    * CappedCarryover: raw 436 (aura_reapplication.rs:455-457)
    * IndependentStackDeadlines: raw 490 and CumulativeAura > 1 (aura_lifetime.rs:11-35)
    """
    from .providers import populations
    pops = populations(ctx)
    out: dict[str, dict[str, int]] = {}
    tests = {
        "aura_unique_cumulative_0": lambda a, cum: 43 in a and cum == 0,
        "aura_unique_cumulative_positive": lambda a, cum: 43 in a and cum > 0,
        "aura_does_not_refresh_489": lambda a, cum: 489 in a,
        "periodic_refresh_extends_436": lambda a, cum: 436 in a,
        "asynchronous_stacking_490_cumulative_gt1": lambda a, cum: 490 in a and cum > 1,
        "asynchronous_stacking_490_cumulative_le1": lambda a, cum: 490 in a and cum <= 1,
    }
    facts = {}
    for spell in pops["all"]:
        opts = ctx.data.row("SpellAuraOptions", spell)
        facts[spell] = (spell_attributes(ctx.data.row("SpellMisc", spell)), int(opts["CumulativeAura"]) if opts else 0)
    for name, test in tests.items():
        out[name] = {pop: sum(1 for s in members if test(*facts[s])) for pop, members in sorted(pops.items())}
    return out


def subtype_coverage(ctx) -> dict[str, dict[str, int]]:
    """Provider effects by Core aura-subtype support class, per population (DIFFICULTY_NONE)."""
    from .providers import populations, provider_effects
    pops = populations(ctx)
    support = core_subtype_support()
    effects = provider_effects(ctx.data)
    out: dict[str, dict[str, int]] = {}
    for pop, members in sorted(pops.items()):
        counts: dict[str, int] = {}
        for e in effects:
            if e["spell"] not in members:
                continue
            cls = support.get(e["aura"], (None, "uncatalogued"))[1]
            counts[cls] = counts.get(cls, 0) + 1
        out[pop] = dict(sorted(counts.items()))
    return out


# --- Core arithmetic mirrors used by navigation timelines ------------------------------------------

def core_projected_expiry(policy: str, now: int, base: int, active_expiry: int | None) -> int | None:
    """Expiry (ms) of an application quote.

    Mirrors: crates/combat/src/aura_state.rs:1131-1200 (projected_expiry) and 747-766 (preserve).
    ``active_expiry`` is the current deadline of an active cell, ``None`` when inactive.
    """
    if policy == "PreserveExistingLifetime":
        return active_expiry if active_expiry is not None else now + base
    if policy not in ("RestartLifetime", "CappedCarryover", "IndependentStackDeadlines"):
        raise FailClosed(f"unknown Core reapplication policy {policy!r}")
    if base <= 0:
        raise FailClosed("Core refuses a zero or indefinite duration for this quote")
    carry = 0
    if active_expiry is not None and policy == "CappedCarryover":
        remaining = max(active_expiry - now, 0)
        carry = min(remaining, base * 3 // 10)
    projected = now + base + carry
    if active_expiry is not None and policy in ("CappedCarryover", "IndependentStackDeadlines"):
        return max(projected, active_expiry)
    return projected


def core_projected_stacks(active_stacks: int, incoming: int, maximum: int) -> int:
    """Stacks after an application quote.

    Mirrors: crates/combat/src/aura_state.rs:729-745 (reject incoming > maximum; add, saturate at maximum).
    """
    if incoming < 1 or maximum < 1:
        raise FailClosed("Core stack counts are nonzero")
    if incoming > maximum:
        raise FailClosed("StackCountExceedsMaximum")
    return min(active_stacks + incoming, maximum) if active_stacks else incoming


# --- Cross-cutting lists ----------------------------------------------------------------------------

UNKNOWNS: list[dict] = [
    {
        "id": "AL-U-K-01", "subject": "host resolver for lifecycle policy inputs",
        "question": "Which producer emits Core's host-authored lifecycle inputs (ResolvedAuraLifetimeInput, "
                    "ResolvedAuraReapplication, ResolvedPeriodicCadenceInput, TerminalInput, application count, max stacks) "
                    "from source data, and from which fields?",
        "known": "Core consumes them as trusted host input (AL-K-K-04/05/14/17/19) and only cross-checks a few against DB2 "
                 "(Aura Unique cumulative, raw 436 vs CappedCarryover).",
        "why_unresolved": "No generator exists in any pinned repo (same gap as UNK-K-002 for the policy catalog).",
        "evidence": ["core-navigation", "unresolved"],
        "coords": ["crates/data/src/action/input.rs:1112-1123", "crates/combat/src/program.rs:1650-1655"],
        "blocker": "missing producer", "reopen_condition": "A resolver/generator for the action catalog is written or audited; "
        "compare its emission against tracks B/C/D rules.", "build_skew": False,
    },
    {
        "id": "AL-U-K-02", "subject": "raw 489 / 490 semantics",
        "question": "Are Core's PreserveExistingLifetime (raw 489) and IndependentStackDeadlines (raw 490) readings of "
                    "Trinity's unnamed SPELL_ATTR15_UNK9/UNK10 retail-correct?",
        "known": "Trinity has no consumer of either bit at the pin; simc names bit 9 SX_AURA_DOES_NOT_REFRESH; Core's catalog "
                 "text implements both.",
        "why_unresolved": "no direct source consumer; semantics come from simc/Core naming only",
        "evidence": ["core-navigation", "retail-unknown"], "coords": ["crates/dbc/src/spell_attribute.rs:490-491"],
        "blocker": "retail observation", "reopen_condition": "Track L designs a retail refresh/per-stack-expiry experiment on "
        "a player carrier of raw 489 or 490.", "build_skew": False,
    },
]

FALSIFICATION: list[dict] = [
    {"id": "AL-F-K-01", "rule": "Core never shortens an active aura's deadline on reapplication",
     "attempt": "read projected_expiry for all four policies", "result": "falsified for RestartLifetime: the max with the "
     "old deadline is taken only for CappedCarryover / IndependentStackDeadlines (aura_state.rs:1188-1199)",
     "action": "recorded in AL-K-K-10"},
    {"id": "AL-F-K-02", "rule": "Core distinguishes remove reasons", "attempt": "enumerate CombatObservation aura variants",
     "result": "falsified: only Removed / AuraExpired / AuraStacksExpired (event.rs:68-76,463-480)", "action": "AL-K-K-26"},
    {"id": "AL-F-K-03", "rule": "expiry drivers run on every removal", "attempt": "trace execute_expiry_drivers callers",
     "result": "falsified: only natural aggregate expiry (state.rs:1394); independent-stack expiry and explicit removal "
     "never run them", "action": "AL-K-K-25"},
    {"id": "AL-F-K-04", "rule": "Core dispel removes one stack per dispel", "attempt": "read dispel admission",
     "result": "unreachable: dispellable auras must have max_stacks 1 (program/dispel.rs:238-243); whole aura removed",
     "action": "AL-K-K-28"},
    {"id": "AL-F-K-05", "rule": "periodic amounts are snapshotted at application", "attempt": "enumerate snapshot inputs",
     "result": "falsified: the only mode is FreshAtTick (periodic_health.rs:62-65, input.rs:1105-1107)",
     "action": "AL-K-K-21"},
    {"id": "AL-F-K-06", "rule": "recipient death removes or suspends the aura", "attempt": "re-run e_death_lifecycle / "
     "h_death_lifecycle probes at the pin", "result": "falsified: 9/9 probes pass asserting the aura survives on the "
     "corpse; only the periodic cell is cancelled at a due tick", "action": "AL-K-K-27"},
    {"id": "AL-F-K-07", "rule": "Core's capped carryover equals pinned Trinity's pandemic arithmetic",
     "attempt": "timeline D=250: apply 0, refresh at 225 (remaining 25) under core_projected_expiry vs the pinned "
     "Spell.cpp:3284-3288 expression as described by track B", "result": "diverges: Core 500, pinned Trinity 550 (carry "
     "always the 30% cap); equal at refresh 150 (475), which is why e_capped_carryover cannot see it",
     "action": "AL-K-K-11 reopen condition (B owns the Trinity verdict)"},
    {"id": "AL-F-K-08", "rule": "Core validates aura duration against SpellDuration", "attempt": "search GameData for "
     "SpellDuration values", "result": "falsified: only a presence bit per spell (game_data.rs:363-385)",
     "action": "AL-K-K-14"},
]


def navigation_records() -> list[dict]:
    """Registry rows in the shared ``core_navigation`` record shape (+ K-specific fields)."""
    out = []
    for rec in REGISTRY:
        if rec["status"] not in STATUSES:
            raise FailClosed(f"{rec['id']}: status {rec['status']!r}")
        bad = [q for q in rec["questions"] if q not in QUESTIONS]
        if bad:
            raise FailClosed(f"{rec['id']}: unknown questions {bad}")
        evidence = ["core-navigation"] + (["trinity-consumer"] if rec["tc_anchors"] else [])
        out.append({
            "id": rec["id"], "topic": rec["topic"], "questions": list(rec["questions"]), "status": rec["status"],
            "core_coords": sorted(rec["anchors"]), "anchors": dict(sorted(rec["anchors"].items())),
            "trinity_coords": dict(sorted(rec["tc_anchors"].items())),
            "research_ref": list(rec["research_ref"]), "audit_refs": list(rec["audit_refs"]),
            "observation": rec["observation"], "reopen_condition": rec["reopen_condition"], "evidence": evidence,
        })
    return sorted(out, key=lambda r: r["id"])


def question_matrix(records: list[dict]) -> dict[str, dict]:
    out = {}
    for q, text in QUESTIONS.items():
        hits = [r for r in records if q in r["questions"]]
        out[q] = {"question": text, "records": [r["id"] for r in hits],
                  "status_counts": {s: sum(1 for r in hits if r["status"] == s) for s in STATUSES}}
    return out
