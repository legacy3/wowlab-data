"""Track I -- generation safety of mutable aura state.

Question: when an aura is removed, refreshed or re-created, can anything still reach the *old*
application (its periodic deadline, expiry, stacks, charges, or a child event it spawned) and act
on it?  Two kinds of answer:

* **observable semantics** -- what a player sees (a new generation starts fresh; an old deadline
  never fires into the new generation; a child spell in flight keeps only value copies);
* **Trinity implementation technique** -- how the engine gets there (immediate erasure from the
  owner's containers, deferred ``delete`` at the end of ``_UpdateSpells``, ``IsRemoved()`` guards,
  ``ScheduleAbort`` of the charge-drop event, value capture in ``Spell::prepare``).

:data:`HOLDERS` classifies every class member of pinned Trinity that stores an ``Aura*`` /
``AuraEffect*`` / ``AuraApplication*`` beyond one call; :func:`scan_holders` re-derives that member
list from the headers so a new holder cannot be missed silently (``unclassified`` must be empty).
:data:`FACTS` are the generation rules with their timelines (``ordering.SCENARIOS``) and coordinates.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import FailClosed

_MEMBER = re.compile(
    r"^\s+(?:std::\w+<\s*)?(?P<type>Aura|UnitAura|AuraEffect|AuraApplication)(?:\s+const)?\s*\*\s*(?:const\s*)?>?\s*"
    r"(?P<name>\w+)\s*;")
_CONTAINER = re.compile(
    r"^\s+(?:std::)?\w+<[^;]*\b(?P<type>Aura|UnitAura|AuraEffect|AuraApplication)(?:\s+const)?\s*\*[^;]*>\s+(?P<name>\w+)\s*;")

# key "<header basename>:<member>" -> classification
HOLDERS: dict[str, dict[str, Any]] = {
    "SpellAuraEffects.h:m_base": {
        "holder": "AuraEffect -> owning Aura", "lifetime": "owned by the Aura (deleted in ~Aura, SpellAuras.cpp:544-545)",
        "invalidation": "same object lifetime", "risk": "none", "evidence": "trinity-consumer"},
    "SpellAuras.h:_base": {
        "holder": "AuraApplication -> Aura", "lifetime": "application deleted by Aura::_DeleteRemovedApplications "
        "(end of UpdateOwner, SpellAuras.cpp:852) or ~Aura (548)", "invalidation": "application unlinked in "
        "_UnapplyAura before the Aura can be deleted", "risk": "none", "evidence": "trinity-consumer"},
    "SpellAuras.h:_removedApplications": {
        "holder": "Aura -> removed applications awaiting delete", "lifetime": "until the end of the owning Aura's next UpdateOwner",
        "invalidation": "deferred delete (implementation technique: lets callers finish with the application)",
        "risk": "none", "evidence": "trinity-consumer"},
    "Player.h:ownerAura": {
        "holder": "SpellModifier -> Aura", "lifetime": "spell modifier removed by the effect's remove handler "
        "(AuraEffect::HandleModSpellModifier path) before the Aura is deleted", "invalidation": "effect unapply",
        "risk": "none observed", "evidence": "structural-inference"},
    "AreaTrigger.h:_aurEff": {
        "holder": "AreaTrigger -> creating AuraEffect", "lifetime": "set at creation (AreaTrigger.cpp:119); area trigger "
        "removed by AuraEffect::HandleCreateAreaTrigger(remove) only `if (Unit* caster = GetCaster())` "
        "(SpellAuraEffects.cpp:6400-6401)", "invalidation": "none when the caster is not reachable at removal: "
        "the area trigger keeps a dangling AuraEffect* until its own duration ends; Unit::RemoveAreaTrigger compares "
        "by pointer identity (Unit.cpp:5503), so a later AuraEffect allocated at the same address can match (ABA)",
        "risk": "stale-pointer (candidate Trinity defect AL-D-I-02)", "evidence": "trinity-consumer"},
    "DynamicObject.h:_aura": {
        "holder": "DynamicObject -> its DynObjAura", "lifetime": "DynamicObject::RemoveAura moves it to _removedAura and "
        "calls _Remove (DynamicObject.cpp:198-205)", "invalidation": "explicit; Update checks IsRemoved/IsExpired "
        "(DynamicObject.cpp:142-148)", "risk": "none", "evidence": "trinity-consumer"},
    "DynamicObject.h:_removedAura": {
        "holder": "DynamicObject -> removed DynObjAura awaiting delete", "lifetime": "deleted in ~DynamicObject (DynamicObject.cpp:51)",
        "invalidation": "deferred delete", "risk": "none", "evidence": "trinity-consumer"},
    "Unit.h:_aura": {
        "holder": "DispelableAura -> Aura (dispel/steal candidate list)", "lifetime": "stack-local list inside one "
        "dispel/steal resolution (GetDispellableAuraList, Unit.cpp:4735)", "invalidation": "not needed (transient)",
        "risk": "none", "evidence": "trinity-consumer"},
    "Unit.h:auraEff": {
        "holder": "SpellPeriodicAuraLogInfo -> AuraEffect", "lifetime": "one periodic tick's combat-log packet",
        "invalidation": "not needed (transient)", "risk": "none", "evidence": "trinity-consumer"},
    "Spell.h:_spellAura": {
        "holder": "Spell -> aura of the hit being processed", "lifetime": "set around HandleEffects / after-hit hooks "
        "(Spell.cpp:3309-3311, 3057-3059)", "invalidation": "EffectApplyAura guards `_spellAura->IsRemoved()` "
        "(SpellEffects.cpp:1115); SpellScript::GetHitAura hides removed auras unless asked (SpellScript.cpp:675)",
        "risk": "none (guarded)", "evidence": "trinity-consumer"},
    "SpellScript.h:m_aura": {
        "holder": "AuraScript -> its Aura", "lifetime": "scripts deleted in ~Aura (SpellAuras.cpp:538-542)",
        "invalidation": "same object lifetime", "risk": "none", "evidence": "trinity-consumer"},
    "SpellScript.h:m_auraApplication": {
        "holder": "AuraScript -> application of the running hook", "lifetime": "set/restored around one hook call "
        "(ScriptStateStore)", "invalidation": "not needed (transient)", "risk": "none", "evidence": "structural-inference"},
    "SpellScript.h:_auraApplication": {
        "holder": "AuraScript::ScriptStateStore -> saved application", "lifetime": "one nested hook call",
        "invalidation": "not needed (transient)", "risk": "none", "evidence": "structural-inference"},
}

# Holders that are not class members but matter (found by reading, not by the member scan).
NON_MEMBER_HOLDERS: tuple[dict[str, Any], ...] = (
    {"holder": "ChargeDropEvent::_base (Aura*) in the owner's EventProcessor", "coords": "SpellAuras.cpp:46-60, 1046-1054",
     "invalidation": "Aura::_Remove calls m_dropEvent->ScheduleAbort() (SpellAuras.cpp:648-652); an aborted event is "
                     "deleted without Execute (EventProcessor.cpp:63-74); BasicEvent::Abort default does not touch _base",
     "risk": "none", "evidence": "trinity-consumer"},
    {"holder": "Spell::TargetInfo::HitAura (UnitAura*) across the effects of one hit", "coords": "Spell.cpp:3238-3305, 3029-3046",
     "invalidation": "application lookups via GetApplicationOfTarget return null once removed; DoSpellEffectHit's "
                     "`else hitInfo.HitAura->AddStaticApplication` (Spell.cpp:3305) has no IsRemoved guard but only "
                     "records a static target mask that a removed aura never consumes (UpdateTargetMap returns early)",
     "risk": "none observed", "evidence": "trinity-consumer"},
    {"holder": "CastSpellExtraArgs::TriggeringAura (AuraEffect const*) -> Spell::prepare", "coords": "SpellDefines.h:498, Object.cpp:2250-2260, Spell.cpp:3459-3463",
     "invalidation": "not retained: prepare copies m_triggeredByAuraSpell (SpellInfo*, static data) and "
                     "m_castItemLevel; the cast item is resolved once by GUID. A triggered spell in flight never "
                     "dereferences its source aura again",
     "risk": "none", "evidence": "trinity-consumer"},
    {"holder": "Unit::m_auraUpdateIterator (AuraMap::iterator) during _UpdateSpells", "coords": "Unit.cpp:2975-2980, 3755-3757",
     "invalidation": "RemoveOwnedAura advances it when it points at the removed aura; insertions do not move it "
                     "(new auras are visited in the same pass only when inserted after it: OR-I-11)",
     "risk": "none (ordering consequence only)", "evidence": ["trinity-consumer", "trinity-probe"]},
)


def _headers(tc_root: Path) -> list[Path]:
    return sorted((tc_root / "src" / "server" / "game").rglob("*.h"))


def scan_holders(tc_root: Path) -> dict[str, Any]:
    """Every class member of type ``Aura*`` / ``AuraEffect*`` / ``AuraApplication*`` (or a container of them)."""
    if not (tc_root / "src" / "server" / "game").is_dir():
        raise FailClosed(f"no TrinityCore checkout at {tc_root}")
    found = []
    for path in _headers(tc_root):
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "(" in line or "typedef" in line or "using " in line or "static " in line:
                continue
            m = _MEMBER.match(line) or _CONTAINER.match(line)
            if m:
                found.append({"key": f"{path.name}:{m['name']}", "type": m["type"], "coords": f"{path.name}:{n}",
                              "line": line.strip()})
    classified = [dict(f, **HOLDERS[f["key"]]) for f in found if f["key"] in HOLDERS]
    unclassified = [f for f in found if f["key"] not in HOLDERS]
    stale = sorted(set(HOLDERS) - {f["key"] for f in found})
    return {"members": sorted(classified, key=lambda r: r["key"]), "unclassified": unclassified,
            "classified_but_not_found": stale, "non_member_holders": list(NON_MEMBER_HOLDERS)}


def count_is_removed(tc_root: Path) -> dict[str, int]:
    """``IsRemoved()`` call/definition sites per file (game + scripts), as a guard-density indicator."""
    out: dict[str, int] = {}
    for sub in ("game", "scripts"):
        for path in sorted((tc_root / "src" / "server" / sub).rglob("*")):
            if path.suffix not in (".cpp", ".h"):
                continue
            n = path.read_text(encoding="utf-8", errors="replace").count("IsRemoved()")
            if n:
                out[str(path.relative_to(tc_root / "src" / "server"))] = n
    return dict(sorted(out.items()))


FACTS: tuple[dict[str, Any], ...] = (
    {"id": "GN-I-01", "name": "removal-then-reapply creates a new generation",
     "semantics": "a reapplication after removal (expiry, dispel, death) starts from nothing: new stacks, charges, "
                  "timers and a fresh duration; nothing of the old application's timing carries over",
     "technique": "RemoveOwnedAura erases from m_ownedAuras at once (Unit.cpp:3758), so GetOwnedAura cannot find the "
                  "removed object and TryRefreshStackOrCreate falls through to Aura::Create (SpellAuras.cpp:386-387)",
     "scenarios": ["reapply-after-expiry-new-generation", "remove-then-refresh-same-tick"],
     "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
    {"id": "GN-I-02", "name": "refresh keeps the same object",
     "semantics": "a successful refresh/stack of the found aura mutates it in place (no remove/apply of the aura)",
     "technique": "_TryStackingOrRefreshingExistingAura -> ModStackAmount on the found object (Unit.cpp:3441)",
     "scenarios": ["refresh-before-due-tick", "pandemic-refresh-reads-refreshed-duration"],
     "evidence": ["trinity-consumer", "trinity-probe"]},
    {"id": "GN-I-03", "name": "old deadlines cannot fire into a new generation",
     "semantics": "periodic phase, tick count and expiry are per object; a new generation's first tick is measured from "
                  "its own creation (or immediately with ATTR5_EXTRA_INITIAL_PERIOD)",
     "technique": "timers live in AuraEffect members; no scheduler entry outlives the object (ticks/expiry are polled "
                  "in _UpdateSpells, not queued)",
     "scenarios": ["reapply-after-expiry-new-generation"], "evidence": ["trinity-consumer", "trinity-probe"]},
    {"id": "GN-I-04", "name": "removed-but-allocated window",
     "semantics": "none observable: a removed aura receives no ticks, refreshes or expiry",
     "technique": "the object stays allocated in m_removedAuras until the owner's next _DeleteRemovedAuras "
                  "(Unit.cpp:2999) -- up to one full world tick when removed after the owner's update; later effects "
                  "of the same UpdateOwner still run AuraEffect::Update on it (no IsRemoved check, SpellAuras.cpp:845-846, "
                  "SpellAuraEffects.cpp:1250-1276) but GetApplicationList is empty, so only OnEffectUpdatePeriodic "
                  "script hooks and _ticksDone see it",
     "scenarios": ["death-lower-id-kills"], "evidence": ["trinity-consumer", "trinity-probe"]},
    {"id": "GN-I-05", "name": "child spells capture values, not the aura",
     "semantics": "a triggered spell in flight from an aura that has since been removed or refreshed still resolves "
                  "with the values captured when it was cast",
     "technique": "Spell::prepare copies SpellInfo* and cast item level from the triggering AuraEffect (Spell.cpp:3459-3463)",
     "scenarios": [], "evidence": ["trinity-consumer"]},
    {"id": "GN-I-06", "name": "pending charge drop defers expiry",
     "semantics": "an aura whose last charge is being consumed by a delayed hit is not expire-removed before the drop",
     "technique": "IsExpired = !duration && !m_dropEvent (SpellAuras.h:226); ChargeDropEvent aborted by _Remove",
     "scenarios": [], "evidence": ["trinity-consumer"]},
    {"id": "GN-I-07", "name": "same-pass visibility of a new generation depends on storage order",
     "semantics": "whether a just-created aura loses one world diff immediately depends on its SpellId relative to "
                  "the owner's update iterator (OR-I-11) and on caster/owner update order (OR-I-10)",
     "technique": "std::multimap iteration with a pre-advanced member iterator (Unit.cpp:2975-2980)",
     "scenarios": ["trigger-insert-before-next", "trigger-insert-after-next", "session-creation-decrement",
                   "late-owner-creation"], "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
)
