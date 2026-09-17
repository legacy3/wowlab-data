"""Script target adapters (track E).

Which pinned-Trinity script hooks change the recipient set / order of a
current-player spell effect, and how.

Three layers:

1. **Dispatch** (engine consumer, exact):
   * ``Spell::CallScriptObjectAreaTargetSelectHandlers`` / ``...ObjectTargetSelect...`` /
     ``...DestinationTargetSelect...`` (Spell.cpp:8991/9004/9017) call a hook iff
     ``hook.IsEffectAffected(m_spellInfo, effIndex) && targetType.GetTarget() == hook.GetTarget()``
     where ``effIndex`` is the **first** effect of the effect-mask group being selected
     (Spell.cpp:787-788) and ``targetType`` is the selector being processed (TargetA *or*
     TargetB).  ``IsEffectAffected`` = ``TargetHook::CheckEffect`` (SpellScript.cpp:203,
     ported in :func:`dummy_semantics.bindings.target_hook_check_effect`).
   * ``Spell::CheckScriptEffectImplicitTargets`` (Spell.cpp:9066) -- :func:`script_allows_grouping`.
   * ``Aura::CallScriptCheckAreaTargetHandlers`` (SpellAuras.cpp:2067) via
     ``Aura::CanBeAppliedOn`` (SpellAuras.cpp:1607) -- AND of every ``DoCheckAreaTarget`` hook.
2. **Inventory** (script-consumer, hand-read): every target-hook registration of an
   in-scope spell plus every executing non-target hook that calls a target-relevant helper;
   each has a verdict in :data:`VERDICTS` / :data:`HELPER_VERDICTS` written after reading the
   handler body and the helpers it calls.  A registration without a verdict is a
   :class:`targeting.FailClosed` (the test suite enforces full coverage).
3. **Runtime adapters** over :class:`targeting.fixture.World` for the families used by the
   witnesses (aura-presence filter, explicit-target handling, random caps, object
   suppression, destination offset).  Smart ranking (``Trinity::SelectRandomInjuredTargets``,
   ``Trinity::SortTargetsWithPriorityRules``) belongs to track D (``targeting.smart``); the
   adapters call it when present and fail closed otherwise.

Scope tiers (``tiers()``): ``reach`` (``ctx.scope.reach``), ``controlled-unit`` (pet-family
SkillLineAbility spells + ClassID-0 (pet) SpecializationSpells + ``creature_template_spell`` of
creatures summoned by reach spells' DIFFICULTY_NONE effects 28/56/153), ``script-reach`` (spells cast -- ``cast``-category calls
with a SpellID argument -- by *executing* hooks of the tiers above, transitively, plus the
authored trigger edges of those children).  Only ``reach`` rows enter ``effect_classes``
(BRIEF §10); the other tiers are reported under ``effect_classes_extended``.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Iterable

from . import PINS, ROOT, TC_ROOT, FailClosed
from .trace import Trace

TARGET_HOOK_LISTS = ("OnObjectAreaTargetSelect", "OnObjectTargetSelect", "OnDestinationTargetSelect")
AURA_AREA_LIST = "DoCheckAreaTarget"
ADAPTER_LISTS = TARGET_HOOK_LISTS + (AURA_AREA_LIST,)
OLD_SNAPSHOT_COMMIT = "63326dd6c11ec16d47bd5b08f52030c3535e0065"   # 12.0.7.68367 (dummy-corpora/build-skew.json)
TC_SPELLS = "src/server/scripts/Spells/"

# Callees that make a non-target hook relevant to recipient selection (scan filter only;
# the verdict comes from reading the body).
HELPER_CALLEES = frozenset({
    "remove_if", "RandomResize", "sort", "resize", "clear", "push_back", "SelectRandomContainerElement",
    "erase", "emplace_back", "remove", "unique", "GetExplTargetUnit", "GetExplTargetWorldObject",
    "GetExplTargetDest", "RandomShuffle", "partition", "max_element", "min_element",
    "SelectRandomInjuredTargets", "SortTargetsWithPriorityRules", "UnitAuraCheck", "ObjectGUIDCheck",
    "HealthPctOrderPred", "ObjectDistanceOrderPred", "GetUnitTargetCountForEffect",
    "GetUnitTargetIndexForEffect", "SelectRandomWeightedContainerElement", "GetAttackableUnitListInRange",
    "GetFriendlyUnitListInRange", "GetAnyUnitListInRange", "GetCreatureListWithEntryInGrid",
    "GetPlayerListInGrid", "GetCreatureListWithOptionsInGrid", "VisitNearbyObject", "Cell",
    "UnitListSearcher", "GetTargetUnit", "GetSelectedUnit", "SetTarget", "SetExplTargetDest", "splice",
    "PreventHitDefaultEffect", "PreventHitEffect", "PreventHitAura",
    # added after the falsification pass (broader callee pattern over every in-scope executing hook)
    "VisitAllObjects", "GetVictim", "GetAttacker", "SelectNearbyTarget", "GetRandomNearPosition", "NearTeleportTo",
    "IsRaid", "GetAllMinionsByEntry", "IsInRange2d", "GetOwner", "GetGuardianPet", "GetThreatManager",
    "SearchTargets", "GetSearcherTypeMask",
    # added after hostile review R3-03 (stored-GUID / areatrigger / aura-application reads)
    "GetAreaTrigger", "GetAreaTriggers", "GetInsideUnits", "GetSingleCastAuras", "GetApplicationVector",
    "GetApplicationMap", "GetUnit", "GetCreature", "GetPlayer", "GetCreatureOrPetOrVehicle",
})
#: callee prefixes (templated calls keep their template argument in the index)
HELPER_CALLEE_PREFIXES = ("GetScript<",)

# ---------------------------------------------------------------------------
# hand-read verdicts (script-consumer)
# ---------------------------------------------------------------------------
# family vocabulary:
#   object-suppression        target = nullptr (effect loses its object target; no chain)
#   area-clear                targets.clear()
#   explicit-only             clear + push explicit target
#   explicit-removal          remove explicit target from the area list
#   explicit-preserving-random
#   aura-presence-filter      remove_if on aura presence (owner: caster / any)
#   guid-exclusion            remove_if(ObjectGUIDCheck) from cast custom arg
#   random-cap                Containers::RandomResize(n)
#   smart-injured             Trinity::SelectRandomInjuredTargets (track D ranks)
#   smart-priority-rules      Trinity::SortTargetsWithPriorityRules (track D ranks)
#   smart-sorted-cap          script sort + resize (own metric)
#   cross-effect-share        list copied from another effect's hook
#   state-capture             reads the list, does not change it
#   destination-offset        SpellDestination relocation
#   cast-fail                 FinishCast on list state
#   variant-redirect          casts another spell on the target, then suppresses
V = dict  # readability


def _v(family: str, summary: str, *, gate: str = "none", predicate: str | None = None, cap: str | None = None,
       rng: str = "none", explicit: str = "n/a", order: str = "independent", hidden: list[str] | None = None,
       runtime: str | None = None, smart: str | None = None, defects: list[str] | None = None,
       inputs: list[str] | None = None) -> dict[str, Any]:
    return {"family": family, "summary": summary, "gate": gate, "predicate": predicate, "cap": cap,
            "rng": rng, "explicit_target": explicit, "input_order": order, "hidden": hidden or [],
            "runtime": runtime, "smart_family": smart, "defects": defects or [], "runtime_inputs": inputs or []}


VERDICTS: dict[tuple[str, str], dict[str, Any]] = {
    # -- object suppression ------------------------------------------------------------------
    ("spell_pri_translucent_image", "PreventEffect"): _v(
        "object-suppression", "unconditional once loaded; Load() requires the caster to LACK Translucent Image",
        gate="script-load", runtime="suppress_object"),
    ("spell_dru_inner_peace", "PreventEffect"): _v(
        "object-suppression", "suppressed unless caster has Inner Peace; Validate() requires EFFECT_4 "
        "MOD_DAMAGE_PERCENT_TAKEN: true in 12.0.7, false in 12.1 (SCHOOL_ABSORB) -> not loaded on 12.1 rows",
        gate="caster-aura-absent",
        runtime="suppress_object", inputs=["caster aura SPELL_DRUID_INNER_PEACE"]),
    ("spell_dru_germination", "PickRejuvenationVariant"): _v(
        "variant-redirect", "with Germination talent and caster-owned Rejuvenation on the explicit target (and no longer "
        "Germination), casts Germination on the target and suppresses the Rejuvenation aura effect",
        gate="caster-aura+target-caster-aura-durations", explicit="replaced-by-variant-cast",
        hidden=["target->ToUnit() dereferenced without null check (TARGET_UNIT_TARGET_ALLY guarantees a unit)",
                "Germination cast happens during target selection (before CheckCast finishes launching the parent)"],
        inputs=["caster aura Germination", "target auras 774/155777 by caster with durations"]),
    ("spell_warl_seed_of_corruption_dummy", "RemoveVisualMissile"): _v(
        "object-suppression", "unconditional: effect 0 (visual) never lands on the explicit target", runtime="suppress_object"),
    ("spell_pri_unfurling_darkness", "PreventDirectDamage"): _v(
        "object-suppression", "suppressed unless the cast consumed Unfurling Darkness (m_appliedMods contains its aura) and "
        "was not triggered with an original cast id (Shadow Crash)", gate="spell-mod-consumed",
        inputs=["Spell::m_appliedMods", "Spell::m_originalCastId"]),
    ("spell_mage_ice_block", "PreventStunWithEverwarmSocks"): _v(
        "object-suppression", "suppressed when caster has Everwarm Socks", gate="caster-aura-present",
        runtime="suppress_object", inputs=["caster aura SPELL_MAGE_EVERWARM_SOCKS"]),
    ("spell_mage_ice_block", "PreventEverwarmSocks"): _v(
        "object-suppression", "suppressed unless caster has Everwarm Socks", gate="caster-aura-absent",
        runtime="suppress_object", inputs=["caster aura SPELL_MAGE_EVERWARM_SOCKS"]),
    ("spell_dru_entangling_roots", "HandleCuriousBramblepatch"): _v(
        "object-suppression", "suppressed unless caster has Curious Bramblepatch", gate="caster-aura-absent",
        runtime="suppress_object"),
    ("spell_monk_pressure_points", "PreventDispel"): _v(
        "object-suppression", "unconditional once loaded; Load() requires the caster to LACK Pressure Points",
        gate="script-load", runtime="suppress_object"),
    ("spell_pri_halo_effect_selector", "PreventUnwantedAura"): _v(
        "object-suppression", "unconditional", runtime="suppress_object"),
    ("spell_pri_auspicious_spirits", "PreventTarget"): _v(
        "object-suppression", "unconditional once loaded; Load() requires the caster to LACK Auspicious Spirits",
        gate="script-load", runtime="suppress_object"),
    ("spell_warr_frenzied_enrage", "HandleFrenziedEnrage"): _v(
        "object-suppression", "unconditional once loaded; Load() loads the script only when the caster LACKS "
        "Frenzied Enrage", gate="script-load", runtime="suppress_object",
        hidden=["static handler registered on EFFECT_0 and EFFECT_1: grouping {0,1} is indeterminate (TD-E-24); "
                "recipients unaffected (both registrations null the object)"]),
    ("spell_warr_powerful_enrage", "HandlePowerfulEnrage"): _v(
        "object-suppression", "unconditional once loaded; Load() requires the caster to LACK Powerful Enrage; "
        "Validate() fails on both 12.0.7 and 12.1 rows (EFFECT_3 is not ADD_PCT_MODIFIER) -> never loaded",
        gate="script-load", runtime="suppress_object"),
    ("spell_pal_blade_of_vengeance", "PreventProc"): _v(
        "object-suppression", "unconditional once loaded; Load() requires the caster to LACK Blade of Vengeance",
        gate="script-load", runtime="suppress_object"),
    ("spell_dk_icy_talons_buff", "HandleSmotheringOffense"): _v(
        "object-suppression", "suppressed unless caster has Smothering Offense", gate="caster-aura-absent",
        runtime="suppress_object"),
    ("spell_dh_deflecting_spikes", "HandleParryChance"): _v(
        "object-suppression", "suppressed unless caster has Deflecting Spikes", gate="caster-aura-absent",
        runtime="suppress_object"),
    ("spell_dk_reaper_of_souls", "HandleDefault"): _v(
        "object-suppression", "suppressed when the cast consumed the Reaper of Souls proc aura (m_appliedMods)",
        gate="spell-mod-consumed", hidden=["IsAffectedByReaperOfSouls (spell_dk.cpp:1225) reads Spell::m_appliedMods"],
        inputs=["Spell::m_appliedMods"]),
    ("spell_warr_thunder_blast", "PreventDefaultTargetObject"): _v(
        "object-suppression", "unconditional", runtime="suppress_object",
        hidden=["static handler registered on EFFECT_1 and EFFECT_2: grouping {1,2} is indeterminate (TD-E-24); "
                "recipients unaffected"]),
    ("spell_dh_enduring_torment_buff", "PreventEffect"): _v(
        "object-suppression", "suppressed unless the caster's primary specialization equals the template argument "
        "(Havoc for effects 0-1, Devourer for effects 2-3)", gate="caster-spec",
        hidden=["two distinct template instantiations -> CheckScriptEffectImplicitTargets splits the effect-mask "
                "group {0,1}|{2,3} although the structural handler name is identical",
                "GetCaster()->ToPlayer() unchecked; Load() requires IsPlayer()"],
        inputs=["caster primary specialization"]),
    ("spell_mage_ignition_burst", "PreventAura"): _v(
        "object-suppression", "unconditional once loaded; Load() requires the caster to LACK Ignition Burst",
        gate="script-load", runtime="suppress_object"),
    # -- area clear / explicit handling ------------------------------------------------------
    ("spell_warr_intimidating_shout", "FilterTargets"): _v(
        "explicit-removal", "removes the explicit target (all equal elements; nullptr if none); registered for EFFECT_2 "
        "which is single-target on 12.0.7/12.1 -> mask 0 (TD-E-20)",
        explicit="removed", runtime="remove_explicit"),
    ("spell_warr_intimidating_shout", "ClearTargets"): _v(
        "area-clear", "unconditional clear; registered for EFFECT_3 (single-target on current rows) -> mask 0 (TD-E-20)",
        runtime="clear_area"),
    ("spell_dru_entangling_roots", "HandleCuriousBramblepatchAOE"): _v(
        "area-clear", "cleared unless caster has Curious Bramblepatch", gate="caster-aura-absent", runtime="clear_area"),
    ("spell_warr_storm_bolts", "FilterTargets"): _v(
        "explicit-only", "list replaced by the explicit unit (empty if none); Load() loads the script only when the "
        "caster LACKS Storm Bolts (436162) -> with the talent the area list is untouched", gate="script-load",
        explicit="forced-only",
        order="independent", runtime="explicit_only",
        hidden=["the pushed explicit unit bypasses the area searcher (radius/relation/conditions) and "
                "SpellInfo::CheckTarget: AddUnitTarget(checkIfValid=false) Spell.cpp:1455; CheckEffectTarget still applies"]),
    ("spell_sha_molten_thunder_sundering", "RemoveIncapacitateEffect"): _v(
        "area-clear", "unconditional clear once loaded; Load() requires the caster to HAVE Molten Thunder; registered for "
        "EFFECT_3, which 197214 does not have on 12.0.7/12.1 -> mask 0 (TD-E-21)",
        gate="script-load", runtime="clear_area"),
    ("spell_dh_vengeful_retreat_damage", "HandleVengefulBonds"): _v(
        "area-clear", "cleared unless caster has Vengeful Bonds", gate="caster-aura-absent", runtime="clear_area"),
    ("spell_evo_fire_breath_damage", "RemoveUnusedEffect"): _v(
        "area-clear", "unconditional clear; Validate() requires EFFECT_2 to be SPELL_AURA_MOD_SILENCE, which neither "
        "12.0.7 nor 12.1 rows satisfy (DUMMY) -> never loaded", runtime="clear_area"),
    ("spell_evo_scouring_flame", "HandleScouringFlame"): _v(
        "area-clear", "cleared unless caster has Scouring Flame", gate="caster-aura-absent", runtime="clear_area"),
    ("spell_pal_blade_of_vengeance_aoe_target_selector", "RemoveExplicitTarget"): _v(
        "explicit-removal", "removes the explicit object", explicit="removed", runtime="remove_explicit"),
    ("spell_rog_airborne_irritant_target_selection", "FilterTargets"): _v(
        "explicit-removal", "removes the explicit object (EFFECT_ALL: every matching effect)", explicit="removed",
        runtime="remove_explicit"),
    ("spell_warl_seed_of_corruption_dummy", "SelectTarget"): _v(
        "explicit-preserving-random", "<2 candidates: unchanged; explicit target without caster's Seed: list := "
        "[explicit]; else remove every candidate with caster's Seed and keep one at random (or [explicit] if none left)",
        predicate="UnitAuraCheck(true, Seed, caster) removed", cap="1", rng="RandomResize(1): N urand draws when N>1",
        explicit="preferred", order="dependent (RandomResize keeps visit order; draw count = filtered size)",
        runtime="seed_of_corruption_select",
        hidden=["GetExplTargetUnit() dereferenced without null check",
                "registered for effects 1 and 2 (same function): if the engine groups them the hook runs once "
                "(effect 1 first); otherwise once per effect with independent draws",
                "pushed explicit unit bypasses searcher checks (Spell.cpp:1455)"],
        inputs=["explicit target auras by caster", "candidate auras by caster", "visit order", "rng"]),
    # -- aura filters ------------------------------------------------------------------------
    ("spell_warl_channel_demonfire_selector", "FilterTargets"): _v(
        "aura-presence-filter", "keeps only units with the caster's Immolate (or Wither when the caster has the "
        "Wither talent)", gate="caster-aura-switches-aura-id",
        predicate="remove_if(UnitAuraCheck(false, Immolate|Wither, caster)): keep HasAura(id, caster)",
        runtime="keep_with_caster_aura", inputs=["caster aura Wither talent", "candidate auras by caster"]),
    ("spell_pri_shadowy_apparition_dummy", "FilterTargets"): _v(
        "aura-presence-filter", "keeps only units with the caster's Vampiric Touch",
        predicate="remove_if(UnitAuraCheck(false, VT, caster))", runtime="keep_with_caster_aura",
        hidden=["UnitAuraCheck(WorldObject*) returns false for non-units -> non-units are KEPT by remove_if"]),
    ("spell_dru_embrace_of_the_dream_effect", "FilterTargets"): _v(
        "aura-presence-filter", "keeps units with a caster-owned SPELL_AURA_PERIODIC_HEAL from SPELLFAMILY_DRUID with "
        "flag128(0x50,0,0,0) (family-flag match, not a SpellID list)",
        predicate="!GetAuraEffect(PERIODIC_HEAL, DRUID, flag128(0x50), caster) removed",
        hidden=["membership decided by SpellClassMask flags of 12.1 rows -> build-skew sensitive"],
        inputs=["candidate periodic-heal aura effects by caster with class mask"]),
    ("spell_mage_ring_of_frost_freeze", "FilterTargets"): _v(
        "aura-presence-filter", "removes non-units, units with Ring of Frost dummy/freeze aura (any caster) and units "
        "outside the ring annulus [radiusA.Max, radiusB.Max] of 82676 effect 2 around the explicit dest (3D)",
        predicate="HasAura(dummy)||HasAura(freeze)||!IsInRange3d(dest, min, max)",
        hidden=["geometry adapter: annulus radii come from ANOTHER spell (SPELL_MAGE_RING_OF_FROST_SUMMON 113724 EFFECT_2, spell_mage.cpp:107) via CalcRadius(nullptr)",
                "IsInRange3d uses the destination WorldLocation, not the area center"],
        inputs=["candidate auras (any caster)", "3D distances to explicit dest"]),
    ("spell_gen_bloodlust", "FilterTargets"): _v(
        "aura-presence-filter", "removes non-units and units with Sated/Exhaustion/Temporal Displacement/Fatigued/"
        "Evoker Exhaustion (any caster)", predicate="HasAura(any of 5, any caster)",
        runtime="remove_with_any_aura"),
    ("spell_pri_dispersing_light_heal", "FilterTargets"): _v(
        "guid-exclusion", "removes the unit whose GUID the triggering script passed in m_customArg",
        predicate="ObjectGUIDCheck(args->TargetToExclude)",
        hidden=["std::any_cast of Spell::m_customArg (set by spell_pri_dispersing_light)"],
        inputs=["Spell::m_customArg"]),
    ("spell_pri_purge_the_wicked_dummy", "FilterTargets"): _v(
        "smart-sorted-cap", "removes non-units, the explicit target and units with a breakable-by-damage CC; sorts "
        "(no caster PtW first, ordered by distance from the explicit target; then by PtW duration ascending); keeps "
        "1 (+Revel in Purity EFFECT_1)", predicate="!unit || explicit || HasBreakableByDamageCrowdControlAura()",
        cap="1 + RevelInPurity.EFFECT_1", order="stable list::sort; ties keep visit order", explicit="removed",
        smart="pw-spread-nearest-unaffected",
        hidden=["comparator is not a strict weak ordering when both lack the aura only through distance ties -> stable",
                "GetExactDist from explTarget (3D); explTarget dereferenced unchecked",
                "HasBreakableByDamageCrowdControlAura (Unit.cpp:778) ignores aura type"],
        inputs=["candidate PtW aura durations by caster", "3D distances to explicit target", "CC auras", "visit order"]),
    ("spell_pal_holy_prism_selector", "FilterTargets"): _v(
        "smart-sorted-cap", ">5 candidates: on 114871 (hook on EFFECT_1 DEST_AREA_ENEMY: the damage bounce around a healed "
        "ally) sorts the ENEMIES by health fraction ascending and keeps 5; on 114852 (hook on EFFECT_1 DEST_AREA_ALLY: the heal "
        "bounce around a damaged enemy) RandomResize 5; stores the list for ShareTargets", cap="5",
        rng="RandomResize(5) for 114852 only", order="stable list::sort (114871); visit order (114852)",
        smart="lowest-health-pct",
        hidden=["HealthPctOrderPred(WorldObject) uses float ratio; non-unit or 0 max health -> 0.0 (CommonPredicates.cpp:39)",
                "branch keyed on GetSpellInfo()->Id, not on the hook registration"],
        inputs=["candidate health/max health", "visit order", "rng"]),
    ("spell_pal_holy_prism_selector", "ShareTargets"): _v(
        "cross-effect-share", "effect 2 list := list stored by effect 1's FilterTargets", explicit="n/a",
        hidden=["relies on effect 1 being selected before effect 2 (Spell.cpp:726 effect loop order)",
                "if effect 1's hook did not run (mask 0) the shared list is empty"]),
    ("spell_warl_channel_demonfire_selector_dummy", "n/a"): {},  # placeholder removed below
    # -- random / smart ---------------------------------------------------------------------
    ("spell_dru_starfall_dummy", "FilterTargets"): _v(
        "random-cap", "keeps 2 at random", cap="2", rng="RandomResize(2): N draws when N>2",
        order="dependent (visit order)", runtime="random_cap"),
    ("spell_sha_path_of_flames_spread", "FilterTargets"): _v(
        "explicit-removal+random", "removes the explicit unit, keeps candidates WITHOUT the caster's Flame Shock and "
        "picks one at random", predicate="copy_if(UnitAuraCheck(false, Flame Shock, caster))", cap="1",
        rng="RandomResize(pred, 1): draws = filtered size when > 1", explicit="removed",
        order="dependent (visit order)", runtime="path_of_flames_select",
        hidden=["RandomResize(pred) (Containers.h:88) filters by copy into a new list; non-units are dropped because "
                "UnitAuraCheck(WorldObject*) requires ToUnit()"]),
    ("spell_dru_wild_growth", "FilterTargets"): _v(
        "smart-injured", "SelectRandomInjuredTargets(EFFECT_1 value + Tree of Life EFFECT_2, players first, caster's "
        "raid first)", cap="EFFECT_1 value (+ToL)", rng="RandomShuffle of the cutoff bucket",
        order="std::ranges::sort (unstable) by priority bits", smart="injured-player-grouped",
        hidden=["engine MaxAffectedTargets RandomResize runs after the hook (Spell.cpp:1445)"]),
    ("spell_dru_efflorescence_heal", "FilterTargets"): _v(
        "smart-injured", "SelectRandomInjuredTargets(3, players first, caster's raid first)", cap="3",
        rng="RandomShuffle of the cutoff bucket", order="unstable sort", smart="injured-player-grouped"),
    ("spell_sha_ancestral_guidance_heal", "ResizeTargets"): _v(
        "smart-injured", "SelectRandomInjuredTargets(3, players first)", cap="3", rng="RandomShuffle cutoff",
        order="unstable sort", smart="injured-player"),
    ("spell_dru_yseras_gift_group_heal", "SelectTargets"): _v(
        "smart-injured", "SelectRandomInjuredTargets(1, players first)", cap="1", rng="RandomShuffle cutoff",
        order="unstable sort", smart="injured-player"),
    ("spell_pri_prayer_of_mending_jump", "FilterTargets"): _v(
        "smart-injured", "SelectRandomInjuredTargets(1, players first)", cap="1", rng="RandomShuffle cutoff",
        order="unstable sort", smart="injured-player"),
    ("spell_monk_burst_of_life_heal", "FilterTargets"): _v(
        "smart-injured", "SelectRandomInjuredTargets(SpellValue MaxAffectedTargets, players first, explicit target's "
        "raid first)", cap="m_spellValue->MaxAffectedTargets (a value of 0 selects nothing)",
        rng="RandomShuffle cutoff", order="unstable sort", smart="injured-player-grouped",
        hidden=["cap read from SpellValue (mods included); engine cap afterwards re-applies RandomResize with the same value "
                "-> no-op because size <= cap", "GetExplTargetUnit() may be nullptr -> no group preference"]),
    ("spell_pri_power_word_radiance", "FilterTargets"): _v(
        "smart-priority-rules", "SortTargetsWithPriorityRules(EFFECT_2 value + 1, [explicit, no caster Atonement, "
        "injured, player-like, in caster's raid]); records visual targets", cap="EFFECT_2 value + 1",
        rng="RandomShuffle of the tie range at the cutoff", explicit="preferred (rule 0)",
        order="std::ranges::sort (unstable) by score", smart="radiance-priority",
        hidden=["GetRadianceRules spell_priest.cpp:3387", "SortTargetsWithPriorityRules indexes [maxTargets-1] (UB for 0; "
                "caller adds 1)"]),
    # -- other -------------------------------------------------------------------------------
    ("spell_dh_blade_dance", "DecideFirstTarget"): _v(
        "state-capture", "no list change: with First Blood, records the caster's *selected* unit (Unit::GetTarget) if "
        "it is in the list (and list has >1), else the first list element, into spell_dh_first_blood",
        order="dependent (front of visit order)",
        hidden=["reads the player's UNIT_FIELD_TARGET selection, not the spell's explicit target",
                "SetFirstTarget spell_dh.cpp:1441 feeds a different AuraScript"],
        inputs=["caster selection guid", "visit order"]),
    ("spell_rog_killing_spree", "FilterTargets"): _v(
        "cast-fail", "FinishCast(OUT_OF_RANGE) when the list is empty or the caster is on a vehicle; list unchanged "
        "(mask 0 on 12.1: 51690:1 is APPLY_AURA TARGET_UNIT_CASTER, no DEST_AREA_ENEMY selector)",
        hidden=["FinishCast during target selection", "script drift TD-E-23"]),
    ("spell_pri_ultimate_penitence_jump", "SetDestTarget"): _v(
        "destination-offset", "dest.RelocateOffset({0,0,5}) (z + 5 yd)", runtime="dest_offset",
        hidden=["JumpOffset spell_priest.cpp:5107"]),
}
VERDICTS.pop(("spell_warl_channel_demonfire_selector_dummy", "n/a"))

#: Distinct C++ functions behind one structural handler name (template instantiations),
#: keyed by (class, registration line) -> function identity used by HasSameTargetFunctionAs.
FUNCTION_IDENTITY: dict[tuple[str, int], str] = {
    ("spell_dh_enduring_torment_buff", 1136): "PreventEffect<DemonHunterHavoc>",
    ("spell_dh_enduring_torment_buff", 1137): "PreventEffect<DemonHunterHavoc>",
    ("spell_dh_enduring_torment_buff", 1138): "PreventEffect<DemonHunterDevourer>",
    ("spell_dh_enduring_torment_buff", 1139): "PreventEffect<DemonHunterDevourer>",
}


#: Validate() predicates beyond ValidateSpellInfo (hand-read).  (spell | "self", effIndex, kind, value, misc)
#: kind: "exists" (ValidateSpellEffect), "aura" (GetEffect(i).IsAura(value)), "effect" (IsEffect(value)),
#: "aura+misc" (IsAura(value) && MiscValue == misc).
VALIDATE_PREDICATES: dict[str, list[tuple[Any, int, str, int, int | None]]] = {
    "spell_dh_deflecting_spikes": [("self", 0, "aura", 47, None)],                     # MOD_PARRY_PERCENT
    "spell_dru_inner_peace": [("self", 4, "exists", 0, None), ("self", 3, "aura", 147, None),   # MECHANIC_IMMUNITY_MASK
                              ("self", 4, "aura", 87, None)],                          # MOD_DAMAGE_PERCENT_TAKEN
    "spell_dru_wild_growth": [("self", 1, "exists", 0, None), (33891, 2, "exists", 0, None)],
    "spell_evo_fire_breath_damage": [("self", 2, "exists", 0, None), ("self", 2, "aura", 27, None)],  # MOD_SILENCE
    "spell_monk_pressure_points": [("self", 2, "exists", 0, None), ("self", 2, "effect", 38, None)],  # DISPEL
    "spell_pal_blade_of_vengeance": [("self", 2, "exists", 0, None), ("self", 2, "effect", 64, None)],  # TRIGGER_SPELL
    "spell_warr_frenzied_enrage": [("self", 1, "exists", 0, None), ("self", 0, "aura", 193, None),    # MELEE_SLOW
                                   ("self", 1, "aura", 31, None)],                     # MOD_INCREASE_SPEED
    "spell_warr_powerful_enrage": [("self", 4, "exists", 0, None), ("self", 3, "aura+misc", 108, 0),  # ADD_PCT_MODIFIER HealingAndDamage
                                   ("self", 4, "aura+misc", 108, 22)],                 # PeriodicHealingAndDamage
}
#: Validate() of every other in-scope adapter class is ValidateSpellInfo only (or absent) -- checked
#: against the catalog by :func:`validate_ok` through the structural index's validate spell list.
VALIDATE_SIMPLE_OR_NONE = True

#: Load() gates (hand-read): class -> (kind, aura/spec, meaning)
LOAD_GATES: dict[str, tuple[str, Any, str]] = {
    "spell_dh_enduring_torment_buff": ("caster-is-player", None, "Load: GetCaster()->IsPlayer()"),
    "spell_mage_ignition_burst": ("caster-lacks-aura", "SPELL_MAGE_IGNITION_BURST", "loaded only without the aura"),
    "spell_monk_pressure_points": ("caster-lacks-aura", "SPELL_MONK_PRESSURE_POINTS", "loaded only without the aura"),
    "spell_pal_blade_of_vengeance": ("caster-lacks-aura", "SPELL_PALADIN_BLADE_OF_VENGEANCE", "loaded only without the aura"),
    "spell_pri_auspicious_spirits": ("caster-lacks-aura", "SPELL_PRIEST_AUSPICIOUS_SPIRITS", "loaded only without the aura"),
    "spell_pri_translucent_image": ("caster-lacks-aura", "SPELL_PRIEST_TRANSLUCENT_IMAGE", "loaded only without the aura"),
    "spell_sha_molten_thunder_sundering": ("caster-has-aura", "SPELL_SHAMAN_MOLTEN_THUNDER_TALENT", "loaded only with the aura"),
    "spell_warr_frenzied_enrage": ("caster-lacks-aura", "SPELL_WARRIOR_FRENZIED_ENRAGE", "loaded only without the aura"),
    "spell_warr_powerful_enrage": ("caster-lacks-aura", "SPELL_WARRIOR_POWERFUL_ENRAGE", "loaded only without the aura"),
    "spell_warr_storm_bolts": ("caster-lacks-aura", "SPELL_WARRIOR_STORM_BOLTS", "loaded only without the aura"),
}


def _row_pred(row: tuple[int, int, int, int] | None, kind: str, value: int, misc: int | None, misc_of_row: int | None) -> bool:
    if row is None:
        return False
    effect, aura, *_ = row
    if kind == "exists":
        return True
    if kind == "effect":
        return effect == value
    if kind == "aura":
        return effect != 0 and aura == value and _is_aura_effect(effect)
    if kind == "aura+misc":
        return effect != 0 and aura == value and _is_aura_effect(effect) and misc_of_row == misc
    raise FailClosed(f"unknown validate predicate kind {kind}")


def _is_aura_effect(effect: int) -> bool:
    """Mirrors: SpellInfo.cpp:465-497 ``IsAura`` = (IsUnitOwnedAuraEffect || PERSISTENT_AREA_AURA) (ApplyAuraName != 0 checked by caller)."""
    from procs.enums import effect as eff
    names = ("APPLY_AREA_AURA_PARTY", "APPLY_AREA_AURA_RAID", "APPLY_AREA_AURA_FRIEND", "APPLY_AREA_AURA_ENEMY",
             "APPLY_AREA_AURA_PET", "APPLY_AREA_AURA_OWNER", "APPLY_AREA_AURA_SUMMONS",
             "APPLY_AREA_AURA_PARTY_NONRANDOM", "APPLY_AURA", "APPLY_AURA_ON_PET", "PERSISTENT_AREA_AURA")
    return effect in {eff(n) for n in names}


def validate_ok(ctx, class_name: str, validate_spells: Iterable[int], spell: int) -> dict[str, Any]:
    """Evaluate a script class's ``Validate(entry)`` against the 12.1 and 12.0.7 rows.

    Mirrors: SpellScriptBase::_ValidateSpellInfo / ``_Validate`` (SpellScript.cpp:26-34): a failing
    Validate means the script is never attached to the spell.
    """
    cat = ctx.bundle.catalog
    missing = sorted(s for s in validate_spells if not cat.exists(s))
    out = {"validate_spells_missing_12_1": missing}
    old = old_effect_rows()
    ok_new, ok_old = not missing, True
    for which, idx, kind, value, misc in VALIDATE_PREDICATES.get(class_name, []):
        sid = spell if which == "self" else which
        info = cat.get(sid)
        e = info.effect(idx) if info else None
        new_row = (e.effect, e.aura, e.target_a, e.target_b) if e else None
        ok_new &= _row_pred(new_row, kind, value, misc, int(e.misc0) if e else None)
        orow = old.get(sid, {}).get(idx)
        # 12.0.7 MiscValue is not loaded by old_effect_rows; misc predicates are compared on 12.1 only
        ok_old &= _row_pred(orow, "aura" if kind == "aura+misc" else kind, value, None, None)
    out["validate_ok_12_1"] = ok_new
    out["validate_ok_12_0_7"] = ok_old
    return out


# ---------------------------------------------------------------------------
# mechanical Validate() evaluation (hostile review R3-08)
# ---------------------------------------------------------------------------
_VALIDATE_CACHE: dict[str, dict[str, Any]] = {}


def _class_source(ctx, sc) -> str:
    path = TC_ROOT / sc.file
    if not path.exists():
        raise FailClosed(f"pinned TrinityCore source {sc.file} not available")
    lines = path.read_text(errors="ignore").splitlines()
    return "\n".join(lines[sc.line - 1:sc.end_line])


def _function_body(text: str, header_regex: str) -> str | None:
    m = re.search(header_regex, text)
    if not m:
        return None
    i, depth = m.end(), 1
    while depth and i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[m.end():i - 1]


def _validate_body(ctx, sc) -> tuple[str | None, str | None]:
    """(body, owning class) of the Validate() override visible from ``sc`` (same-file bases)."""
    seen = set()
    cur = sc
    while cur is not None and cur.name not in seen:
        seen.add(cur.name)
        body = _function_body(_class_source(ctx, cur), r"bool\s+Validate\s*\([^)]*\)\s*override\s*\{")
        if body is not None:
            return body, cur.name
        nxt = None
        for base in cur.bases:
            cand = ctx.bundle.index.resolve_class(base, cur.file)
            if cand is not None and cand.kind in ("SpellScript", "AuraScript"):
                nxt = cand
                break
        cur = nxt
    return None, None


def _spell_exists(ctx, sid: int) -> bool:
    """``sSpellMgr->GetSpellInfo(id, DIFFICULTY_NONE)``: DB2 or serverside_spell (SpellMgr.cpp:2887)."""
    return ctx.bundle.catalog.exists(sid) or (sid, 0) in _serverside(ctx)


_SS: dict[int, dict] = {}


def _serverside(ctx) -> dict:
    if id(ctx) not in _SS:
        _SS[id(ctx)] = ctx.bundle.world.serverside_spells()
    return _SS[id(ctx)]


def _effect_count(ctx, sid: int) -> int:
    """``GetEffects().size()`` = highest DIFFICULTY_NONE EffectIndex + 1 (dense vector, SpellInfo.cpp:1323-1330)."""
    rows = ctx.data.effects(sid)
    if rows:
        return max(e.index for e in rows) + 1
    ss = _serverside(ctx).get((sid, 0))
    if ss and ss["_effects"]:
        return max(int(e["EffectIndex"]) for e in ss["_effects"]) + 1
    return 0


def mechanical_validate(ctx, sc, spell: int) -> dict[str, Any]:
    """Evaluate ``Validate(spellInfo)`` of ``sc`` for ``spell`` on the 12.1 rows.

    Mirrors: SpellScript.cpp:26-34 (a false Validate drops the script), SpellScript.cpp:43-70
    (ValidateSpellInfoImpl / ValidateSpellEffectImpl: every id / (id, index) is checked, no
    short-circuit).  Mechanical parts: ``ValidateSpellInfo({...})`` and ``ValidateSpellEffect({{id, EFFECT_n}...})``
    with ids from the file's enum constants or ``spellInfo->Id``.  ``spellInfo->GetEffect(..)``
    predicates come from :data:`VALIDATE_PREDICATES` (hand-read).  Anything else -> ``unparsed``
    (reported; the script is then counted as loaded).
    """
    body, owner = _validate_body(ctx, sc)
    out: dict[str, Any] = {"validate_owner": owner, "failures": [], "unparsed": []}
    if body is None:
        out["ok"] = True
        return out
    consts = ctx.bundle.index.constants.get(sc.file, {})
    if owner and owner != sc.name:
        oc = ctx.bundle.index.resolve_class(owner, sc.file)
        consts = {**ctx.bundle.index.constants.get(oc.file, {}), **consts} if oc else consts

    def resolve(tok: str) -> int | None:
        tok = tok.strip()
        if re.fullmatch(r"spellInfo->Id|GetId\(\)|m_scriptSpellId", tok):
            return spell
        if tok.isdigit():
            return int(tok)
        name = tok.split("::")[-1]
        return consts.get(name)
    handled = body
    for m in re.finditer(r"ValidateSpellInfo\s*\(\s*\{(.*?)\}\s*\)", body, re.S):
        for tok in re.split(r",", m.group(1)):
            tok = re.sub(r"//.*", "", tok).strip()
            if not tok:
                continue
            sid = resolve(tok)
            if sid is None:
                out["unparsed"].append(f"ValidateSpellInfo token {tok!r}")
            elif not _spell_exists(ctx, sid):
                out["failures"].append(f"spell {tok}={sid} missing")
        handled = handled.replace(m.group(0), "")
    for m in re.finditer(r"ValidateSpellEffect\s*\(\s*\{(.*?)\}\s*\)\s*(?=[;&|)]|$)", body, re.S):
        for pm in re.finditer(r"\{\s*([^,{}]+?)\s*,\s*EFFECT_(\d+)\s*\}", m.group(1)):
            sid = resolve(pm.group(1))
            idx = int(pm.group(2))
            if sid is None:
                out["unparsed"].append(f"ValidateSpellEffect token {pm.group(1)!r}")
            elif not _spell_exists(ctx, sid) or _effect_count(ctx, sid) <= idx:
                out["failures"].append(f"{pm.group(1)}={sid} EFFECT_{idx} missing")
        handled = handled.replace(m.group(0), "")
    residue = re.sub(r"//[^\n]*", "", handled)
    residue = re.sub(r"\breturn\b|&&|\|\||[;(){}\s]|\btrue\b", "", residue)
    if residue:
        if sc.name in VALIDATE_PREDICATES or owner in VALIDATE_PREDICATES:
            v = validate_ok(ctx, owner if owner in VALIDATE_PREDICATES else sc.name, [], spell)
            if not v["validate_ok_12_1"]:
                out["failures"].append("hand-read GetEffect predicate fails on 12.1")
        else:
            out["unparsed"].append(residue[:120])
    out["ok"] = not out["failures"]
    return out


def script_loads(spell_id: int, script_name: str, ctx=None, strict: bool = True) -> bool:
    """Stable entry point (track G grouping): does ``script_name`` attach to ``spell_id`` on the 12.1 rows?

    Mirrors: SpellScript.cpp:26-34 (``_Validate``) for every SpellScript/AuraScript class the
    registration name instantiates (ScriptMgr ``CreateSpellScripts``); False if any class fails.
    ``strict``: a Validate() construct that is neither mechanical nor hand-read raises FailClosed.
    Load() (caster state) is not evaluated here -- see :data:`LOAD_GATES`.
    """
    from . import context
    ctx = ctx or context.get()
    res = ctx.bundle.index.resolve_script_name(script_name)
    if not res["resolved"]:
        raise FailClosed(f"script name {script_name!r} has no SpellScript/AuraScript registration")
    ok = True
    for sc in res["classes"]:
        v = validate_cached(ctx, sc, spell_id)
        if strict and v["unparsed"]:
            raise FailClosed(f"Validate() of {sc.name} for {spell_id} not evaluable: {v['unparsed']}")
        ok &= v["ok"]
    return ok


def validate_cached(ctx, sc, spell: int) -> dict[str, Any]:
    key = f"{sc.key}|{spell}"
    if key not in _VALIDATE_CACHE:
        _VALIDATE_CACHE[key] = mechanical_validate(ctx, sc, spell)
    return _VALIDATE_CACHE[key]


# ---------------------------------------------------------------------------
# Register()-time conditional registration (hostile review R3-04; lead unknown TG-H-03)
# ---------------------------------------------------------------------------
#: (class, handler, target token) -> spell -> "registered" | "not-registered" | "caster-state"
REGISTER_CONDITIONS: dict[tuple[str, str, str | None], Callable[[int], str]] = {
    # spell_paladin.cpp:1157-1160
    ("spell_pal_holy_prism_selector", "FilterTargets", "TARGET_UNIT_DEST_AREA_ALLY"):
        lambda s: "registered" if s == 114852 else "not-registered",
    ("spell_pal_holy_prism_selector", "FilterTargets", "TARGET_UNIT_DEST_AREA_ENEMY"):
        lambda s: "registered" if s == 114871 else "not-registered",
    # spell_druid.cpp:943-945
    ("spell_dru_entangling_roots", "HandleCuriousBramblepatchAOE", "TARGET_UNIT_DEST_AREA_ENEMY"):
        lambda s: "registered" if s == 102359 else "not-registered",
    # spell_priest.cpp:2271-2302 / 2318-2330: registered unless the caster's talents select that effect
    ("spell_pri_halo_effect_selector", "PreventUnwantedAura", "TARGET_UNIT_CASTER"): lambda s: "caster-state",
    ("spell_pri_halo_return_effect_selector", "PreventUnwantedAura", "TARGET_UNIT_CASTER"): lambda s: "caster-state",
}
#: classes whose Register() branches (R3 regscan + lead TG-H-03); helper verdicts note the gate where it matters
CONDITIONAL_REGISTER_CLASSES = {
    "spell_dh_collective_anguish", "spell_dh_demonic_appetite_energize", "spell_dk_subduing_grasp",
    "spell_dru_entangling_roots", "spell_evo_ruby_embers", "spell_pal_holy_prism_selector", "spell_pri_entropic_rift",
    "spell_pri_halo_effect_selector", "spell_pri_halo_return_effect_selector", "spell_rog_blade_flurry",
    "spell_sha_deeply_rooted_elements", "spell_sha_primordial_wave",
}

#: static handler functions registered on >1 effect of one spell (hostile review R3-05): HasSameTargetFunctionAs
#: compares all 16 bytes of ImplStorage but placement-new writes only the 8-byte function pointer
#: (SpellScript.h:149-178, 522-549); the other 8 bytes are indeterminate -> grouping unspecified.
STATIC_HANDLER = "static"


def _h(kind: str, summary: str, *, changes_recipients: bool = False, reads: str | None = None,
       rng: str = "none", smart: str | None = None, hidden: list[str] | None = None) -> dict[str, Any]:
    return {"kind": kind, "summary": summary, "changes_recipients": changes_recipients, "reads": reads,
            "rng": rng, "smart_family": smart, "hidden": hidden or []}


#: Non-target hooks that call a target-relevant helper.  kind:
#:   recipient-count-consumer / recipient-order-consumer / explicit-target-consumer  -> dependency edge (payload)
#:   cast-gate          -> CheckCast on the explicit target / dest (track B)
#:   dest-consumer      -> uses the explicit destination for another cast
#:   script-target-selection -> the script selects recipients of ANOTHER cast (its own search/list)
#:   effect-suppression -> PreventHitDefaultEffect (recipient stays in the target map)
#:   not-targeting      -> random choice among spells, not among targets
HELPER_VERDICTS: dict[tuple[str, str], dict[str, Any]] = {
    ("spell_pri_prayerful_litany", "CalcPrimaryTargetHealing"): _h("explicit-target-consumer", "bonus when victim == explicit target", reads="explicit unit"),
    ("spell_warr_improved_whirlwind_cleave", "CalculateDamage"): _h(
        "recipient-order-consumer", "bonus for every recipient except index 0 of GetUnitTargetIndexForEffect",
        reads="unique-target list order", hidden=["index = position in m_UniqueTargetInfo among targets carrying the effect"]),
    ("spell_mage_flame_patch", "HandleFlamePatch"): _h("dest-consumer", "casts Flame Patch at the explicit dest", reads="explicit dest"),
    ("spell_warr_meat_cleaver_damage_bonus", "HandleDamageBonus"): _h("recipient-count-consumer", "bonus when target count >= talent value", reads="target count"),
    ("spell_warr_meat_cleaver_damage_bonus_thunder_clap", "HandleDamageBonus"): _h("recipient-count-consumer", "bonus when target count >= talent value", reads="target count"),
    ("spell_warr_heroic_leap", "CheckElevation"): _h("cast-gate", "dest required; rooted / pathing / +4 yd elevation checks", reads="explicit dest",
                                                    hidden=["PathGenerator in instanceable maps (world geometry)"]),
    ("spell_pal_holy_shock", "CheckCast"): _h("cast-gate", "explicit unit required; hostile targets must be attackable and in front", reads="explicit unit"),
    ("spell_warr_bloodthirst_enrage", "Enrage"): _h("explicit-target-consumer", "enrage/Fresh Meat only when hit unit == explicit target",
                                                   reads="explicit unit", rng="roll_chance"),
    ("spell_warr_deft_experience", "HandleDeftExperience"): _h("explicit-target-consumer", "only when hit unit == explicit target", reads="explicit unit"),
    ("spell_dru_innervate", "CheckCast"): _h("cast-gate", "explicit unit must be a player whose primary spec role is Healer", reads="explicit unit spec role"),
    ("spell_mage_ice_lance", "IndexTarget"): _h("recipient-order-consumer", "records launch order of hit units", reads="launch order"),
    ("spell_warr_rumbling_earth", "HandleCooldownReduction"): _h("recipient-count-consumer", "CDR when EFFECT_0 count >= talent value", reads="target count"),
    ("spell_pri_penance", "CheckCast"): _h("cast-gate", "hostile explicit target must be attackable and in front", reads="explicit unit"),
    ("spell_dreamwalker_guardian_spirit_restriction", "SkipWithWeakenedSoul"): _h(
        "cast-gate", "boss-file script bound to a player spell: explicit unit required and must not have Weakened Soul",
        reads="explicit unit auras", hidden=["registered from boss_valithria_dreamwalker.cpp but bound by spell_script_names globally"]),
    ("spell_rog_killing_spree_aura", "HandleEffectPeriodic"): _h(
        "dead-by-drift", "would pick a random GUID from the hit list each tick, but the list writer "
        "spell_rog_killing_spree::HandleDummy (EFFECT_1 SPELL_EFFECT_DUMMY) has mask 0 on 12.0.7 and 12.1 (51690:1 is "
        "APPLY_AURA) -> list always empty: no teleport, no weapon damage, no RNG draw",
        reads="empty hit list", hidden=["script drift TD-E-23"]),
    ("spell_hun_masters_call", "DoCheckCast"): _h("cast-gate", "pet state + explicit unit + pet LOS", reads="explicit unit, pet"),
    ("spell_rog_tricks_of_the_trade", "DoAfterHit"): _h("explicit-target-consumer", "stores explicit unit as the threat redirect target", reads="explicit unit"),
    ("spell_sha_molten_assault", "TriggerFlameShocks"): _h(
        "script-target-selection", "own 10 yd enemy search around the hit unit (Flame Shock check), partition by "
        "caster Flame Shock, shuffle the unaffected part, cast Flame Shock on the first value+1",
        changes_recipients=True, reads="grid visit order", rng="RandomShuffle of the unaffected partition (only if missing>0)",
        smart="refresh-affected-then-random",
        hidden=["std::partition is not stable", "flameShocksMissing is size_t: value+1-affected may wrap when affected > value+1 "
                "(then non-zero -> shuffle still happens)"]),
    ("spell_sha_healing_rain", "InitializeVisualStalker"): _h("dest-consumer", "summons the visual stalker at the explicit dest; the aura later casts heals at that position", reads="explicit dest"),
    ("spell_hun_binding_shot", "HandleCast"): _h("dest-consumer", "visual arrow cast at explicit dest", reads="explicit dest"),
    ("spell_sha_restorative_mists", "HandleHeal"): _h("recipient-count-consumer", "heal divided by target count", reads="target count",
                                                    hidden=["integer division by GetUnitTargetCountForEffect"]),
    ("spell_sha_elemental_blast", "TriggerBuff"): _h("not-targeting", "random buff choice (weighted by absence)", rng="weighted/random element"),
    ("spell_mage_supernova", "HandleDamage"): _h("explicit-target-consumer", "bonus damage on the explicit target", reads="explicit unit"),
    ("spell_sha_artifact_gathering_storms", "TriggerBuff"): _h("recipient-count-consumer", "buff amount * target count", reads="target count"),
    ("spell_sha_converging_storms", "TriggerBuff"): _h("recipient-count-consumer", "stacks = min(target count, 6)", reads="target count"),
    ("spell_sha_crash_lightning", "TriggerCleaveBuff"): _h("recipient-count-consumer", "cleave buff when count >= 2", reads="target count"),
    ("spell_sha_unrelenting_storms", "Trigger"): _h("recipient-count-consumer", "CDR/Windfury when count <= limit", reads="target count"),
    ("spell_sha_thorims_invocation_primer", "UpdateThorimsInvocationSpell"): _h("recipient-count-consumer", "records CL vs LB by EFFECT_0 count", reads="target count"),
    ("spell_sha_chain_lightning_crash_lightning", "HandleDamageBuff"): _h("recipient-count-consumer", "stacks = count when > 1", reads="target count"),
    ("spell_sha_chain_lightning_energize", "HandleScript"): _h("recipient-count-consumer", "energize * EFFECT_0 count", reads="target count"),
    ("spell_warr_improved_whirlwind", "HandleHit"): _h("recipient-count-consumer", "rage from EFFECT_0 count", reads="target count"),
    ("spell_sha_molten_thunder_sundering", "RollReset"): _h("recipient-count-consumer", "reset chance by min(count, limit)", reads="target count", rng="roll_chance"),
    ("spell_rog_shuriken_storm", "HandleEnergize"): _h("recipient-count-consumer", "combo points = target count", reads="target count"),
    ("spell_dru_shooting_stars", "OnTick"): _h(
        "script-target-selection", "each tick: 100 yd world search for caster Moonfire/Sunfire periodic effects; per DoT "
        "list, procs = floor(chance/100) + roll(frac); RandomResize(procs); Crashing Star roll per target",
        changes_recipients=True, reads="grid visit order (UnitWorker)", rng="roll_chance + RandomResize + per-target roll",
        smart="dot-bearing-random", hidden=["float procs -> size_t conversion in RandomResize", "chance = amount * sqrt(n)"]),
    ("spell_dh_demonic_appetite", "ShatterLesserSoulFragment"): _h("not-targeting", "random left/right fragment spell", rng="SelectRandomContainerElement"),
    ("spell_sha_path_of_flames_spread", "HandleScript"): _h("explicit-target-consumer", "copies explicit target's Flame Shock duration to the hit unit", reads="explicit unit aura"),
    ("spell_evo_causality_pyre", "HandleCooldown"): _h("recipient-count-consumer", "CDR * min(count, limit)", reads="target count"),
    ("spell_evo_verdant_embrace_trigger_heal", "HandleHitTarget"): _h(
        "script-target-selection", "hit unit casts the heal on the explicit target (recipient = explicit, caster = hit unit)",
        changes_recipients=True, reads="explicit unit"),
    ("spell_pri_ultimate_penitence_channel", "HandlePeriodic"): _h(
        "script-target-selection", "each tick: own area search (GetSearcherTypeMask + WorldObjectSpellAreaTargetCheck with "
        "the child spell's range and conditions); damage: random enemy; heal: injured allies ranked by "
        "SortTargetsWithPriorityRules(1, [player-like, in caster's raid unless ungrouped])",
        changes_recipients=True, reads="grid visit order", rng="SelectRandomContainerElement / tie shuffle",
        smart="penitence-heal-priority",
        hidden=["heal-first vs damage-first decided by BASE_POINT1 set from explicit target hostility at cast"]),
    ("spell_pri_ultimate_penitence", "TriggerImmunity"): _h("explicit-target-consumer", "explicit hostility selects heal-first/damage-first mode", reads="explicit unit"),
    ("spell_pri_divine_procession", "HandleProc"): _h(
        "script-target-selection", "extends the Atonement with the smallest remaining duration (min_element over the "
        "Atonement script's target list; missing units rank last)", changes_recipients=True,
        reads="atonement target list order (first minimum wins)", smart="lowest-duration"),
    ("spell_dh_shattered_souls_devourer", "HandleProc"): _h("not-targeting", "random fragment spell cast at the actor", rng="SelectRandomContainerElement"),
    ("spell_dh_shattered_souls_devourer", "HandleSoulsGathering"): _h(
        "script-target-selection", "consumes the first N (aura amount) own soul-fragment areatriggers within the proc "
        "spell's max range", changes_recipients=True, reads="Unit::GetAreaTriggers order"),
    ("spell_sha_windfury_weapon", "HandleEffect"): _h("effect-suppression", "default dummy prevented; enchant cast on main-hand item"),
    ("spell_mage_cauterize", "SuppressSpeedBuff"): _h("effect-suppression", "trigger-spell default effect prevented"),
    ("spell_pri_halo_effect_selector", "PreventHitDefaultEffect"): _h("effect-suppression", "CREATE_AREATRIGGER default prevented (hook bound to the base method)"),
    ("spell_warr_execute", "HandleExecuteCast"): _h("effect-suppression", "default trigger prevented and re-cast on the same hit unit with custom rage args (recipient unchanged)"),
    ("spell_warr_rampaging_ruin", "HandleSingleTarget"): _h(
        "effect-suppression", "single-target triggers (effects 1-4) prevented while Rampaging Ruin + Whirlwind cleave are up",
        hidden=["caster-state mode switch between single-target and cone children"]),
    ("spell_warr_rampaging_ruin", "HandleCone"): _h("effect-suppression", "cone trigger (effect 5) prevented unless Rampaging Ruin + Whirlwind cleave are up"),
    ("spell_sha_primordial_wave", "PreventLavaSurge"): _h(
        "effect-suppression", "Lava Surge trigger (EFFECT_5) default prevented ONLY when the caster's primary spec is not "
        "Elemental: Register() reads the spec (spell_shaman.cpp:2448-2461); Elemental casters trigger 77762 on themselves",
        hidden=["register-time caster-spec gate (lead unknown TG-H-03)",
                "EnergizeMaelstrom (EFFECT_4) registered only for spec None/Enhancement"]),
    # -- added by the falsification pass (broader callee pattern) --------------------------------
    ("spell_warl_seduction", "HandleScriptEffect"): _h("not-targeting", "owner glyph gate for aura removal on the hit unit", reads="caster owner auras"),
    ("spell_mage_ice_barrier", "HandleProc"): _h("proc-actor-target", "damaged unit casts Chilled on the attacker (recipient = proc actor)",
                                                 reads="DamageInfo attacker"),
    ("spell_rog_blade_flurry", "CheckProc"): _h(
        "proc-gate+rng", "Unit::SelectNearbyTarget(action target) picks a random unfriendly unit (1 urand draw when >= 1 "
        "candidate) and the proc passes only if one exists; the pick is never used on 12.1 because HandleProc (EFFECT_0 aura "
        "MOD_POWER_REGEN_PERCENT / MOD_MELEE_HASTE) has mask 0 (13877:0 is SPELL_AURA_DUMMY) -> no extra-attack recipient",
        reads="grid visit order", rng="SelectRandomContainerElement (Unit.cpp:10948), only when candidates exist",
        hidden=["Unit.cpp:10920-10949", "script drift TD-E-22: HandleProc registrations dead on 12.0.7 and 12.1"]),
    ("spell_warl_devour_magic", "OnSuccessfulDispel"): _h("controlled-unit-owner-cast", "pet heals itself; owner heals itself with the glyph",
                                                        reads="pet owner"),
    ("spell_warl_seed_of_corruption_dummy_aura", "HandleProc"): _h(
        "proc-actor-target", "detonation cast at the proc action target (the seeded unit) when the caster's damage exceeds the "
        "threshold or another Seed detonation hits", reads="DamageInfo attacker == caster"),
    ("spell_pri_shadow_word_death", "DetermineKillStatus"): _h("not-targeting", "backlash on the caster when the victim survives", reads="victim health"),
    ("spell_mage_water_elemental_freeze", "HandleImprovedFreeze"): _h("controlled-unit-owner-cast", "owner casts Fingers of Frost on itself", reads="pet owner"),
    ("spell_warl_demonic_circle_teleport", "HandleTeleport"): _h("not-targeting", "caster teleports to its Demonic Circle game object", reads="owned game object"),
    ("spell_dk_dancing_rune_weapon", "HandleProc"): _h(
        "controlled-unit-victim", "rune weapon (first m_Controlled with the entry) deals half the damage to ITS current victim",
        changes_recipients=True, reads="m_Controlled order, guardian victim",
        hidden=["DealDamage directly (no spell target selection); recipient = DRW's GetVictim()"]),
    ("spell_rog_tricks_of_the_trade_aura", "OnRemove"): _h("not-targeting", "threat redirect cleanup"),
    ("spell_rog_tricks_of_the_trade_proc", "HandleRemove"): _h("not-targeting", "threat redirect cleanup"),
    ("spell_gen_major_healing_cooldown_modifier", "CalculateHealingBonus"): _h("not-targeting", "healing bonus by map type (IsRaid)"),
    ("spell_gen_major_healing_cooldown_modifier_aura", "CalculateHealingBonus"): _h("not-targeting", "healing bonus by map type (IsRaid)"),
    ("spell_rog_mastery_main_gauche", "HandleCheckProc"): _h("proc-actor-target", "requires a damage victim"),
    ("spell_rog_mastery_main_gauche", "HandleProc"): _h("proc-actor-target", "Main Gauche cast at the damage victim", reads="DamageInfo victim"),
    ("spell_sha_lava_surge", "CheckProcChance"): _h(
        "world-count-consumer", "proc chance normalised by the count of caster Flame Shock effects within 100 yd", reads="grid search count",
        rng="roll_chance", hidden=["1.0 / flameShocks with flameShocks == 0 -> +inf (float division)"]),
    ("spell_dru_efflorescence_dummy", "HandlePeriodicDummy"): _h(
        "controlled-unit-owner-cast", "the Efflorescence creature's owner casts the heal at the creature (dest/area center = summon)",
        changes_recipients=True, reads="summon owner"),
    ("spell_pri_angelic_feather_trigger", "HandleEffectDummy"): _h(
        "script-target-selection", "caster inside [radius min,max] (2D) of the dest -> aura on the caster, else areatrigger at the dest",
        changes_recipients=True, reads="explicit dest, caster position", hidden=["IsInRange2d uses the effect radius pair of this effect"]),
    ("spell_mage_ring_of_frost", "HandleEffectPeriodic"): _h("dest-consumer", "freeze cast at the tracked Ring of Frost minion's position", reads="owned minion"),
    ("spell_mage_ring_of_frost", "Apply"): _h("not-targeting", "keeps the newest Ring of Frost minion, despawns older ones", reads="owned minions"),
    ("spell_warl_channel_demonfire_activator", "RemoveEffect"): _h("world-count-consumer", "removes the activator when no unit within 100 yd has the caster's aura",
                                                                   reads="grid search (UnitSearcher first match)"),
    ("spell_mage_touch_of_the_magi_aura", "HandleProc"): _h("not-targeting", "accumulates damage the caster dealt to the aura owner"),
    ("spell_warr_execute_refund_rage", "DetermineKillStatus"): _h("not-targeting", "rage refund when the victim survives", reads="victim health"),
    ("spell_sha_fire_nova", "TriggerDamage"): _h(
        "script-target-selection", "Fire Nova damage cast on EVERY unit within 40 yd (3D) carrying the caster's Flame Shock",
        changes_recipients=True, reads="grid visit order (UnitListSearcher)", smart="dot-bearing-all",
        hidden=["FireNovaTargetCheck spell_shaman.cpp:1191 has no hostility / LOS / attackability check"]),
    ("spell_mage_alter_time_aura", "AfterRemove"): _h("not-targeting", "teleport back within 100 yd"),
    ("spell_sha_primordial_wave", "TriggerDamage"): _h(
        "script-target-selection", "Primordial Wave damage on every unit within the spell's hostile max range carrying the caster's Flame Shock",
        changes_recipients=True, reads="grid visit order", smart="dot-bearing-all",
        hidden=["same FireNovaTargetCheck with MaxSearchRange = GetMinMaxRange(false).Max"]),
    ("spell_warr_bloodsurge", "HandlePeriodic"): _h("world-count-consumer", "energize chance = sqrt(#units with caster Rend within 50 yd) * amount",
                                                   reads="grid search count", rng="roll_chance"),
    ("spell_pri_divine_image", "HandleProc"): _h(
        "script-target-selection", "Divine Image (first controlled unit with the entry) casts the mapped spell with a COPY of the "
        "proc spell's SpellCastTargets; teleports near the priest (GetRandomNearPosition 3 yd) when > 15 yd away",
        changes_recipients=True, reads="proc spell m_targets", rng="GetRandomNearPosition",
        hidden=["target inheritance: explicit targets of the priest's spell reused by the image",
                "eventInfo.GetProcSpell() dereferenced unchecked in DivineImageHelpers::Trigger (spell_priest.cpp:1218)"]),
    ("spell_sha_flame_shock_fire_nova_enabler", "CheckFlameShocks"): _h("world-count-consumer", "enabler aura kept while any unit within 40 yd has the caster's Flame Shock",
                                                                        reads="grid search (first match)"),
    # -- added after hostile review R3-03 (stored GUIDs, areatriggers, aura applications, GetScript<>) --------------
    ("spell_warl_rain_of_fire", "HandleDummyTick"): _h(
        "script-target-selection", "every tick: union of GetInsideUnits of all own Rain of Fire areatriggers; each unit that is "
        "not IsFriendlyTo the warlock gets the damage cast", changes_recipients=True,
        reads="areatrigger inside-unit sets (GuidUnorderedSet: hash order)", smart="areatrigger-occupants",
        hidden=["only IsFriendlyTo is checked (no attackability/LOS); iteration order of GuidUnorderedSet is unspecified",
                "areatrigger membership is maintained by AreaTrigger search (track I)"]),
    ("spell_sha_stormblast_proc", "SaveCastId"): _h("not-targeting", "stores the cast id in the Stormblast talent script"),
    ("spell_pal_consecration", "HandleEffectPeriodic"): _h(
        "dest-consumer", "damage cast at the position of the FIRST own Consecration areatrigger (Unit::GetAreaTrigger)",
        changes_recipients=True, reads="owned areatrigger list order",
        hidden=["with several Consecrations only the first AT's position is used"]),
    ("spell_pri_prayer_of_mending", "HandleEffectDummy"): _h("not-targeting", "passes the Focused Mending flag to the aura script"),
    ("spell_pal_light_s_beacon", "HandleProc"): _h(
        "script-target-selection", "beacon heal: proc ACTOR casts on the target of the first application of the caster's "
        "single-cast Beacon of Light", changes_recipients=True, reads="single-cast aura list, application vector order",
        smart="beacon-first-application",
        hidden=["GetApplicationVector order (application map order) picks the recipient when several applications exist",
                "eventInfo.GetHealInfo() dereferenced unchecked"]),
    ("spell_rog_tricks_of_the_trade_aura", "HandleProc"): _h("not-targeting", "self buff when the stored redirect target still resolves",
                                                           reads="stored GUID"),
    ("spell_pri_atonement", "HandleOnProc"): _h(
        "script-target-selection", "Atonement heal cast on every stored Atonement target within EFFECT_1 yards (2D) of the "
        "priest; unresolvable GUIDs are erased", changes_recipients=True,
        reads="_appliedAtonements vector order (TriggerAtonementHealOnTargets spell_priest.cpp:607-634)",
        smart="stored-guid-list", hidden=["2D distance (IsInDist2d)", "list maintained by spell_pri_atonement_effect_aura apply/remove"]),
    ("spell_pal_holy_prism_selector", "HandleScript"): _h(
        "script-target-selection", "beam visual cast by the effect-0 hit unit onto each effect-2 hit unit", changes_recipients=True,
        reads="stored GUID (SaveTargetGuid)"),
    ("spell_sha_thorims_invocation_trigger", "TriggerLightningSpell"): _h(
        "explicit-target-consumer", "casts the stored Lightning Bolt/Chain Lightning id at the hit unit", reads="script field spell id"),
    ("spell_dru_efflorescence", "InitSummon"): _h(
        "controlled-unit-owner-cast", "each summoned Efflorescence creature (execute log) casts the aura on itself",
        reads="execute-log summon GUIDs"),
    ("spell_sha_earthen_rage_proc_aura", "HandleEffectPeriodic"): _h(
        "script-target-selection", "periodic damage cast on the LAST proc action target stored by the Earthen Rage passive "
        "(written by spell_sha_earthen_rage_passive::HandleEffectProc, spell_shaman.cpp:874-881)", changes_recipients=True,
        reads="stored GUID via GetScript<>", smart="last-proc-target"),
    ("spell_pri_atonement_effect_aura", "HandleOnApply"): _h("state-capture", "adds the aura owner to the priest's Atonement target list",
                                                            reads="caster Atonement script"),
    ("spell_pri_atonement_effect_aura", "HandleOnRemove"): _h("state-capture", "removes the aura owner from the Atonement list"),
    ("spell_pri_power_word_radiance", "HandleEffectHitTarget"): _h("not-targeting", "visuals toward the stored extra targets"),
    ("spell_dh_blade_dance_damage", "HandleHitTarget"): _h("recipient-order-consumer", "bonus on the unit recorded as first target",
                                                          reads="First Blood stored GUID"),
    ("spell_pri_trail_of_light", "HandleOnProc"): _h(
        "script-target-selection", "heal cast on the heal target from two heals ago (_healQueue.front()) if still an assist "
        "target within the heal's max range", changes_recipients=True, reads="stored GUID queue",
        smart="previous-heal-target", hidden=["queue maintained by CheckProc of the same script"]),
    ("spell_pri_mind_devourer_buff", "ModifyAuraValueAndRemoveBuff"): _h("not-targeting", "stores a damage bonus in the hit aura's script"),
    ("spell_pri_deaths_torment", "HandleProc"): _h(
        "script-target-selection", "schedules N delayed Shadow Word: Death casts at the proc action target (GUID resolved at "
        "each delay)", changes_recipients=True, reads="stored GUID"),

}

HOOK_ENGINE_SITES = {
    "OnObjectAreaTargetSelect": "Spell.cpp:1305 (cone), 1437 (area), 1851 (chain), 1998 (line); dispatcher 8991",
    "OnObjectTargetSelect": "Spell.cpp:1050/1068 (channel), 1208 (nearby), 1794 (caster object), 1814 (target object); "
                            "2035/2112 pass SpellImplicitTargetInfo() (target 0) and can never match; dispatcher 9004",
    "OnDestinationTargetSelect": "Spell.cpp:1075, 1148, 1172, 1261, 1432, 1649, 1686, 1738, 1959; dispatcher 9017",
    "DoCheckAreaTarget": "SpellAuras.cpp:1625 via CanBeAppliedOn (UpdateTargetMap 684/717); dispatcher 2067 (AND of all hooks)",
}


# ---------------------------------------------------------------------------
# engine rules
# ---------------------------------------------------------------------------
def hook_fires(hook_mask: int, hook_target: int, first_eff_of_group: int, selector_target: int) -> bool:
    """Whether a target hook runs while selecting ``selector_target`` for a group.

    Mirrors: Spell.cpp:8996/9009/9022 -- ``IsEffectAffected(m_spellInfo, effIndex) &&
    targetType.GetTarget() == hook.GetTarget()``; ``effIndex`` is the group's first effect
    (Spell.cpp:787-788 pass ``spellEffectInfo`` of the loop effect).
    """
    return bool(hook_mask & (1 << first_eff_of_group)) and selector_target == hook_target


@dataclass(frozen=True)
class HookRef:
    """One registered target hook, as the grouping rule sees it."""
    list: str
    function: str       # identity used by HasSameTargetFunctionAs
    mask: int           # GetAffectedEffectsMask
    static: bool = False  # handler is a static/free function (8-byte pointer in a 16-byte storage)
    registration: int = 0  # distinct per `+=` (two registrations of one function are distinct objects)


def script_allows_grouping(scripts: list[list[HookRef]], eff: int, other: int) -> bool:
    """``Spell::CheckScriptEffectImplicitTargets(eff, other)``.

    Mirrors: Spell.cpp:9066-9097.  For each loaded script and for the ObjectTarget and
    ObjectAreaTarget lists only (Destination hooks are NOT compared), every hook affecting
    one index must have a hook with the same function affecting the other index, in both
    directions.  The hook's target type is not compared.
    """
    def same(h: HookRef, o: HookRef) -> bool:
        if h.function != o.function:
            return False       # different functions: storage differs in the written pointer bytes
        if h.static and h.registration != o.registration:
            # SpellScript.h:149-178 / 546-549: ImplStorage (16 bytes) is never value-initialised and
            # placement-new writes only the 8-byte function pointer -> bytes 8-15 indeterminate (TD-E-24)
            raise FailClosed(f"HasSameTargetFunctionAs on two registrations of static {h.function}: indeterminate")
        return True

    def shared(hooks: list[HookRef], a: int, b: int) -> bool:
        for h in hooks:
            if not h.mask & (1 << a):
                continue
            if not any(o.mask & (1 << b) and same(h, o) for o in hooks):
                return False
        return True

    for hooks in scripts:
        for lst in ("OnObjectTargetSelect", "OnObjectAreaTargetSelect"):
            sub = [h for h in hooks if h.list == lst]
            if not shared(sub, eff, other) or not shared(sub, other, eff):
                return False
    return True


def check_area_target(hook_results: Iterable[bool]) -> bool:
    """Mirrors: SpellAuras.cpp:2067-2079 -- ``result &= hook(target)`` over every loaded script (no short-circuit)."""
    result = True
    for r in hook_results:
        result &= bool(r)
    return result


# ---------------------------------------------------------------------------
# scope tiers
# ---------------------------------------------------------------------------
@dataclass
class Tiers:
    reach: set[int]
    controlled_unit: set[int]
    script_reach: set[int]
    script_parent: dict[int, int] = field(default_factory=dict)
    not_loaded: dict[int, list[str]] = field(default_factory=dict)   # spell -> classes whose Validate fails on 12.1

    def tier(self, spell: int) -> str | None:
        if spell in self.reach:
            return "reach"
        if spell in self.controlled_unit:
            return "controlled-unit"
        if spell in self.script_reach:
            return "script-reach"
        return None


def _summon_entries(ctx, spells: Iterable[int]) -> set[int]:
    out = set()
    for s in spells:
        info = ctx.bundle.catalog.get(s)
        if info is None:
            continue
        for e in info.effects:
            if e.effect in (28, 56, 153) and int(e.misc0):
                out.add(int(e.misc0))
    return out


def compute_tiers(ctx, bm) -> Tiers:
    from controlled_units import spells as cus
    b = ctx.bundle
    reach = set(ctx.scope.reach)
    cts = cus.creature_template_spells()
    cu = {int(r["spell"]) for r in cus.family_tables()["rows"]}
    d = cus._db2()
    for spec_id, rows in d["spec_spells"].items():          # pet specializations (ClassID 0)
        if d["specs"].get(spec_id, {}).get("ClassID") == "0":
            cu.update(int(r["SpellID"]) for r in rows)
    for entry in _summon_entries(ctx, reach):
        cu.update(cts.get(entry, {}).values())
    cu = {s for s in cu if s and b.catalog.exists(s)} - reach
    seen = reach | cu
    stack = sorted(seen)
    sr: set[int] = set()
    parent: dict[int, int] = {}
    not_loaded: dict[int, set[str]] = defaultdict(set)
    while stack:
        s = stack.pop()
        kids: set[int] = set()
        for bd, sc, h in _registrations(ctx, bm, s):
            if not h.executes:
                continue
            if not validate_cached(ctx, sc, s)["ok"]:      # R3-08: never-attached scripts cast nothing
                not_loaded[s].add(sc.name)
                continue
            for c in h.facts.get("calls", []):
                if c["cat"] == "cast":
                    kids.update(v for v in c.get("ints", []) if v >= 100)
        if s in sr:
            kids.update(ch for ch, _ in ctx.scope.edges.get(s, []))
        for k in sorted(kids):
            if k not in seen and b.catalog.exists(k):
                seen.add(k)
                sr.add(k)
                parent[k] = s
                stack.append(k)
    return Tiers(reach=reach, controlled_unit=cu, script_reach=sr, script_parent=parent,
                 not_loaded={k: sorted(v) for k, v in not_loaded.items()})


# ---------------------------------------------------------------------------
# 12.0.7 rows (drift)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def old_effect_rows() -> dict[int, dict[int, tuple[int, int, int, int]]]:
    """12.0.7.68367 SpellEffect rows (DIFFICULTY 0): spell -> index -> (effect, aura, targetA, targetB).

    Read from git (``build-skew.json`` old commit); unavailable -> FailClosed.
    """
    try:
        text = subprocess.run(["git", "-C", str(ROOT), "show", f"{OLD_SNAPSHOT_COMMIT}:data/tables/SpellEffect.csv"],
                              capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FailClosed(f"12.0.7 SpellEffect.csv unavailable from git: {exc}") from exc
    out: dict[int, dict[int, tuple[int, int, int, int]]] = defaultdict(dict)
    for r in csv.DictReader(io.StringIO(text)):
        if r["DifficultyID"] != "0":
            continue
        out[int(r["SpellID"])][int(r["EffectIndex"])] = (int(r["Effect"]), int(r["EffectAura"]),
                                                         int(r["ImplicitTarget_0"]), int(r["ImplicitTarget_1"]))
    return dict(out)


def old_mask(spell: int, hook_list: str, eff_index: Any, target: int) -> int | None:
    """TargetHook mask against the 12.0.7 rows (None when the spell did not exist)."""
    from dummy_semantics.bindings import TARGET_HOOK_FLAGS, target_hook_check_effect
    rows = old_effect_rows().get(spell)
    if rows is None:
        return None
    area, dest = TARGET_HOOK_FLAGS[hook_list]

    def check(i: int) -> bool:
        r = rows.get(i)
        return target_hook_check_effect(target, area, dest, r[2] if r else None, r[3] if r else None)
    mask = 0
    if eff_index in ("EFFECT_ALL", "EFFECT_FIRST_FOUND"):
        for i in sorted(rows):
            if eff_index == "EFFECT_FIRST_FOUND" and mask:
                break
            if check(i):
                mask |= 1 << i
    elif isinstance(eff_index, int) and check(eff_index):
        mask = 1 << eff_index
    return mask


# ---------------------------------------------------------------------------
# inventory
# ---------------------------------------------------------------------------
def _registrations(ctx, bm, spell: int):
    """(binding, ScriptClass, HookBinding) for each hook registration of ``spell``."""
    for bd in bm.bindings(spell):
        for sc in ctx.bundle.index.resolve_script_name(bd.script_name)["classes"]:
            for hk in sc.hooks:
                for h in bd.hooks:
                    if h.handler == hk["handler"] and h.line == hk["line"] and h.list == hk["list"]:
                        yield bd, sc, h
                        break


def _function_identity(sc, h) -> str:
    return FUNCTION_IDENTITY.get((sc.name, h.line), f"{sc.name}::{h.handler}")


def verdict_for(class_name: str, handler: str) -> dict[str, Any]:
    v = VERDICTS.get((class_name, handler))
    if v is None:
        raise FailClosed(f"no hand-read verdict for target hook {class_name}::{handler}")
    return v


def helper_verdict_for(class_name: str, handler: str) -> dict[str, Any]:
    v = HELPER_VERDICTS.get((class_name, handler))
    if v is None:
        raise FailClosed(f"no hand-read verdict for helper site {class_name}::{handler}")
    return v


def _helper_hit(h) -> set[str]:
    f = h.facts or {}
    names = {c["callee"] for c in f.get("calls", [])} | set(f.get("other_calls", {}))
    return (names & HELPER_CALLEES) | {n for n in names if n.startswith(HELPER_CALLEE_PREFIXES)}


@dataclass
class Inventory:
    tiers: Tiers
    adapters: list[dict[str, Any]]
    helpers: list[dict[str, Any]]
    outside: list[dict[str, Any]]
    not_loaded_helpers: list[dict[str, Any]] = field(default_factory=list)
    validate_erratum: list[dict[str, Any]] = field(default_factory=list)
    validate_unparsed: list[str] = field(default_factory=list)


def build_inventory(ctx) -> Inventory:
    from dummy_semantics.bindings import BindingMap
    b = ctx.bundle
    bm = BindingMap(b)
    tiers = compute_tiers(ctx, bm)
    adapters: list[dict[str, Any]] = []
    helpers: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    not_loaded_helpers: list[dict[str, Any]] = []
    for spell in sorted(bm.by_spell):
        tier = tiers.tier(spell)
        info = b.catalog.get(spell)
        for bd, sc, h in _registrations(ctx, bm, spell):
            if h.list in ADAPTER_LISTS:
                base = {"spell": spell, "name": b.name(spell), "script": bd.script_name, "class": sc.name,
                        "list": h.list, "handler": h.handler, "function": _function_identity(sc, h),
                        "eff_index": h.eff_index, "target_token": h.eff_name, "target": h.eff_value,
                        "mask": h.affected_mask, "effects": h.affected_effects, "executes": h.executes,
                        "registration": f"{sc.file}:{h.line}", "note": h.note}
                if tier is None:
                    outside.append({k: base[k] for k in ("spell", "name", "script", "list", "handler", "mask", "registration")})
                    continue
                v = verdict_for(sc.name, h.handler)
                method = sc.methods.get(h.handler) or {}
                row = dict(base)
                val = validate_ok(ctx, sc.name, (sc.validate or {}).get("spells", []), spell)
                mech = validate_cached(ctx, sc, spell)
                val["validate_ok_12_1"] = bool(val["validate_ok_12_1"] and mech["ok"])
                val["mechanical"] = {"failures": mech["failures"], "unparsed": mech["unparsed"]}
                gate = LOAD_GATES.get(sc.name)
                cond = REGISTER_CONDITIONS.get((sc.name, h.handler, h.eff_name))
                reg_state = cond(spell) if cond else "registered"
                row.update({"tier": tier, "verdict": v, "body": f"{sc.file}:{method.get('line')}",
                            "validate": val, "load_gate": list(gate) if gate else None,
                            "registration_state": reg_state,
                            "static_handler": bool(method.get("static")),
                            "effective_executes": bool(h.executes and val["validate_ok_12_1"] and reg_state != "not-registered"),
                            "build_skew_added": b.skew.is_newer_than_trinity(spell),
                            "controlled_unit": tier == "controlled-unit",
                            "script_parent": tiers.script_parent.get(spell)})
                if h.list in TARGET_HOOK_LISTS and isinstance(h.eff_value, int):
                    om = old_mask(spell, h.list, h.eff_index, h.eff_value)
                    row["mask_12_0_7"] = om
                    row["drift"] = ("new-in-12.1" if om is None else "unchanged" if om == h.affected_mask
                                    else "dead-in-12.1" if om and not h.affected_mask
                                    else "revived-in-12.1" if h.affected_mask and not om else "mask-changed")
                else:
                    row["mask_12_0_7"] = None
                    row["drift"] = "n/a"
                row["targets_12_1"] = {str(e.index): [e.target_a, e.target_b] for e in info.effects} if info else {}
                row["max_affected_targets"] = ctx.data.restrictions(spell).get("MaxTargets") if _has_restr(ctx, spell) else None
                adapters.append(row)
            elif tier is not None and h.executes:
                hit = _helper_hit(h)
                if not hit:
                    continue
                if not validate_cached(ctx, sc, spell)["ok"]:
                    not_loaded_helpers.append({"spell": spell, "class": sc.name, "handler": h.handler,
                                               "failures": validate_cached(ctx, sc, spell)["failures"]})
                    continue
                hv = helper_verdict_for(sc.name, h.handler)
                method = sc.methods.get(h.handler) or {}
                helpers.append({"spell": spell, "name": b.name(spell), "tier": tier, "script": bd.script_name,
                                "class": sc.name, "list": h.list, "handler": h.handler, "effects": h.affected_effects,
                                "registration": f"{sc.file}:{h.line}", "body": f"{sc.file}:{method.get('line')}",
                                "callees": sorted(hit), "verdict": hv})
    # Dummy erratum: executing hooks of in-scope bindings whose class fails Validate() on 12.1
    erratum = []
    for spell in sorted(bm.by_spell):
        if tiers.tier(spell) is None:
            continue
        for bd, sc, h in _registrations(ctx, bm, spell):
            if h.executes and not validate_cached(ctx, sc, spell)["ok"]:
                erratum.append({"spell": spell, "tier": tiers.tier(spell), "class": sc.name, "list": h.list,
                                "handler": h.handler, "failures": validate_cached(ctx, sc, spell)["failures"]})
    unparsed = sorted({f"{sc.name}: {u}" for spell in sorted(bm.by_spell) if tiers.tier(spell)
                       for bd, sc, h in _registrations(ctx, bm, spell) if h.executes
                       for u in validate_cached(ctx, sc, spell)["unparsed"]})
    return Inventory(tiers=tiers, adapters=adapters, helpers=helpers, outside=outside,
                     not_loaded_helpers=not_loaded_helpers, validate_erratum=erratum, validate_unparsed=unparsed)


def _has_restr(ctx, spell: int) -> bool:
    try:
        return bool(ctx.data.restrictions(spell))
    except FailClosed:
        return False


# ---------------------------------------------------------------------------
# runtime adapters (fixture)
# ---------------------------------------------------------------------------
def _explicit(world) -> str | None:
    return world.explicit.get("unit")


def suppress_object(world, target: str | None, gate: bool, trace: Trace) -> str | None:
    """``target = nullptr`` when ``gate`` holds (the caller evaluates the script's gate).

    Mirrors: script ``WorldObject*& target`` hooks; engine then skips AddUnitTarget and chain
    selection (Spell.cpp:1796-1805, 1816-1828).
    """
    out = None if gate else target
    trace.add("script.suppress_object", "Spell.cpp:1794/1814 + script", output=out, inputs={"gate": gate},
              evidence="script-consumer")
    return out


def clear_area(world, targets: list[str], gate: bool, trace: Trace) -> list[str]:
    out = [] if gate else list(targets)
    trace.add("script.clear", "Spell.cpp:1437 + script", output=out, inputs={"gate": gate}, evidence="script-consumer")
    return out


def remove_explicit(world, targets: list[str], trace: Trace) -> list[str]:
    """``targets.remove(GetExplTargetWorldObject())`` -- removes every equal element (list::remove)."""
    expl = _explicit(world)
    out = [t for t in targets if t != expl]
    trace.add("script.remove_explicit", "std::list::remove + script", output=out, inputs={"explicit": expl},
              evidence="script-consumer")
    return out


def explicit_only(world, targets: list[str], trace: Trace) -> list[str]:
    """Storm Bolt: ``clear(); if (explicit unit) push_back(explicit)`` (spell_warrior.cpp:1845)."""
    expl = _explicit(world)
    out = [expl] if expl is not None else []
    trace.add("script.explicit_only", "spell_warrior.cpp:1845-1851", output=out, evidence="script-consumer",
              notes=["pushed unit bypasses the area searcher checks (AddUnitTarget checkIfValid=false, Spell.cpp:1455)"])
    return out


def _is_unit(world, uid: str) -> bool:
    return world.actor(uid).kind not in ("gameobject", "dynamicobject", "areatrigger", "corpse")


def unit_aura_check(world, present: bool, spell: int, caster: str | None) -> Callable[[str], bool]:
    """Mirrors: GridNotifiers.h:2016-2019 ``UnitAuraCheck::operator()(WorldObject*)``.

    ``ToUnit() && HasAura(spell, caster) == present`` (caster ``None`` = ObjectGuid::Empty = any caster).
    """
    def pred(uid: str) -> bool:
        return _is_unit(world, uid) and world.actor(uid).has_aura(spell, caster) == present
    return pred


def keep_with_caster_aura(world, targets: list[str], aura: int, trace: Trace) -> list[str]:
    """``targets.remove_if(UnitAuraCheck(false, aura, caster))`` (Channel Demonfire / Shadowy Apparition).

    Non-units are kept (the predicate is false for them).
    """
    pred = unit_aura_check(world, False, aura, world.caster)
    out = [t for t in targets if not pred(t)]
    trace.add("script.keep_with_caster_aura", "GridNotifiers.h:2016 + std::list::remove_if", output=out,
              inputs={"aura": aura}, evidence="script-consumer")
    return out


def remove_with_any_aura(world, targets: list[str], auras: Iterable[int], trace: Trace) -> list[str]:
    """Primal Rage: remove non-units and units with any of ``auras`` from any caster (spell_generic.cpp:5212)."""
    auras = list(auras)
    out = [t for t in targets if _is_unit(world, t) and not any(world.actor(t).has_aura(a) for a in auras)]
    trace.add("script.remove_with_any_aura", "spell_generic.cpp:5212-5226", output=out, evidence="script-consumer")
    return out


def random_cap(world, targets: list[str], n: int, trace: Trace) -> list[str]:
    from . import rng
    return rng.random_resize(world, targets, n, trace, what="script RandomResize")


def seed_of_corruption_select(world, targets: list[str], seed_spell: int, trace: Trace) -> list[str]:
    """spell_warlock.cpp:1124-1144 ``spell_warl_seed_of_corruption_dummy::SelectTarget``."""
    from . import rng
    if len(targets) < 2:
        trace.add("script.seed.small", "spell_warlock.cpp:1126", output=list(targets), evidence="script-consumer")
        return list(targets)
    expl = _explicit(world)
    if expl is None:
        raise FailClosed("Seed of Corruption selector dereferences GetExplTargetUnit() unchecked (spell_warlock.cpp:1129)")
    if not world.actor(expl).has_aura(seed_spell, world.caster):
        trace.add("script.seed.explicit", "spell_warlock.cpp:1129-1134", output=[expl], evidence="script-consumer")
        return [expl]
    has_seed = unit_aura_check(world, True, seed_spell, world.caster)
    rest = [t for t in targets if not has_seed(t)]
    if rest:
        out = rng.random_resize(world, rest, 1, trace, what="seed RandomResize(1)")
    else:
        out = [expl]
    trace.add("script.seed.other", "spell_warlock.cpp:1136-1143", output=out, evidence="script-consumer")
    return out


def path_of_flames_select(world, targets: list[str], flame_shock: int, trace: Trace) -> list[str]:
    """spell_shaman.cpp:2386-2390: remove explicit; RandomResize(UnitAuraCheck(false, FS, caster), 1)."""
    from . import rng
    expl = _explicit(world)
    rest = [t for t in targets if t != expl]
    return rng.random_resize_pred(world, rest, unit_aura_check(world, False, flame_shock, world.caster), 1, trace,
                                  what="path of flames RandomResize(pred,1)")


def dest_offset(dest: tuple[float, float, float, float], offset: tuple[float, float, float], trace: Trace):
    """``Position::RelocateOffset`` for the pinned pure-z offset.

    Mirrors: Position.cpp:34-40 -- x/y add ``0.0f*cos - 0.0f*sin`` (no change for finite
    values), ``m_positionZ += offset.z`` in binary32, orientation re-normalised
    (``SetOrientation``).  Only offsets with x = y = 0 and a normalised orientation are
    modelled (spell_priest.cpp:5107 ``JumpOffset = {0, 0, 5}``); others fail closed.
    """
    import math
    import struct

    def f32(v: float) -> float:
        return struct.unpack("f", struct.pack("f", v))[0]
    x, y, z, o = dest
    if offset[0] != 0.0 or offset[1] != 0.0:
        raise FailClosed("dest_offset: only pure-z offsets are modelled (spell_priest.cpp:5107)")
    if not 0.0 <= o < f32(2 * math.pi):
        raise FailClosed("dest_offset: orientation must already be normalised (Position::NormalizeOrientation not modelled)")
    out = (x, y, f32(f32(z) + f32(offset[2])), o)
    trace.add("script.dest_offset", "spell_priest.cpp:5109-5112; Position.cpp:34-40", output=out,
              evidence="script-consumer")
    return out


def smart_injured(world, targets: list[str], n: int, prioritize_players: bool, group_of: str | None, trace: Trace):
    """Delegates to track D (``targeting.smart``); fails closed if D has not published it."""
    try:
        from . import smart  # type: ignore[attr-defined]
    except ImportError as exc:
        raise FailClosed("SelectRandomInjuredTargets ranking is owned by track D (targeting.smart) - not available") from exc
    fn = getattr(smart, "select_random_injured_targets", None)
    if fn is None:
        raise FailClosed("targeting.smart has no select_random_injured_targets")
    return fn(world, targets, n, prioritize_players, group_of, trace)


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------
COMPLEX_FAMILIES = {"smart-injured", "smart-priority-rules", "smart-sorted-cap", "random-cap",
                    "explicit-preserving-random", "explicit-removal+random"}


def _effect_class(rows: list[dict[str, Any]], skew: bool) -> dict[str, Any]:
    tags = {"script-adapter"}
    klass = "understood"
    for r in rows:
        v = r["verdict"]
        fam = v["family"]
        if not r["executes"]:
            tags.add("script-adapter-dead")
            continue
        if not r["effective_executes"]:
            tags.add("script-adapter-not-loaded")
            continue
        tags.add(fam)
        if fam.startswith("smart"):
            tags.add("smart")
        if v["rng"] != "none":
            tags.add("rng")
        if fam == "random-cap":
            tags.add("random-cap")
        if fam == "destination-offset":
            tags.add("dest")
        if r["tier"] == "controlled-unit":
            tags.add("controlled-unit")
        if v.get("defects"):
            klass = _worse(klass, "understood-with-defect")
        if v["gate"] not in ("none", "script-load") or v["runtime_inputs"] or v["rng"] != "none" \
                or v["input_order"] != "independent":
            klass = _worse(klass, "fixture-dependent")
    return {"class": klass, "tags": sorted(tags), "unknowns": [], "build_skew": skew}


_ORDER = ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")


def _worse(a: str, b: str) -> str:
    return max(a, b, key=_ORDER.index)


def corpus(ctx, command: str) -> dict[str, Any]:
    inv = build_inventory(ctx)
    b = ctx.bundle
    rows = sorted(inv.adapters, key=lambda r: (r["spell"], r["registration"], r["eff_index"] if isinstance(r["eff_index"], int) else -1))
    helpers = sorted(inv.helpers, key=lambda r: (r["spell"], r["registration"]))
    by_effect: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    dead_by_effect: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["executes"]:
            for e in r["effects"]:
                by_effect[(r["spell"], e)].append(r)
        elif isinstance(r["eff_index"], int) and str(r["eff_index"]) in r["targets_12_1"]:
            dead_by_effect[(r["spell"], r["eff_index"])].append(r)
    for (s, e), lst in dead_by_effect.items():
        by_effect.setdefault((s, e), []).extend(lst)
    effect_classes: dict[str, Any] = {}
    extended: dict[str, Any] = {}
    # helper sites choose recipients of OTHER casts (children); the owning effect gets a tagged row
    # (lead R1-06) so the census does not count it as simple.
    HELPER_TAGGED = {"script-target-selection", "dest-consumer", "controlled-unit-owner-cast", "controlled-unit-victim",
                     "proc-gate+rng", "dead-by-drift"}
    helper_effects: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for h in helpers:
        if h["verdict"]["kind"] not in HELPER_TAGGED:
            continue
        info = b.catalog.get(h["spell"])
        effs = h["effects"] or ([e.index for e in info.effects if e.effect] if info else [])
        for e in effs:
            helper_effects[(h["spell"], e)].append(h)
    for key in sorted(set(by_effect) | set(helper_effects)):
        s, e = key
        info = b.catalog.get(s)
        eff = info.effect(e) if info else None
        if eff is None or not eff.effect:
            continue
        ec = _effect_class(by_effect.get(key, []), b.skew.is_newer_than_trinity(s))
        for h in helper_effects.get(key, []):
            kind = h["verdict"]["kind"]
            tags = set(ec["tags"]) | {"script-adapter", "script-helper", kind}
            if h["verdict"].get("smart_family"):
                tags.add("smart")
            if h["verdict"].get("rng", "none") != "none":
                tags.add("rng")
            if kind in ("dead-by-drift", "proc-gate+rng"):
                tags.add("script-drift")
                ec["class"] = _worse(ec["class"], "understood-with-defect")
            else:
                ec["class"] = _worse(ec["class"], "fixture-dependent")
            ec["tags"] = sorted(tags)
        tier = inv.tiers.tier(s)
        (effect_classes if tier == "reach" else extended)[f"{s}:{e}"] = ec | ({"tier": tier} if tier != "reach" else {})

    executing = [r for r in rows if r["effective_executes"]]
    not_loaded = [r for r in rows if r["executes"] and not r["effective_executes"]]
    fam = Counter(r["verdict"]["family"] for r in executing)
    fam_fn = defaultdict(set)
    for r in executing:
        fam_fn[r["verdict"]["family"]].add(r["function"])
    def state(r: dict[str, Any]) -> str:
        if r["registration_state"] == "not-registered":
            return "not-registered"
        return "exec" if r["effective_executes"] else "validate-fails" if r["executes"] else "mask0"
    per_tier = Counter((r["tier"], state(r)) for r in rows)
    per_list = Counter((r["list"], state(r)) for r in rows)
    drift = Counter(r["drift"] for r in rows)
    specs = ctx.scope.specs_reach
    spec_names = ctx.scope.roots.spec_names
    per_spec = {}
    for spec, spells in sorted(specs.items()):
        ex = {r["spell"] for r in executing if r["spell"] in spells}
        per_spec[str(spec)] = {"name": spec_names.get(spec, ""), "spells_with_executing_adapter": len(ex),
                               "executing_registrations": sum(1 for r in executing if r["spell"] in spells),
                               "families": dict(sorted(Counter(r["verdict"]["family"] for r in executing
                                                               if r["spell"] in spells).items()))}
    smart = defaultdict(list)
    for r in executing:
        if r["verdict"]["smart_family"]:
            smart[r["verdict"]["smart_family"]].append(f"{r['spell']}:{','.join(map(str, r['effects']))} {r['function']}")
    for h in helpers:
        if h["verdict"]["smart_family"]:
            smart[h["verdict"]["smart_family"]].append(f"{h['spell']} {h['class']}::{h['handler']} (other cast)")
    unique_functions = {r["function"] for r in executing}
    reach_exec_spells = {r["spell"] for r in executing if r["tier"] == "reach"}
    totals = {
        "registrations_in_scope": len(rows),
        "registrations_executing": len(executing),
        "registrations_dead_mask0": sum(1 for r in rows if not r["executes"] and r["registration_state"] != "not-registered"),
        "registrations_not_registered": sum(1 for r in rows if r["registration_state"] == "not-registered"),
        "registrations_attached": sum(1 for r in rows if r["registration_state"] != "not-registered"),
        "registrations_caster_state_conditional": sum(1 for r in rows if r["registration_state"] == "caster-state"),
        "static_multi_effect_groups": sorted({f"{r['spell']} {r['function']}" for r in rows
                                              if r["effective_executes"] and r["static_handler"]
                                              and sum(1 for x in rows if x["spell"] == r["spell"] and x["function"] == r["function"]
                                                      and x["effective_executes"]) > 1}),
        "helper_sites_not_loaded": len(inv.not_loaded_helpers),
        "helper_scan": "pattern-based (HELPER_CALLEES / HELPER_CALLEE_PREFIXES over structural call facts): a lower bound",
        "dummy_erratum_executing_hooks_validate_fails": len(inv.validate_erratum),
        "dummy_erratum_classes_validate_fails": sorted({f"{e['spell']} {e['class']}" for e in inv.validate_erratum}),
        "validate_unparsed_constructs": len(inv.validate_unparsed),
        "script_reach_parents_skipped_validate": sum(len(v) for v in inv.tiers.not_loaded.values()),
        "spells_with_executing_adapter": len({r["spell"] for r in executing}),
        "reach_spells_with_executing_adapter": len(reach_exec_spells),
        "distinct_executing_functions": len(unique_functions),
        "by_tier_state": {f"{t}|{x}": n for (t, x), n in sorted(per_tier.items())},
        "by_list_state": {f"{t}|{x}": n for (t, x), n in sorted(per_list.items())},
        "drift_mask": dict(sorted(drift.items())),
        "registrations_not_loaded_validate_fails_12_1": len(not_loaded),
        "validate_drift": sorted({f"{r['spell']} {r['class']}: 12.0.7={r['validate']['validate_ok_12_0_7']} 12.1={r['validate']['validate_ok_12_1']}"
                                  for r in rows if r['validate']['validate_ok_12_0_7'] != r['validate']['validate_ok_12_1']}),
        "validate_fails_both_builds": sorted({f"{r['spell']} {r['class']}" for r in rows
                                              if not r['validate']['validate_ok_12_0_7'] and not r['validate']['validate_ok_12_1']}),
        "tiers": {"reach": len(inv.tiers.reach), "controlled_unit": len(inv.tiers.controlled_unit),
                  "script_reach": len(inv.tiers.script_reach)},
        "helper_sites": len(helpers),
        "helper_sites_by_kind": dict(sorted(Counter(h["verdict"]["kind"] for h in helpers).items())),
        "outside_scope_target_hook_registrations": len(inv.outside),
        "do_check_area_target_in_scope": sum(1 for r in rows if r["list"] == AURA_AREA_LIST),
        "effect_classes": len(effect_classes), "effect_classes_extended": len(extended),
    }
    prim = defaultdict(set)
    for r in executing:
        v = r["verdict"]
        key = (v["family"], v["gate"], v["predicate"], v["cap"], v["rng"], v["explicit_target"])
        prim[v["family"]].add(json_key(key))
    families = {f: {"registrations": n, "distinct_functions": len(fam_fn[f]),
                    "semantic_variants": len(prim[f])} for f, n in sorted(fam.items())}
    totals["semantic_variants_total"] = sum(len(v) for v in prim.values())
    modelled = {(r["script"], r["handler"]) for r in rows}
    totals["runner_modelled_registrations"] = sum(1 for r in executing if (r["script"], r["handler"]) in HOOK_ADAPTERS)
    totals["runner_unmodelled_functions"] = sorted({f"{r['script']}::{r['handler']}" for r in executing
                                                    if (r["script"], r["handler"]) not in HOOK_ADAPTERS})
    del modelled
    return {
        "provenance": {"pins": PINS, "command": command, "evidence": ["trinity-consumer", "script-consumer", "db2-fact"],
                       "scope": "ctx.scope.reach + controlled-unit (reach summons + pet families) + script-reach",
                       "drift_baseline": f"git {OLD_SNAPSHOT_COMMIT}:data/tables/SpellEffect.csv (12.0.7.68367)"},
        "engine": {"dispatch": HOOK_ENGINE_SITES,
                   "match_rule": "IsEffectAffected(m_spellInfo, first effect of the group) && selector.GetTarget() == hook.GetTarget() "
                                 "(Spell.cpp:8996/9009/9022); selector = TargetA or TargetB currently processed",
                   "check_effect": "SpellScript.cpp:203-254 (ported: dummy_semantics.bindings.target_hook_check_effect)",
                   "grouping": "Spell.cpp:9066-9097 CheckScriptEffectImplicitTargets: ObjectTarget + ObjectAreaTarget lists "
                               "only, function identity, both directions; target type not compared",
                   "cap_order": "script area hook runs before MaxAffectedTargets RandomResize / FURTHEST sort (Spell.cpp:1437-1451, 1305-1311, 1998-2010)",
                   "inserted_units": "area/cone lists: AddUnitTarget(checkIfValid=false) -> inserted units skip searcher + CheckTarget (Spell.cpp:1455, 1315)",
                   "dead_calls": "Spell.cpp:2035/2112 pass SpellImplicitTargetInfo() (target 0): TargetHook::CheckEffect returns false for _targetType 0 -> never fire"},
        "totals": totals,
        "families": families,
        "smart_families_for_track_d": {k: sorted(v) for k, v in sorted(smart.items())},
        "per_spec": per_spec,
        "adapters": rows,
        "helper_sites": helpers,
        "helper_sites_not_loaded": inv.not_loaded_helpers,
        "dummy_validate_erratum": inv.validate_erratum,
        "validate_unparsed": inv.validate_unparsed,
        "outside_scope": sorted(inv.outside, key=lambda r: (r["spell"], r["registration"])),
        "effect_classes": effect_classes,
        "effect_classes_extended": extended,
        "unknowns": UNKNOWNS,
        "retail_experiments": RETAIL_EXPERIMENTS,
        "trinity_defects": DEFECTS,
    }


def json_key(key: tuple) -> str:
    return "|".join("" if k is None else str(k) for k in key)


UNKNOWNS = [
    {"id": "TG-E-04", "subject": "effect-mask grouping across two registrations of one static target hook (435607 {1,2}, 184362 {0,1})",
     "evidence": "trinity-consumer", "known": "SpellScript.h:546-549 compares 16 uninitialised-padded bytes; static pointer is 8 bytes",
     "unknown": "whether the pinned binary groups these effects", "why_unresolved": "indeterminate value (compiler/stack dependent)",
     "reopen_condition": "a probe compiling the handler with the pinned toolchain", "build_skew": False},
    {"id": "TG-E-05", "subject": "Register()-time conditional hooks", "evidence": "structural-inference",
     "known": "Holy Prism FilterTargets per spell (not-registered rows labelled), Mass Entanglement AOE per spell, Halo selectors by "
              "caster talents (mask 0 either way), Primordial Wave by caster spec (helper verdict); lead list TG-H-03",
     "unknown": "the other conditional classes outside the target-hook inventory", "why_unresolved": "hand-read only where in scope",
     "reopen_condition": "Register() evaluation per spell/caster state", "build_skew": "n/a"},
    {"id": "TG-E-06", "subject": "Validate() constructs not evaluated mechanically", "evidence": "structural-inference",
     "known": "ValidateSpellInfo / ValidateSpellEffect evaluated; GetEffect predicates hand-read for 8 classes",
     "unknown": "other constructs (listed in `validate_unparsed`) are assumed to pass",
     "why_unresolved": "arbitrary C++", "reopen_condition": "hand-read or probe", "build_skew": "n/a"},
    {"id": "TG-E-01", "subject": "script-reach closure", "evidence": "structural-inference",
     "known": "children cast by executing hooks (cast-category calls with SpellID ints) are followed",
     "unknown": "casts through helper tables / constructor args / AreaTriggerAI / creature AI are not followed; "
                "448 target-hook registrations on bound spells lie outside every tier",
     "why_unresolved": "no authored edge and the structural index does not resolve indirect SpellIDs",
     "reopen_condition": "a script-cast edge corpus (AreaTriggerAI + helper tables) exists", "build_skew": "n/a"},
    {"id": "TG-E-02", "subject": "smart selection ranking (SelectRandomInjuredTargets / SortTargetsWithPriorityRules)",
     "evidence": "trinity-consumer", "known": "priority bits, unstable std::ranges::sort, cutoff/tie shuffle (Spell.cpp:9513-9608)",
     "unknown": "order among equal-priority elements before the shuffle (libstdc++ introsort) - track D oracle",
     "why_unresolved": "owned by track D", "reopen_condition": "targeting.smart publishes select_random_injured_targets",
     "build_skew": False},
    {"id": "TG-E-03", "subject": "Retail parity of script adapters", "evidence": "unresolved",
     "known": "Trinity behaviour per hook", "unknown": "whether Retail implements the same filters/caps (e.g. Wild Growth grouping preference)",
     "why_unresolved": "Trinity is a consumer oracle, not Retail truth", "reopen_condition": "retail experiments below",
     "build_skew": "n/a"},
]

RETAIL_EXPERIMENTS = [
    {"id": "RX-E-04", "question": "Does Intimidating Shout's area fear exclude the primary target and does the area root (aura 191) apply?",
     "model_a": "Trinity 12.1: hooks dead -> primary in area list, area root on all", "model_b": "script intent: primary excluded, no area root",
     "setup": "shout at a target with 2 more enemies within 8 yd", "observable": "SPELL_AURA_APPLIED 5246 per unit / aura", "fidelity": "exact"},
    {"id": "RX-E-05", "question": "Who receives Blade Flurry's cleave (22482) and is it random among nearby enemies?",
     "model_a": "Trinity 12.1: nobody (HandleProc dead)", "model_b": "random nearby unfriendly unit per hit (script intent)",
     "setup": "Blade Flurry active, 3 enemies in melee", "observable": "SPELL_DAMAGE 22482 destinations", "fidelity": "exact"},
    {"id": "RX-E-06", "question": "Which units does Killing Spree strike?",
     "model_a": "Trinity 12.1: none (hit list empty)", "model_b": "random among enemies hit at cast",
     "setup": "cast among 3 enemies", "observable": "SPELL_DAMAGE / teleport events of the child spells", "fidelity": "exact"},
    {"id": "RX-E-01", "question": "Does Wild Growth prefer injured group members over injured non-grouped players?",
     "model_a": "Trinity SelectRandomInjuredTargets(prioritizeGroupMembersOf=caster)", "model_b": "no group preference",
     "setup": "caster in a party of 2 injured members plus 6 injured non-grouped players in range, cap 6",
     "observable": "combat log SPELL_HEAL recipients of 48438", "fidelity": "exact"},
    {"id": "RX-E-02", "question": "Does Seed of Corruption's detonation re-target to a random unseeded unit when the explicit target is seeded?",
     "model_a": "Trinity spell_warl_seed_of_corruption_dummy::SelectTarget", "model_b": "always explicit target",
     "setup": "cast Seed on a seeded target with 3 unseeded enemies nearby", "observable": "SPELL_AURA_APPLIED 27243 destination",
     "fidelity": "exact"},
    {"id": "RX-E-03", "question": "Does Power Word: Radiance always include the explicit target and prefer targets without Atonement?",
     "model_a": "Trinity GetRadianceRules order", "model_b": "random among injured",
     "setup": "explicit target + 6 allies, 3 with Atonement", "observable": "Atonement applications", "fidelity": "exact"},
]

DEFECTS = [
    {"id": "TD-E-20", "file_line": "src/server/scripts/Spells/spell_warrior.cpp:1341-1342",
     "description": "script drift: Intimidating Shout hooks target EFFECT_2/EFFECT_3 TARGET_UNIT_SRC_AREA_ENEMY, but on 12.0.7 and "
                    "12.1 the area group is effects 4-6 (TARGET_SRC_CASTER + SRC_AREA_ENEMY); both hooks have mask 0",
     "effect_on_recipients": "the explicit target stays in the 4-6 area list (gets fear/aura 33/aura 191 from both groups, merged "
                             "in the unique list) and the aura-191 area group the script meant to clear hits every enemy",
     "oracle_behaviour": "reproduce (hooks never run); mark defect"},
    {"id": "TD-E-21", "file_line": "src/server/scripts/Spells/spell_shaman.cpp:2343",
     "description": "script drift: Molten Thunder hook registered for Sundering EFFECT_3, which 197214 does not have (3 effects)",
     "effect_on_recipients": "none on current rows (no incapacitate effect to clear)", "oracle_behaviour": "hook never runs"},
    {"id": "TD-E-22", "file_line": "src/server/scripts/Spells/spell_rogue.cpp:283-301",
     "description": "script drift: Blade Flurry HandleProc registered for EFFECT_0 aura MOD_POWER_REGEN_PERCENT / MOD_MELEE_HASTE; "
                    "13877:0 is SPELL_AURA_DUMMY -> mask 0; CheckProc still calls SelectNearbyTarget",
     "effect_on_recipients": "no cleave (22482) recipient ever; the proc is gated on 'an unfriendly unit exists nearby' and "
                             "consumes one urand draw when it does",
     "oracle_behaviour": "proc gate + draw, no recipient; mark defect"},
    {"id": "TD-E-23", "file_line": "src/server/scripts/Spells/spell_rogue.cpp:704-719",
     "description": "script drift: Killing Spree FilterTargets/HandleDummy registered for EFFECT_1 DEST_AREA_ENEMY / DUMMY; 51690:1 is "
                    "APPLY_AURA TARGET_UNIT_CASTER on 12.0.7 and 12.1 -> both mask 0; the aura's target list stays empty",
     "effect_on_recipients": "periodic ticks cast nothing (no teleport, no weapon damage, no draw)",
     "oracle_behaviour": "empty recipient set; mark defect"},
    {"id": "TD-E-24", "file_line": "src/server/game/Spells/SpellScript.h:149-178,522-549,584-587",
     "description": "HasSameTargetFunctionAs compares 16 bytes of ImplStorage that is never value-initialised; static handlers write "
                    "only 8 bytes -> equality of two registrations of the same static function is indeterminate",
     "effect_on_recipients": "effect-mask grouping of 435607 {1,2} and 184362 {0,1} is compiler/stack dependent; recipients "
                             "unaffected because both registrations null the object",
     "oracle_behaviour": "script_allows_grouping fails closed for that comparison"},
    {"id": "TD-E-01", "file_line": "src/server/game/Spells/Spell.cpp:9599",
     "description": "SortTargetsWithPriorityRules indexes prioritizedTargets[maxTargets - 1] - UB for maxTargets == 0",
     "effect_on_recipients": "none for pinned current-player callers (Radiance passes value+1, Penitence passes 1)",
     "oracle_behaviour": "fail closed on maxTargets 0"},
    {"id": "TD-E-02", "file_line": "src/server/scripts/Spells/spell_shaman.cpp:2262",
     "description": "flameShocksMissing is size_t computed as value + 1 - affectedCount: wraps when more targets already have "
                    "Flame Shock than value + 1 (still non-zero -> shuffle runs)",
     "effect_on_recipients": "extra shuffle draws; recipients are still the first value+1 (affected first)",
     "oracle_behaviour": "reproduce: shuffle whenever the unsigned result is non-zero"},
    {"id": "TD-E-03", "file_line": "src/server/scripts/Spells/spell_warlock.cpp:1129",
     "description": "GetExplTargetUnit() dereferenced without null check in Seed of Corruption selector",
     "effect_on_recipients": "crash if the detonation is cast without a unit target and >= 2 candidates",
     "oracle_behaviour": "fail closed"},
]


# ---------------------------------------------------------------------------
# hook runner (track G pipeline entry point)
# ---------------------------------------------------------------------------
# Aura / spell constants resolved from the structural index (dummy-corpora/script-index.json
# `constants`) for the pinned script files.
A_INNER_PEACE, A_CURIOUS_BRAMBLEPATCH, A_EVERWARM_SOCKS, A_IGNITION_BURST = 197073, 330670, 320913, 1217359
A_SMOTHERING_OFFENSE, A_DEFLECTING_SPIKES, A_VENGEFUL_BONDS, A_SCOURING_FLAME = 435005, 321028, 320635, 378438
A_WITHER_TALENT, A_WITHER_PERIODIC, A_IMMOLATE_PERIODIC, A_VAMPIRIC_TOUCH = 445465, 445474, 157736, 34914
A_TRANSLUCENT_IMAGE, A_AUSPICIOUS_SPIRITS, A_FLAME_SHOCK, A_MOLTEN_THUNDER = 373446, 155271, 188389, 469344
A_PRESSURE_POINTS, A_BLADE_OF_VENGEANCE, A_FRENZIED_ENRAGE, A_POWERFUL_ENRAGE, A_STORM_BOLTS = 450432, 403826, 383848, 440277, 436162
BLOODLUST_EXCLUSIONS = (57724, 57723, 80354, 264689, 390435)   # spell_generic.cpp:5220-5224


def _caster_has(world, aura: int) -> bool:
    return world.actor(world.caster).has_aura(aura)


def _obj(fn_gate):
    """Object-suppression adapter: ``gate(world) -> bool`` (True = target := nullptr)."""
    def run(world, sv, eff, target, value, trace):
        return suppress_object(world, value, fn_gate(world), trace)
    return run


def _area(fn):
    def run(world, sv, eff, target, value, trace):
        return fn(world, list(value), trace)
    return run


def _smart_injured(n_fn, group_fn):
    def run(world, sv, eff, target, value, trace):
        from . import smart
        return smart.select_random_injured_targets(world, list(value), n_fn(world), True, group_fn(world), trace)
    return run


def _spell_value(world, key: str) -> Any:
    if key not in world.spell_value:
        raise FailClosed(f"fixture: spell_value.{key} not stated")
    return world.spell_value[key]


def _smart(name: str):
    def run(world, sv, eff, target, value, trace):
        from . import smart
        return getattr(smart, name)(world, list(value), trace)
    return run


#: (script name, handler) -> (load_gate(world) -> bool | None, adapter).  ``load_gate`` returning
#: False means Load() rejected the script (the hook never runs).  Validate() failures on the
#: 12.1 rows are listed in NOT_LOADED_12_1 and skipped.
HOOK_ADAPTERS: dict[tuple[str, str], tuple[Callable[[Any], bool] | None, Callable[..., Any]]] = {
    ("spell_pri_translucent_image", "PreventEffect"): (lambda w: not _caster_has(w, A_TRANSLUCENT_IMAGE), _obj(lambda w: True)),
    ("spell_dru_inner_peace", "PreventEffect"): (None, _obj(lambda w: not _caster_has(w, A_INNER_PEACE))),
    ("spell_warl_seed_of_corruption_dummy", "RemoveVisualMissile"): (None, _obj(lambda w: True)),
    ("spell_warl_seed_of_corruption_dummy", "SelectTarget"): (None, _smart("seed_of_corruption_select")),
    ("spell_mage_ice_block", "PreventStunWithEverwarmSocks"): (None, _obj(lambda w: _caster_has(w, A_EVERWARM_SOCKS))),
    ("spell_mage_ice_block", "PreventEverwarmSocks"): (None, _obj(lambda w: not _caster_has(w, A_EVERWARM_SOCKS))),
    ("spell_dru_entangling_roots", "HandleCuriousBramblepatch"): (None, _obj(lambda w: not _caster_has(w, A_CURIOUS_BRAMBLEPATCH))),
    ("spell_dru_entangling_roots", "HandleCuriousBramblepatchAOE"): (
        None, lambda w, sv, e, t, v, tr: clear_area(w, list(v), not _caster_has(w, A_CURIOUS_BRAMBLEPATCH), tr)),
    ("spell_monk_pressure_points", "PreventDispel"): (lambda w: not _caster_has(w, A_PRESSURE_POINTS), _obj(lambda w: True)),
    ("spell_pri_halo_effect_selector", "PreventUnwantedAura"): (None, _obj(lambda w: True)),
    ("spell_pri_auspicious_spirits", "PreventTarget"): (lambda w: not _caster_has(w, A_AUSPICIOUS_SPIRITS), _obj(lambda w: True)),
    ("spell_warr_frenzied_enrage", "HandleFrenziedEnrage"): (lambda w: not _caster_has(w, A_FRENZIED_ENRAGE), _obj(lambda w: True)),
    ("spell_warr_powerful_enrage", "HandlePowerfulEnrage"): (lambda w: not _caster_has(w, A_POWERFUL_ENRAGE), _obj(lambda w: True)),
    ("spell_pal_blade_of_vengeance", "PreventProc"): (lambda w: not _caster_has(w, A_BLADE_OF_VENGEANCE), _obj(lambda w: True)),
    ("spell_dk_icy_talons_buff", "HandleSmotheringOffense"): (None, _obj(lambda w: not _caster_has(w, A_SMOTHERING_OFFENSE))),
    ("spell_dh_deflecting_spikes", "HandleParryChance"): (None, _obj(lambda w: not _caster_has(w, A_DEFLECTING_SPIKES))),
    ("spell_warr_thunder_blast", "PreventDefaultTargetObject"): (None, _obj(lambda w: True)),
    ("spell_mage_ignition_burst", "PreventAura"): (lambda w: not _caster_has(w, A_IGNITION_BURST), _obj(lambda w: True)),
    ("spell_warr_intimidating_shout", "FilterTargets"): (None, _area(remove_explicit)),
    ("spell_warr_intimidating_shout", "ClearTargets"): (None, lambda w, sv, e, t, v, tr: clear_area(w, list(v), True, tr)),
    ("spell_warr_storm_bolts", "FilterTargets"): (lambda w: not _caster_has(w, A_STORM_BOLTS), _area(explicit_only)),
    ("spell_sha_molten_thunder_sundering", "RemoveIncapacitateEffect"): (
        lambda w: _caster_has(w, A_MOLTEN_THUNDER), lambda w, sv, e, t, v, tr: clear_area(w, list(v), True, tr)),
    ("spell_dh_vengeful_retreat_damage", "HandleVengefulBonds"): (
        None, lambda w, sv, e, t, v, tr: clear_area(w, list(v), not _caster_has(w, A_VENGEFUL_BONDS), tr)),
    ("spell_evo_fire_breath_damage", "RemoveUnusedEffect"): (None, lambda w, sv, e, t, v, tr: clear_area(w, list(v), True, tr)),
    ("spell_evo_scouring_flame", "HandleScouringFlame"): (
        None, lambda w, sv, e, t, v, tr: clear_area(w, list(v), not _caster_has(w, A_SCOURING_FLAME), tr)),
    ("spell_pal_blade_of_vengeance_aoe_target_selector", "RemoveExplicitTarget"): (None, _area(remove_explicit)),
    ("spell_rog_airborne_irritant_target_selection", "FilterTargets"): (None, _area(remove_explicit)),
    ("spell_warl_channel_demonfire_selector", "FilterTargets"): (
        None, lambda w, sv, e, t, v, tr: keep_with_caster_aura(
            w, list(v), A_WITHER_PERIODIC if _caster_has(w, A_WITHER_TALENT) else A_IMMOLATE_PERIODIC, tr)),
    ("spell_pri_shadowy_apparition_dummy", "FilterTargets"): (
        None, lambda w, sv, e, t, v, tr: keep_with_caster_aura(w, list(v), A_VAMPIRIC_TOUCH, tr)),
    ("spell_hun_primal_rage", "FilterTargets"): (
        None, lambda w, sv, e, t, v, tr: remove_with_any_aura(w, list(v), BLOODLUST_EXCLUSIONS, tr)),
    ("spell_sha_path_of_flames_spread", "FilterTargets"): (
        None, lambda w, sv, e, t, v, tr: path_of_flames_select(w, list(v), A_FLAME_SHOCK, tr)),
    ("spell_dru_starfall_dummy", "FilterTargets"): (None, _smart("starfall_dummy")),
    ("spell_dru_wild_growth", "FilterTargets"): (None, _smart("wild_growth")),
    ("spell_pri_power_word_radiance", "FilterTargets"): (None, _smart("power_word_radiance")),
    ("spell_dru_efflorescence_heal", "FilterTargets"): (None, _smart_injured(lambda w: 3, lambda w: w.caster)),
    ("spell_sha_ancestral_guidance_heal", "ResizeTargets"): (None, _smart_injured(lambda w: 3, lambda w: None)),
    ("spell_dru_yseras_gift_group_heal", "SelectTargets"): (None, _smart_injured(lambda w: 1, lambda w: None)),
    ("spell_pri_prayer_of_mending_jump", "FilterTargets"): (None, _smart_injured(lambda w: 1, lambda w: None)),
    ("spell_monk_burst_of_life_heal", "FilterTargets"): (
        None, _smart_injured(lambda w: int(_spell_value(w, "max_affected_targets")), lambda w: w.explicit.get("unit"))),
    ("spell_dh_blade_dance", "DecideFirstTarget"): (None, lambda w, sv, e, t, v, tr: list(v)),   # state capture only
}


def _script_state(world, key: str) -> Any:
    state = world.raw.get("script_state") or {}
    if key not in state:
        raise FailClosed(f"fixture: script_state.{key} not stated")
    return state[key]


def _enduring_torment(world, sv, eff, target, value, trace):
    """spell_dh.cpp:1128-1132 -- PreventEffect<Havoc(577)> on effects 0-1, <Devourer(1480)> on 2-3 (DBCEnums.h:434/436)."""
    spec = world.actor(world.caster).fact("primary_specialization")
    wanted = 577 if eff.index in (0, 1) else 1480
    return suppress_object(world, value, spec != wanted, trace)


def _unfurling_darkness(world, sv, eff, target, value, trace):
    """spell_priest.cpp:5130-5144 -- keep only if not triggered with an original cast id and the cast consumed
    Unfurling Darkness (m_appliedMods)."""
    triggered = bool(_script_state(world, "has_original_cast_id"))
    consumed = bool(_script_state(world, "applied_mod_unfurling_darkness"))
    return suppress_object(world, value, triggered or not consumed, trace)


def _reaper_of_souls(world, sv, eff, target, value, trace):
    """spell_dk.cpp:1225-1236 -- suppressed when the cast consumed the Reaper of Souls proc (m_appliedMods)."""
    return suppress_object(world, value, bool(_script_state(world, "applied_mod_reaper_of_souls")), trace)


HOOK_ADAPTERS[("spell_dh_enduring_torment_buff", "PreventEffect")] = (lambda w: w.actor(w.caster).kind == "player", _enduring_torment)
HOOK_ADAPTERS[("spell_pri_unfurling_darkness", "PreventDirectDamage")] = (None, _unfurling_darkness)
HOOK_ADAPTERS[("spell_dk_reaper_of_souls", "HandleDefault")] = (None, _reaper_of_souls)
#: Not modelled (fail closed): spell_dru_germination (casts another spell), spell_pal_holy_prism_selector
#: (script field sharing), spell_pri_purge_the_wicked_dummy (duration/distance sort),
#: spell_pri_dispersing_light_heal (m_customArg), spell_mage_ring_of_frost_freeze (annulus of another spell),
#: spell_rog_killing_spree (FinishCast), spell_pri_ultimate_penitence_jump (SpellDestination type).

#: scripts whose Validate() fails on the 12.1 rows (never attached): hooks are skipped.
NOT_LOADED_12_1 = {"spell_dru_inner_peace", "spell_warr_powerful_enrage", "spell_evo_fire_breath_damage"}
#: script names whose registration class differs from the script name (spell_script_names -> class)
_SCRIPT_CLASS = {"spell_hun_primal_rage": "spell_gen_bloodlust"}
COMMUTATIVE = {"object-suppression", "area-clear", "explicit-removal", "aura-presence-filter", "guid-exclusion", "state-capture"}


def run_target_hook(world, sv, eff, target: int, lst: str, hooks: list[dict[str, Any]], value: Any, trace: Trace) -> Any:
    """Run the executing script target hooks for (group lead effect, selector).

    Mirrors: Spell.cpp:8991-9030 (every matching hook of every loaded script, in load order).
    The pinned load order (``m_loadedScripts`` from spell_script_names, then Register() order)
    is not carried by the hook dicts, so more than one non-commutative hook fails closed.
    Load() gates read caster auras from the fixture; Validate() failures on 12.1 rows skip the
    hook.  Unmodelled hooks fail closed.
    """
    runnable = []
    for h in hooks:
        key = (h["script"], h["handler"])
        if h["script"] in NOT_LOADED_12_1:
            trace.add("script.not-loaded", "SpellScript.cpp:26-34 (Validate)", output=value, evidence="script-consumer",
                      inputs={"hook": f"{key[0]}::{key[1]}"}, notes=["Validate() fails on the 12.1 rows"])
            continue
        entry = HOOK_ADAPTERS.get(key)
        if entry is None:
            raise FailClosed(f"script target hook {key[0]}::{key[1]} has no modelled adapter")
        gate, fn = entry
        if gate is not None and not gate(world):
            trace.add("script.load-rejected", "SpellScript Load()", output=value, evidence="script-consumer",
                      inputs={"hook": f"{key[0]}::{key[1]}"})
            continue
        cls = _SCRIPT_CLASS.get(h["script"], h["script"])
        fam = VERDICTS.get((cls, h["handler"]), {}).get("family")
        runnable.append((key, fn, fam))
    if len({k for k, _, _ in runnable}) > 1 and any(f not in COMMUTATIVE for _, _, f in runnable):
        raise FailClosed(f"several non-commutative target hooks {[k for k, _, _ in runnable]}: script load order not modelled")
    for key, fn, _ in runnable:
        value = fn(world, sv, eff, target, value, trace)
    return value
