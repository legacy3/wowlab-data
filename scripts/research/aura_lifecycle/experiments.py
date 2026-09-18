"""Retail experiment design (track L).

Trinity is a consumer oracle, not Retail truth.  Every conclusion that rests on
Trinity alone (or on build-skewed / script-controlled content) needs the
smallest *live-game* observation that separates the competing models.  This
module holds

* the **observation surface** of the current Retail client (12.1.0): which
  channels expose aura state, under which addon restrictions, at which
  resolution -- established from Blizzard's generated API documentation
  (``SURFACE_PIN``), secondary web documentation and one local capture;
  unverified surfaces are marked as such, never assumed;
* :func:`classify` -- the fidelity (``exact`` / ``approximate`` /
  ``insufficient``) an experiment can reach, derived from what its required
  observables need and what the surface offers in the experiment's context;
* small prediction functions for the competing models (Trinity ones carry
  ``Mirrors:`` lines; alternative models name their source);
* :func:`catalogue` -- the machine-readable experiment catalogue, with
  witnesses checked against the snapshot and applicability counts per named
  population; :func:`merge_candidates` folds other tracks'
  ``retail_experiments`` into it.

No live integration (Lua / V8 / Wasm) lives here: experiments are specified,
not executed.
"""

from __future__ import annotations

import struct
from fractions import Fraction
from typing import Any, Iterable

from procs.enums import attr, aura

from . import FailClosed

# ---------------------------------------------------------------------------
# Observation surface
# ---------------------------------------------------------------------------

# Blizzard's generated API documentation, as mirrored by Gethe/wow-ui-source.
SURFACE_PIN = {
    "repo": "Gethe/wow-ui-source",
    "branch": "live",
    "commit": "78282522143e25c3540583734fd192c3d69be910",
    "client_build": "12.1.0.69875",
    "docs_dir": "Interface/AddOns/Blizzard_APIDocumentationGenerated",
}
_DOCS = SURFACE_PIN["docs_dir"]

SOURCE_KINDS = ("blizzard-generated-doc", "wiki-secondary", "web-secondary", "local-capture", "trinity-consumer",
                "simc-consumer")
SOURCE_STATUS = ("verified-primary", "verified-secondary", "unverified")

# Every surface claim cites one of these.  ``quote`` is a verbatim substring of
# the cited file/line (checked by tests when a local copy of the docs exists).
SOURCES: dict[str, dict[str, Any]] = {
    "SRC-L-01": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/UnitAuraDocumentation.lua:170",
                 "quote": 'Name = "GetAuraDataByAuraInstanceID"',
                 "claim": "GetAuraDataByAuraInstanceID(unit, auraInstanceID) -> AuraData; RequiresUnitAuraAccess, "
                          "SecretWhenUnitAuraRestricted"},
    "SRC-L-02": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/UnitAuraDocumentation.lua:452",
                 "quote": 'Name = "GetUnitAuras"',
                 "claim": "GetUnitAuras / GetUnitAuraInstanceIDs (:432) enumerate a unit's auras with filter + sort rule"},
    "SRC-L-03": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/UnitAuraDocumentation.lua:392",
                 "quote": "Returns the client-predicted new duration of this aura if it were cast again right now.",
                 "claim": "GetRefreshExtendedDuration: the CLIENT's prediction of a refresh result (not server truth)"},
    "SRC-L-04": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/UnitAuraDocumentation.lua:600",
                 "quote": 'LiteralName = "UNIT_AURA"',
                 "claim": "UNIT_AURA(unitTarget, updateInfo), SecretWhenAurasRestricted (:603)"},
    "SRC-L-05": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/UnitConstantsDocumentation.lua:55",
                 "quote": 'Name = "UnitAuraUpdateInfo"',
                 "claim": "UnitAuraUpdateInfo = {isFullUpdate, removedAuraInstanceIDs, addedAuras (AuraData), "
                          "updatedAuraInstanceIDs} (:59-62)"},
    "SRC-L-06": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/SecretPredicatesDocumentation.lua:79",
                 "quote": "Guarded APIs and events produce secret values when combat, encounter, challenge mode, or "
                          "PvP match addon restrictions are in effect. Individual spells may be flagged as never or "
                          "always secret, which takes priority over restrictions.",
                 "claim": "SecretWhenUnitAuraRestricted: aura values are secret under Combat/Encounter/ChallengeMode/"
                          "PvPMatch restrictions unless the spell is flagged never-secret"},
    "SRC-L-07": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/RestrictedActionsConstantsDocumentation.lua:26",
                 "quote": "The player is actively affecting combat.",
                 "claim": "AddOnRestrictionType.Combat definition (Encounter :27, ChallengeMode :28, PvPMatch :29, "
                          "Map :30)"},
    "SRC-L-08": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/SecretPredicateAPIDocumentation.lua:139",
                 "quote": "Returns true if a given spell identifier would, if applied as an aura, produce secret values "
                          "when queried.",
                 "claim": "C_Secrets.ShouldSpellAuraBeSecret / GetSpellAuraSecrecy (:45) -> SecrecyLevel "
                          "(NeverSecret / AlwaysSecret / ...) queryable per spell"},
    "SRC-L-09": {"kind": "blizzard-generated-doc", "status": "verified-primary",
                 "coords": f"{_DOCS}/CombatLogDocumentation.lua:132",
                 "quote": 'LiteralName = "COMBAT_LOG_EVENT_UNFILTERED"',
                 "claim": "COMBAT_LOG_EVENT_UNFILTERED HasRestrictions; the event getter lives in C_CombatLogSecure "
                          "(Environment SecureOnly, CombatLogSecureDocumentation.lua:6/:45)"},
    "SRC-L-10": {"kind": "wiki-secondary", "status": "verified-secondary",
                 "coords": "warcraft.wiki.gg/wiki/COMBAT_LOG_EVENT",
                 "quote": "This event is no longer accessible to addons in Midnight since Patch 12.0.0",
                 "claim": "addons cannot register CLEU in 12.x (ADDON_ACTION_FORBIDDEN); CLEU timestamp was Unix "
                          "seconds with millisecond precision"},
    "SRC-L-11": {"kind": "wiki-secondary", "status": "verified-secondary",
                 "coords": "warcraft.wiki.gg/wiki/UNIT_AURA (Patch 12.1.0 history)",
                 "quote": "The UNIT_AURA event now delivers a fully secret payload while auras are secret. AuraData "
                          "structs are now always fully secret.",
                 "claim": "12.1.0: while auras are secret the whole payload (incl. auraInstanceID) is secret"},
    "SRC-L-12": {"kind": "wiki-secondary", "status": "verified-secondary",
                 "coords": "warcraft.wiki.gg/wiki/Struct_AuraData",
                 "quote": "auraInstanceID",
                 "claim": "AuraData fields: applications, auraInstanceID (NeverSecret), charges, duration, "
                          "expirationTime, maxCharges, points, sourceUnit, spellId, timeMod, ...; the page does NOT "
                          "state whether auraInstanceID survives a refresh"},
    "SRC-L-13": {"kind": "web-secondary", "status": "unverified",
                 "coords": "wowcoach.gg/docs/combat-log/line-format; warcraft.wiki.gg/wiki/COMBAT_LOG_EVENT",
                 "quote": "COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,12.0.0,PROJECT_ID,1",
                 "claim": "WoWCombatLog.txt (/combatlog, advanced logging) is written by the client, unaffected by "
                          "addon restrictions; local-clock timestamps with 3 fractional digits + tz; aura subevents "
                          "APPLIED/REFRESH/APPLIED_DOSE/REMOVED_DOSE/REMOVED/BROKEN; no aura instance id and no "
                          "remaining duration on any line"},
    "SRC-L-14": {"kind": "local-capture", "status": "verified-secondary",
                 "coords": "innocent-js@4e46b18c:src/events/Aura.ts:55",
                 "quote": '"auraInstanceID": 2162',
                 "claim": "captured UNIT_AURA payload (client build not recorded): Barkskin/Matted Fur added with "
                          "auraInstanceIDs 2162/2163, a removed id 2079, applications 0 for non-stacking auras, "
                          "expirationTime with 3 decimals (GetTime clock)"},
    "SRC-L-15": {"kind": "trinity-consumer", "status": "verified-primary",
                 "coords": "SpellAuras.cpp:235-298",
                 "quote": "auraInfo.Slot = GetSlot();",
                 "claim": "Trinity's SMSG_AURA_UPDATE carries Slot + {CastID, SpellID, Flags, Applications "
                          "(stacks if IsUsingStacks else charges, :261), CastUnit, Duration=max ms, Remaining ms, "
                          "TimeMod, Points}; no aura instance id on the wire; Aura::m_castId is const "
                          "(SpellAuras.h:394) so a refresh keeps the original CastID"},
}

# Execution contexts of a live experiment and the addon restrictions they activate
# (RestrictedActionsConstantsDocumentation.lua:26-31).  Whether a training dummy or an
# out-of-combat self-heal activates ``Combat`` is itself unverified (AL-U-L-03).
CONTEXTS: dict[str, dict[str, Any]] = {
    "solo-no-combat": {"restrictions": (), "note": "open world, player not in combat (self-cast buffs/HoTs)"},
    "group-no-combat": {"restrictions": (), "note": "open world party, nobody in combat (friendly buffs/HoTs)"},
    "open-world-combat": {"restrictions": ("Combat",), "note": "dummy or open-world mob; DoTs on hostiles"},
    "duel": {"restrictions": ("Combat",), "note": "duel (not a PvPMatch per the enum text; unverified)"},
    "instance-encounter": {"restrictions": ("Combat", "Encounter", "Map"), "note": "dungeon/raid boss"},
}
# SecretWhenUnitAuraRestricted (SRC-L-06) lists exactly these restriction types.
AURA_SECRET_RESTRICTIONS = frozenset({"Combat", "Encounter", "ChallengeMode", "PvPMatch"})

# Observable quantities an experiment can require.
QUANTITIES = {
    "identity": "same aura object across events (instance identity)",
    "event-kind": "which lifecycle event happened (applied / refreshed / dose / removed / dispelled / died)",
    "instance-count": "number of coexisting instances on one target (per caster)",
    "source": "caster attribution of an instance",
    "stacks": "stack count",
    "charges": "proc charge count",
    "max-duration": "authored/current maximum duration of the instance",
    "remaining-duration": "remaining duration at a given moment",
    "event-time": "time of a tick / removal / refresh",
    "amount": "integer amount of a tick or absorb",
    "caster-stat": "caster stat value at a moment",
    "same-time-order": "server order of two events in the same server update",
    "client-prediction": "the client's own predicted refresh result",
    "secrecy": "whether a spell's aura data is secret in restricted contexts",
}

# Per channel: quantity -> quality.  Quality is "exact", ("timing", resolution_ms),
# "order" (receipt order only), or "unverified-exact" (believed exact, unconfirmed).
CHANNELS: dict[str, dict[str, Any]] = {
    "OBS-ADDON-AURA-QUERY": {
        "api": "C_UnitAuras.GetAuraDataByAuraInstanceID / GetUnitAuras / GetUnitAuraInstanceIDs (AuraData)",
        "requires_nonsecret_aura": True,
        "provides": {"identity": "exact", "instance-count": "exact", "source": "exact", "stacks": "exact",
                     "charges": "exact", "max-duration": "unverified-exact",
                     "remaining-duration": ("timing", 1)},
        "sources": ["SRC-L-01", "SRC-L-02", "SRC-L-06", "SRC-L-11", "SRC-L-12", "SRC-L-14", "SRC-L-15"],
        "note": "duration (s) presumably mirrors the server's integer max-duration ms (Trinity wire: Duration) -> "
                "exact if so (AL-U-L-02); expirationTime = client receipt time + Remaining -> latency-bound",
    },
    "OBS-UNIT-AURA-EVENT": {
        "api": "UNIT_AURA updateInfo {addedAuras, updatedAuraInstanceIDs, removedAuraInstanceIDs, isFullUpdate}",
        "requires_nonsecret_aura": True,
        "provides": {"identity": "exact", "event-kind": "exact", "event-time": ("timing", 1)},
        "sources": ["SRC-L-04", "SRC-L-05", "SRC-L-11"],
        "note": "added/updated/removed only: no removal cause; isFullUpdate batches hide individual events",
    },
    "OBS-REFRESH-PREDICTION": {
        "api": "C_UnitAuras.GetRefreshExtendedDuration(unit, auraInstanceID[, spellID])",
        "requires_nonsecret_aura": True,
        "provides": {"client-prediction": "exact"},
        "sources": ["SRC-L-03"],
        "note": "Blizzard's client-side model of a refresh; compare with the server result, never substitute",
    },
    "OBS-SECRECY-QUERY": {
        "api": "C_Secrets.GetSpellAuraSecrecy / ShouldSpellAuraBeSecret(spellID)",
        "requires_nonsecret_aura": False,
        "provides": {"secrecy": "exact"},
        "sources": ["SRC-L-08"],
        "note": "decides whether an addon channel survives in a restricted context for one witness spell",
    },
    "OBS-COMBATLOG-FILE": {
        "api": "WoWCombatLog.txt via /combatlog with advanced combat logging",
        "requires_nonsecret_aura": False,
        "provides": {"event-kind": "exact", "instance-count": "exact", "source": "exact", "stacks": "exact",
                     "amount": "exact", "event-time": ("timing", 1), "remaining-duration": ("timing", 1),
                     "max-duration": ("timing", 1), "same-time-order": "order",
                     "caster-stat": "unverified-exact"},
        "sources": ["SRC-L-13", "SRC-L-10"],
        "note": "client-written, local clock at receipt; instance-count/source inferred per source GUID from "
                "APPLIED without REMOVED; durations only as event-time differences; advanced fields' unit "
                "semantics for periodic lines unverified (AL-U-L-05)",
    },
    "OBS-ADDON-CLEU": {
        "api": "COMBAT_LOG_EVENT_UNFILTERED (addon registration)",
        "requires_nonsecret_aura": False,
        "provides": {},
        "sources": ["SRC-L-09", "SRC-L-10"],
        "note": "unavailable to addons since 12.0.0 (registration forbidden)",
    },
    "OBS-UI-RECORDING": {
        "api": "screen recording of Blizzard's own aura frames (they may display secret values)",
        "requires_nonsecret_aura": False,
        "provides": {"stacks": "unverified-exact", "remaining-duration": ("timing", 100)},
        "sources": ["SRC-L-06"],
        "note": "display rounding and frame rate bound the resolution; last-resort channel",
    },
}

# Assumed bound on client-receipt jitter between two server events seen by one client.
# NOT evidence: an explicit assumption (AL-U-L-04), to be replaced by AL-X-L-11's measurement.
TIMING_JITTER_MS = 200

FIDELITY_ORDER = ("insufficient", "approximate", "exact")


def channel_available(channel: str, context: str, secrecy: str | None = None) -> bool:
    """Whether ``channel`` yields readable values in ``context``.

    An aura-data channel survives an aura-secret restriction only for a spell whose
    secrecy is ``NeverSecret`` (SRC-L-06: per-spell flags take priority).
    """
    spec = CHANNELS[channel]
    if not spec["provides"]:
        return False
    restricted = bool(AURA_SECRET_RESTRICTIONS.intersection(CONTEXTS[context]["restrictions"]))
    if spec["requires_nonsecret_aura"] and restricted:
        return secrecy == "NeverSecret"
    return True


def _grade(quality: Any, margin_ms: int | None, jitter_ms: int) -> str:
    if quality == "exact":
        return "exact"
    if quality == "unverified-exact":
        return "approximate"
    if quality == "order":
        # Receipt order of lines stamped with the same time proves nothing about server
        # scheduler order (brief: lexical order != scheduler order).
        return "insufficient"
    if isinstance(quality, tuple) and quality[0] == "timing":
        if margin_ms is None:
            raise FailClosed("timing observable without a discrimination margin")
        return "approximate" if margin_ms > 2 * (jitter_ms + quality[1]) else "insufficient"
    return "insufficient"


def best_channel(quantity: str, context: str, secrecy: str | None = None, margin_ms: int | None = None,
                 jitter_ms: int = TIMING_JITTER_MS) -> tuple[str, str | None]:
    if quantity not in QUANTITIES:
        raise FailClosed(f"unknown observable quantity {quantity!r}")
    best: tuple[str, str | None] = ("insufficient", None)
    for name in sorted(CHANNELS):
        quality = CHANNELS[name]["provides"].get(quantity)
        if quality is None or not channel_available(name, context, secrecy):
            continue
        grade = _grade(quality, margin_ms, jitter_ms)
        if FIDELITY_ORDER.index(grade) > FIDELITY_ORDER.index(best[0]):
            best = (grade, name)
    return best


def classify(observables: Iterable[dict], context: str, secrecy: str | None = None,
             jitter_ms: int = TIMING_JITTER_MS) -> dict[str, Any]:
    """Fidelity of an experiment = the weakest of its required observables.

    Each observable is ``{"quantity", "margin_ms"?}``; ``margin_ms`` is the smallest
    difference between the competing models' predictions (timing quantities only).
    """
    if context not in CONTEXTS:
        raise FailClosed(f"unknown experiment context {context!r}")
    basis = []
    overall = "exact"
    for obs in observables:
        grade, channel = best_channel(obs["quantity"], context, secrecy, obs.get("margin_ms"), jitter_ms)
        basis.append({"quantity": obs["quantity"], "grade": grade, "channel": channel,
                      **({"margin_ms": obs["margin_ms"]} if "margin_ms" in obs else {})})
        if FIDELITY_ORDER.index(grade) < FIDELITY_ORDER.index(overall):
            overall = grade
    if not basis:
        raise FailClosed("experiment without observables")
    return {"fidelity": overall, "basis": basis}


# ---------------------------------------------------------------------------
# Prediction models
# ---------------------------------------------------------------------------

def _f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def calculate_pct_i32(base: int, pct: int) -> int:
    """``CalculatePct<int32,int>``: ``T(base * float(pct) / 100.0f)`` in binary32.

    Mirrors: Util.h:72
    """
    product = _f32(_f32(float(base)) * _f32(float(pct)))
    return int(_f32(product / 100.0))


def pandemic_trinity_as_written(hit_ms: int, max_after_refresh_ms: int) -> int:
    """Trinity's refresh duration for an ATTR13 aura at the pinned revision.

    The refresh path (Unit::_TryStackingOrRefreshingExistingAura -> ModStackAmount ->
    RefreshTimers -> RefreshDuration) has already set the current duration to the max
    duration when the pandemic branch reads ``GetDuration()`` (track B), so the carry is the
    full cap regardless of the remaining time.
    Mirrors: Spell.cpp:3284-3288
    Mirrors: SpellAuras.cpp:1114-1121 (ModStackAmount refresh -> RefreshTimers)
    Mirrors: SpellAuras.cpp:976-990 (RefreshTimers -> RefreshDuration -> SetDuration(max))
    """
    return min(hit_ms + max_after_refresh_ms, calculate_pct_i32(hit_ms, 130))


def pandemic_remaining_read(hit_ms: int, remaining_ms: int) -> int:
    """Carry = remaining capped at 30% (read BEFORE the refresh overwrote it).

    Same arithmetic as the Trinity branch but fed the pre-refresh remaining time; this is
    what the branch evidently intends (track B: likely Trinity defect).  structural-inference.
    """
    return min(hit_ms + remaining_ms, calculate_pct_i32(hit_ms, 130))


def pandemic_simc(hit_ms: int, remaining_ms: int) -> Fraction:
    """simc ``DOT_REFRESH_PANDEMIC``: ``max(remains, min(0.3 * D, remains) + D)`` (action.cpp:4603)."""
    d, r = Fraction(hit_ms), Fraction(remaining_ms)
    return max(r, min(d * Fraction(3, 10), r) + d)


def pandemic_core(hit_ms: int, remaining_ms: int) -> int:
    """Core ``CappedCarryover`` (navigation, per track K: aura_state.rs:1153-1200):
    ``max(D + min(r, floor(3D/10)), r)`` relative to the refresh time."""
    return max(hit_ms + min(remaining_ms, (3 * hit_ms) // 10), remaining_ms)


def trinity_periodic_timeline(*, period: int, max_ms: int, extra_initial: bool, refresh_at: int | None = None,
                              refresh_max_ms: int | None = None, reset_on_refresh: bool = False,
                              diff: int = 1) -> dict[str, Any]:
    """Tick times of one periodic aura effect owned by one unit, ms resolution.

    Model assumptions (stated, not evidence): uniform update ``diff``; the refresh is applied
    after the owner update of the same millisecond (ordering is track I's question).
    Mirrors: SpellAuraEffects.cpp:949-958 (ResetPeriodic: ticks=0; timer=0 or period if EXTRA_INITIAL_PERIOD)
    Mirrors: SpellAuraEffects.cpp:936-945 (GetTotalTicks = max/period (+1 EXTRA_INITIAL_PERIOD))
    Mirrors: SpellAuras.cpp:855-862 (Aura::Update: duration -= diff, clamp 0)
    Mirrors: SpellAuraEffects.cpp:1250-1275 (AuraEffect::Update: timer loop, tick cap)
    Mirrors: Unit.cpp:2957-2992 (_UpdateSpells: UpdateOwner for all, then expired sweep)
    Mirrors: SpellAuras.cpp:952-990 (RefreshDuration: duration=max, ResetTicks; RefreshTimers periodic reset flag)
    """
    if period <= 0 or max_ms <= 0 or diff <= 0:
        raise FailClosed("timeline needs a positive period, finite duration and update step")
    if (refresh_at is None) != (refresh_max_ms is None):
        raise FailClosed("refresh needs both refresh_at and refresh_max_ms")
    maximum, duration = max_ms, max_ms
    ticks, timer = 0, (period if extra_initial else 0)
    out: list[int] = []
    t = 0
    while True:
        t += diff
        duration = max(duration - diff, 0)
        total = maximum // period + (1 if extra_initial else 0)
        timer += diff
        while timer >= period:
            timer -= period
            if ticks + 1 > total:
                break
            ticks += 1
            out.append(t)
        if duration == 0:
            return {"ticks": out, "removed_at": t}
        if refresh_at is not None and t - diff < refresh_at <= t:
            maximum = duration = refresh_max_ms
            ticks = 0
            if reset_on_refresh:
                timer = period if extra_initial else 0


def simc_periodic_timeline(*, period: int, duration_ms: int, tick_zero: bool, refresh_at: int | None = None,
                           refresh_duration_ms: int | None = None) -> dict[str, Any]:
    """simc dot: refresh keeps the tick phase (dot.cpp:938-962 reschedules only the end event);
    the last tick is partial with factor ``(end - last_full)/period`` (dot.cpp:693 last_tick_factor)."""
    end = duration_ms if refresh_at is None else refresh_at + refresh_duration_ms
    ticks: list[tuple[int, Fraction]] = [(0, Fraction(1))] if tick_zero else []
    k = 1
    while k * period <= end:
        ticks.append((k * period, Fraction(1)))
        k += 1
    last = (k - 1) * period
    if end > last:
        ticks.append((end, Fraction(end - last, period)))
    return {"ticks": [[t, str(f)] for t, f in ticks], "removed_at": end}


# ---------------------------------------------------------------------------
# Witness selectors (general predicates, never spell lists)
# ---------------------------------------------------------------------------

_PERIODIC_AURAS = frozenset(aura(n) for n in (
    "OBS_MOD_POWER", "PERIODIC_DAMAGE", "PERIODIC_HEAL", "OBS_MOD_HEALTH", "PERIODIC_TRIGGER_SPELL",
    "PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "PERIODIC_ENERGIZE", "PERIODIC_LEECH", "PERIODIC_HEALTH_FUNNEL",
    "PERIODIC_MANA_LEECH", "PERIODIC_DAMAGE_PERCENT", "POWER_BURN", "PERIODIC_DUMMY",
    "PERIODIC_TRIGGER_SPELL_WITH_VALUE"))  # SpellAuraEffects.cpp:966-980 (CalculatePeriodic list)
_A = {n: attr(n) for n in (
    "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION", "SPELL_ATTR3_DOT_STACKING_RULE", "SPELL_ATTR5_EXTRA_INITIAL_PERIOD",
    "SPELL_ATTR1_IS_CHANNELLED", "SPELL_ATTR1_IS_SELF_CHANNELLED", "SPELL_ATTR0_PASSIVE",
    "SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD")}


def _has(misc: dict | None, name: str) -> bool:
    if misc is None:
        return False
    word, mask = _A[name]
    return bool(int(misc[f"Attributes_{word}"]) & mask)


def spell_facts(data, spell: int) -> dict[str, Any] | None:
    """Lifecycle facts of one spell (DIFFICULTY_NONE rows) used by selectors and predictions."""
    misc = data.row("SpellMisc", spell)
    effects = data.effects(spell)
    if misc is None and not effects:
        return None
    options = data.row("SpellAuraOptions", spell)
    cats = data.row("SpellCategories", spell)
    dur = data.duration(misc["DurationIndex"]) if misc and misc["DurationIndex"] else None
    periodic = [{"index": e["EffectIndex"], "aura": e["EffectAura"], "period": e["EffectAuraPeriod"]}
                for e in effects if e["EffectAura"] in _PERIODIC_AURAS and e["EffectAuraPeriod"] > 0]
    return {
        "duration_ms": dur["Duration"] if dur else None,
        "max_duration_ms": dur["MaxDuration"] if dur else None,
        "stack_amount": options["CumulativeAura"] if options else 0,
        "dispel_type": cats["DispelType"] if cats else 0,
        "periodic": periodic,
        "aura_types": sorted({e["EffectAura"] for e in effects if e["EffectAura"]}),
        "pandemic": _has(misc, "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION"),
        "dot_stacking_rule": _has(misc, "SPELL_ATTR3_DOT_STACKING_RULE"),
        "extra_initial_period": _has(misc, "SPELL_ATTR5_EXTRA_INITIAL_PERIOD"),
        "channeled": _has(misc, "SPELL_ATTR1_IS_CHANNELLED") or _has(misc, "SPELL_ATTR1_IS_SELF_CHANNELLED"),
        "passive": _has(misc, "SPELL_ATTR0_PASSIVE"),
        "allow_while_dead": _has(misc, "SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD"),
    }


SELECTORS: dict[str, tuple[str, Any]] = {
    "finite": ("authored duration > 0", lambda f: bool(f["duration_ms"]) and f["duration_ms"] > 0),
    "active": ("not SPELL_ATTR0_PASSIVE", lambda f: not f["passive"]),
    "periodic": ("an effect in the CalculatePeriodic list with period > 0", lambda f: bool(f["periodic"])),
    "periodic-heal": ("a PERIODIC_HEAL effect with period > 0",
                      lambda f: any(p["aura"] == aura("PERIODIC_HEAL") for p in f["periodic"])),
    "periodic-damage": ("a PERIODIC_DAMAGE effect with period > 0",
                        lambda f: any(p["aura"] == aura("PERIODIC_DAMAGE") for p in f["periodic"])),
    "pandemic": ("SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION", lambda f: f["pandemic"]),
    "no-pandemic": ("not SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION", lambda f: not f["pandemic"]),
    "dot-stacking-rule": ("SPELL_ATTR3_DOT_STACKING_RULE", lambda f: f["dot_stacking_rule"]),
    "no-dot-stacking-rule": ("not SPELL_ATTR3_DOT_STACKING_RULE", lambda f: not f["dot_stacking_rule"]),
    "extra-initial-period": ("SPELL_ATTR5_EXTRA_INITIAL_PERIOD", lambda f: f["extra_initial_period"]),
    "single-stack": ("CumulativeAura < 2 (Trinity resets the periodic timer on spell refresh, Spell.cpp:3240)",
                     lambda f: f["stack_amount"] < 2),
    "stacking": ("CumulativeAura > 1", lambda f: f["stack_amount"] > 1),
    "stackable-one-slot": ("CumulativeAura > 1, not channeled, not ATTR3_DOT_STACKING_RULE (SpellInfo.cpp:1808)",
                           lambda f: f["stack_amount"] > 1 and not f["channeled"] and not f["dot_stacking_rule"]),
    "not-periodic": ("no periodic effect", lambda f: not f["periodic"]),
    "magic": ("DispelType 1 (Magic)", lambda f: f["dispel_type"] == 1),
    "dies-with-target": ("not SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD", lambda f: not f["allow_while_dead"]),
}


def matches(facts: dict[str, Any], selectors: Iterable[str]) -> dict[str, bool]:
    out = {}
    for name in selectors:
        if name not in SELECTORS:
            raise FailClosed(f"unknown selector {name!r}")
        out[name] = bool(SELECTORS[name][1](facts))
    return out


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def _margin(values: Iterable[int | Fraction]) -> int:
    vals = sorted({Fraction(v) for v in values})
    if len(vals) < 2:
        return 0
    return int(min(b - a for a, b in zip(vals, vals[1:])))


def _core_specs() -> list[dict[str, Any]]:
    """The core experiment set.  Numeric predictions that depend on authored data are
    filled from the witness in :func:`_predict`; everything else is stated here."""
    return [
        {
            "id": "AL-X-L-01", "topic": "identity",
            "question": "Does a same-caster refresh keep the aura instance (auraInstanceID) or replace it?",
            "context": "solo-no-combat",
            "witness": {"spell": 774, "selectors": ["finite", "active", "periodic-heal", "pandemic"]},
            "setup": {"class": "Druid", "spec": "Restoration", "talents": "none required",
                      "steps": ["/combatlog on", "cast the witness on self out of combat; record UNIT_AURA addedAuras "
                                "auraInstanceID", "recast at 2 s remaining (pandemic branch)", "record the next UNIT_AURA updateInfo",
                                "repeat with recast at 8 s remaining (carry capped)", "repeat after a stat change (AL-X-L-06 interplay)"]},
            "observables": [{"quantity": "identity"}, {"quantity": "event-kind"}],
            "api": ["OBS-UNIT-AURA-EVENT", "OBS-ADDON-AURA-QUERY", "OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "slot-keyed (Trinity wire)", "source": "SpellAuras.cpp:235-298; Aura::m_castId const "
                 "SpellAuras.h:394", "evidence": "trinity-consumer",
                 "prediction": {"instance_id": "unchanged", "update_info": "updatedAuraInstanceIDs=[old]",
                                "combat_log": "SPELL_AURA_REFRESH"}},
                {"name": "cast-keyed (new CastID per recast)", "source": "structural-inference (client allocates "
                 "ids: SRC-L-14 ids 2079..2163 > MAX_AURAS 300)", "evidence": "structural-inference",
                 "prediction": {"instance_id": "new", "update_info": "removedAuraInstanceIDs=[old], addedAuras=[new]",
                                "combat_log": "SPELL_AURA_REFRESH"}},
            ],
            "discriminates": "instance identity survives refresh (Trinity keeps Aura object, slot and CastID) vs a "
                             "new instance per application",
            "related": ["track:A", "AL-U-L-01"],
        },
        {
            "id": "AL-X-L-02", "topic": "identity",
            "question": "When a second caster refreshes a stackable-on-one-slot aura, whose aura is it afterwards?",
            "context": "group-no-combat",
            "witness": {"spell": None, "selectors": ["finite", "active", "stackable-one-slot"]},
            "setup": {"class": "two players of the witness class", "spec": "any", "talents": "witness-dependent",
                      "steps": ["player A applies the witness aura to player C", "player B applies it to C",
                                "read sourceUnit, applications, auraInstanceID on C"]},
            "observables": [{"quantity": "source"}, {"quantity": "stacks"}, {"quantity": "identity"}],
            "api": ["OBS-ADDON-AURA-QUERY", "OBS-UNIT-AURA-EVENT"],
            "models": [
                {"name": "Trinity: one slot, original caster kept, stack +1", "source": "Unit.cpp:3386-3445 "
                 "(lookup ANY caster when IsStackableOnOneSlotWithDifferentCasters, track A)",
                 "evidence": "trinity-consumer",
                 "prediction": {"instances": 1, "sourceUnit": "A", "applications": 2}},
                {"name": "latest caster owns the slot", "source": "structural-inference", "evidence": "retail-unknown",
                 "prediction": {"instances": 1, "sourceUnit": "B", "applications": 2}},
                {"name": "per-caster instances", "source": "structural-inference", "evidence": "retail-unknown",
                 "prediction": {"instances": 2, "sourceUnit": ["A", "B"], "applications": [1, 1]}},
            ],
            "discriminates": "caster identity rule of one-slot multi-caster stacking",
            "related": ["track:A", "track:C"],
        },
        {
            "id": "AL-X-L-03", "topic": "coexistence",
            "question": "Do two casters' applications of the same spell coexist, replace, or merge?",
            "context": "group-no-combat",
            "witness": {"spell": 774, "selectors": ["finite", "active", "periodic-heal", "dot-stacking-rule"]},
            "variants": [
                {"witness": {"spell": 774, "selectors": ["periodic-heal", "dot-stacking-rule"]},
                 "trinity": "coexist: 2 instances (CanStackWith SpellAuras.cpp:1698 ATTR3_DOT_STACKING_RULE)"},
                {"witness": {"spell": 1126, "selectors": ["not-periodic", "no-dot-stacking-rule"]},
                 "trinity": "replace: newest wins (CanStackWith SpellAuras.cpp:1757-1767 same rank chain -> false; "
                            "_RemoveNoStackAurasDueToAura removes the old)"},
            ],
            "setup": {"class": "two players of the witness class", "spec": "any", "talents": "none required",
                      "steps": ["A applies the witness to C", "B applies the witness to C 3 s later",
                                "list C's auras with GetUnitAuras(C, 'HELPFUL'): count, sourceUnit, instance ids"]},
            "observables": [{"quantity": "instance-count"}, {"quantity": "source"}],
            "api": ["OBS-ADDON-AURA-QUERY", "OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "Trinity CanStackWith", "source": "SpellAuras.cpp:1633-1770", "evidence": "trinity-consumer",
                 "prediction": {"periodic+DOT_STACKING_RULE": 2, "non-periodic same spell": "1 (B's)"}},
                {"name": "highest-value-wins (no replacement by weaker)", "source": "structural-inference",
                 "evidence": "retail-unknown",
                 "prediction": {"periodic+DOT_STACKING_RULE": 2, "non-periodic same spell": "1 (stronger)"}},
                {"name": "per-caster always", "source": "structural-inference", "evidence": "retail-unknown",
                 "prediction": {"periodic+DOT_STACKING_RULE": 2, "non-periodic same spell": 2}},
            ],
            "discriminates": "observable coexistence (not Trinity's multimap storage) per stacking shape",
            "related": ["track:A"],
        },
        {
            "id": "AL-X-L-04", "topic": "pandemic",
            "question": "What maximum duration results from refreshing an ATTR13 aura with little time left?",
            "context": "solo-no-combat",
            "witness": {"spell": 774, "selectors": ["finite", "active", "pandemic", "periodic-heal"]},
            "setup": {"class": "Druid", "spec": "Restoration", "talents": "none affecting the witness duration "
                      "(verify base duration equals the authored one first)",
                      "steps": ["cast the witness on self out of combat", "before recasting read "
                                "GetRefreshExtendedDuration (client prediction)", "recast with r_small remaining",
                                "read AuraData.duration after the UNIT_AURA update", "repeat with r_large"]},
            "observables": [{"quantity": "max-duration"}, {"quantity": "client-prediction"}],
            "api": ["OBS-ADDON-AURA-QUERY", "OBS-REFRESH-PREDICTION", "OBS-COMBATLOG-FILE"],
            "discriminates": "Trinity-as-written full 30% carry vs remaining-capped carry (simc/Core/intended); "
                             "r_large is the non-discriminating control",
            "related": ["track:B", "track:K"],
        },
        {
            "id": "AL-X-L-05", "topic": "next-tick",
            "question": "When does the next tick occur after a refresh, and is the final tick partial?",
            "context": "solo-no-combat",
            "witness": {"spell": 774, "selectors": ["finite", "periodic-heal", "pandemic", "extra-initial-period"]},
            "setup": {"class": "Druid", "spec": "Restoration", "talents": "none; record haste (period scales)",
                      "steps": ["/combatlog on (advanced)", "cast the witness on self below full health",
                                "recast at inputs.refresh_at_ms", "let it expire",
                                "read SPELL_PERIODIC_HEAL times/amounts and SPELL_AURA_REMOVED"]},
            "observables": [{"quantity": "event-time"}, {"quantity": "amount"}],
            "api": ["OBS-COMBATLOG-FILE"],
            "discriminates": "phase kept + whole ticks (Trinity pandemic branch) vs timer reset (Trinity non-pandemic "
                             "branch) vs phase kept + partial last tick (simc)",
            "related": ["track:D", "track:B"],
        },
        {
            "id": "AL-X-L-06", "topic": "snapshot",
            "question": "After a caster stat change mid-aura (no refresh), do later ticks change?",
            "context": "solo-no-combat",
            "witness": {"spell": 774, "selectors": ["finite", "periodic-heal"]},
            "setup": {"class": "Druid", "spec": "Restoration", "talents": "none",
                      "steps": ["/combatlog on (advanced)", "record caster stats", "cast the witness on self",
                                "after the 2nd tick gain a known healing-done/primary-stat buff (verify its effect "
                                "out of combat first)", "compare non-crit tick amounts before/after"]},
            "observables": [{"quantity": "amount"}, {"quantity": "caster-stat"}],
            "api": ["OBS-COMBATLOG-FILE", "OBS-ADDON-AURA-QUERY"],
            "models": [
                {"name": "Trinity dynamic-each-tick", "source": "SpellAuraEffects.cpp:5916 (heal) / :5664 (damage) "
                 "BonusDone at tick time", "evidence": "trinity-consumer",
                 "prediction": {"ticks_after_change": "scaled by the stat change"}},
                {"name": "application-snapshot", "source": "structural-inference", "evidence": "retail-unknown",
                 "prediction": {"ticks_after_change": "unchanged until refresh"}},
            ],
            "discriminates": "input timing of periodic amounts (dynamic vs snapshot)",
            "related": ["track:D"],
        },
        {
            "id": "AL-X-L-07", "topic": "death",
            "question": "What happens to a periodic aura when its caster dies, and when its target dies, before expiry?",
            "context": "open-world-combat",
            "witness": {"spell": 146739, "selectors": ["finite", "periodic-damage", "dies-with-target"]},
            "setup": {"class": "Warlock", "spec": "Affliction", "talents": "none",
                      "steps": ["/combatlog on", "(a) apply the DoT to a high-health open-world mob, then die "
                                "(e.g. fall damage) before expiry", "(b) apply to a low-health mob and kill it",
                                "read SPELL_PERIODIC_DAMAGE after UNIT_DIED and SPELL_AURA_REMOVED presence/time"]},
            "observables": [{"quantity": "event-kind"}, {"quantity": "source"}],
            "api": ["OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "Trinity", "source": "Unit.cpp:4472 RemoveAllAurasOnDeath removes only the dying unit's own "
                 "auras (keeps passive / ATTR3_ALLOW_AURA_WHILE_DEAD); SpellAuras.cpp:557 caster lookup",
                 "evidence": "trinity-consumer",
                 "prediction": {"caster_dies": "DoT keeps ticking to expiry", "target_dies": "aura removed "
                                "(AURA_REMOVE_BY_DEATH) at death"}},
                {"name": "caster-death ends caster's DoTs", "source": "structural-inference",
                 "evidence": "retail-unknown",
                 "prediction": {"caster_dies": "SPELL_AURA_REMOVED at caster death, no further ticks",
                                "target_dies": "aura removed at death"}},
            ],
            "discriminates": "death is (or is not) a lifecycle event for auras the dead unit cast",
            "related": ["track:E", "UNK-E-001"],
        },
        {
            "id": "AL-X-L-08", "topic": "dispel",
            "question": "Does a dispel between ticks produce a final (partial) tick, and how is the removal logged?",
            "context": "duel",
            "witness": {"spell": 774, "selectors": ["finite", "periodic-heal", "magic"]},
            "setup": {"class": "Druid (target) + a class with an offensive magic dispel", "spec": "any",
                      "talents": "offensive dispel",
                      "steps": ["/combatlog on (both)", "duel; target casts the witness on self",
                                "opponent dispels 1.5 s after a tick", "read SPELL_DISPEL, SPELL_AURA_REMOVED and any "
                                "SPELL_PERIODIC_HEAL at the dispel time"]},
            "observables": [{"quantity": "event-kind"}, {"quantity": "event-time", "margin_ms": 1500}],
            "api": ["OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "Trinity", "source": "Unit.cpp:4006 RemoveAurasDueToSpellByDispel -> ModStackAmount/"
                 "ModCharges(ENEMY_SPELL) -> removal, no tick on removal", "evidence": "trinity-consumer",
                 "prediction": {"tick_at_dispel": False, "events": ["SPELL_DISPEL", "SPELL_AURA_REMOVED"]}},
                {"name": "partial tick on early removal", "source": "structural-inference",
                 "evidence": "retail-unknown",
                 "prediction": {"tick_at_dispel": "partial (0.5 of a tick)", "events": ["SPELL_PERIODIC_HEAL",
                                "SPELL_DISPEL", "SPELL_AURA_REMOVED"]}},
            ],
            "discriminates": "removal-by-dispel pays nothing vs a partial final tick",
            "related": ["track:E", "track:D"],
        },
        {
            "id": "AL-X-L-09", "topic": "same-timestamp-order",
            "question": "When a dispel and a tick land in the same server update, which happens first?",
            "context": "duel",
            "witness": {"spell": 774, "selectors": ["periodic-heal", "magic"]},
            "setup": {"class": "as AL-X-L-08", "spec": "any", "talents": "offensive dispel",
                      "steps": ["repeat AL-X-L-08 aiming the dispel at the tick time; collect many trials"]},
            "observables": [{"quantity": "same-time-order"}],
            "api": ["OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "Trinity: spell effects before aura updates in the same unit update", "source": "track I "
                 "(Unit::Update order)", "evidence": "trinity-consumer",
                 "prediction": {"tick_after_dispel": "never"}},
                {"name": "aura update first", "source": "structural-inference", "evidence": "retail-unknown",
                 "prediction": {"tick_after_dispel": "the coinciding tick is paid"}},
            ],
            "discriminates": "only via tick presence statistics, never via line order (receipt order is not "
                             "scheduler order)",
            "related": ["track:I", "AL-X-L-08"],
        },
        {
            "id": "AL-X-L-10", "topic": "stack-around-tick",
            "question": "Does a stack gained on a tick apply to that tick's amount or only to later ticks?",
            "context": "open-world-combat",
            "witness": {"spell": 980, "selectors": ["finite", "periodic-damage", "stacking", "pandemic"]},
            "setup": {"class": "Warlock", "spec": "Affliction", "talents": "none altering the witness stacking",
                      "steps": ["/combatlog on", "apply the witness to a training dummy",
                                "read SPELL_AURA_APPLIED_DOSE (stack) and SPELL_PERIODIC_DAMAGE (non-crit) per tick"]},
            "observables": [{"quantity": "stacks"}, {"quantity": "amount"}],
            "api": ["OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "Trinity pinned", "source": "no consumer for the witness's stack growth: "
                 "spell_warlock.cpp has no Agony stack script; PERIODIC_DUMMY effect 1 is inert",
                 "evidence": ["trinity-consumer", "script-consumer"],
                 "prediction": {"stack_multipliers_first_4_ticks": [1, 1, 1, 1]}},
                {"name": "stack after damage", "source": "structural-inference (effect order: damage index 0 before "
                 "dummy index 1 in UpdateOwner, SpellAuras.cpp:845-846)", "evidence": "structural-inference",
                 "prediction": {"stack_multipliers_first_4_ticks": [1, 2, 3, 4]}},
                {"name": "stack before damage", "source": "structural-inference", "evidence": "retail-unknown",
                 "prediction": {"stack_multipliers_first_4_ticks": [2, 3, 4, 5]}},
            ],
            "discriminates": "same-tick stack mutation order; absence of a Trinity consumer is not absence of "
                             "Retail behaviour",
            "related": ["track:C", "track:H", "track:D"],
        },
        {
            "id": "AL-X-L-11", "topic": "calibration",
            "question": "How large is the combat-log timing jitter between two server events of one aura?",
            "context": "solo-no-combat",
            "witness": {"spell": 774, "selectors": ["finite", "periodic-heal"]},
            "setup": {"class": "Druid", "spec": "Restoration", "talents": "none; fixed haste during the run",
                      "steps": ["/combatlog on", "cast the witness on self 30x", "histogram tick-to-tick intervals"]},
            "observables": [{"quantity": "event-time", "margin_ms": 1000}],
            "api": ["OBS-COMBATLOG-FILE"],
            "models": [
                {"name": "fixed-period server ticks", "source": "SpellAuraEffects.cpp:1250-1275",
                 "evidence": "trinity-consumer", "prediction": {"interval_spread_ms": "<= receipt jitter"}},
                {"name": "server update quantised ticks", "source": "structural-inference",
                 "evidence": "retail-unknown", "prediction": {"interval_spread_ms": ">= server update period"}},
            ],
            "discriminates": "replaces the TIMING_JITTER_MS assumption with a measurement",
            "related": ["AL-U-L-04"],
        },
        {
            "id": "AL-X-L-12", "topic": "surface",
            "question": "Which witness auras are NeverSecret (addon-readable even in combat)?",
            "context": "solo-no-combat",
            "witness": {"spell": 146739, "selectors": ["finite"]},
            "setup": {"class": "any", "spec": "any", "talents": "none",
                      "steps": ["out of combat call C_Secrets.GetSpellAuraSecrecy(id) for every catalogue witness"]},
            "observables": [{"quantity": "secrecy"}],
            "api": ["OBS-SECRECY-QUERY"],
            "models": [
                {"name": "all player auras contextually secret", "source": "SRC-L-06", "evidence": "structural-inference",
                 "prediction": {"secrecy": "contextual for every witness"}},
                {"name": "some flagged NeverSecret", "source": "SRC-L-06 per-spell flags", "evidence": "retail-unknown",
                 "prediction": {"secrecy": "NeverSecret for some witnesses"}},
            ],
            "discriminates": "whether in-combat experiments can be upgraded to addon channels",
            "related": ["AL-U-L-08"],
        },
    ]


def _predict(spec: dict[str, Any], facts: dict[str, Any] | None) -> None:
    """Fill data-derived model predictions (pandemic, next tick) from the witness facts."""
    if spec["topic"] == "pandemic":
        if not facts or not facts["duration_ms"]:
            raise FailClosed(f"{spec['id']}: witness without an authored duration")
        d = facts["duration_ms"]
        cap = calculate_pct_i32(d, 130) - d
        r_small, r_large = cap // 2, d // 2
        spec["setup"]["inputs"] = {"D_ms": d, "r_small_ms": r_small, "r_large_ms": r_large,
                                   "note": "D = authored duration without mods; remaining r read at recast"}

        def row(fn):
            return {"r_small": _num(fn(d, r_small)), "r_large": _num(fn(d, r_large))}
        spec["models"] = [
            {"name": "Trinity as written", "source": "Spell.cpp:3284-3288 after SpellAuras.cpp:976-990",
             "evidence": "trinity-consumer",
             "prediction": {"r_small": pandemic_trinity_as_written(d, d), "r_large": pandemic_trinity_as_written(d, d)}},
            {"name": "remaining-capped carry (intended Trinity)", "source": "structural-inference (track B)",
             "evidence": "structural-inference", "prediction": row(pandemic_remaining_read)},
            {"name": "simc DOT_REFRESH_PANDEMIC", "source": "simc action.cpp:4603", "evidence": "simc-consumer",
             "prediction": row(pandemic_simc)},
            {"name": "Core CappedCarryover", "source": "Core aura_state.rs:1153-1200 (track K)",
             "evidence": "core-navigation", "prediction": row(pandemic_core)},
        ]
        small = [m["prediction"]["r_small"] for m in spec["models"]]
        spec["observables"][0]["margin_ms"] = _margin(small)
    elif spec["topic"] == "next-tick":
        if not facts or not facts["periodic"] or not facts["duration_ms"]:
            raise FailClosed(f"{spec['id']}: witness without a periodic effect / duration")
        period = min(p["period"] for p in facts["periodic"])
        d = facts["duration_ms"]
        refresh_at = 2 * period + period // 3
        extra = facts["extra_initial_period"]
        pand_new = pandemic_trinity_as_written(d, d) if facts["pandemic"] else d
        simc_new = pandemic_simc(d, d - refresh_at) if facts["pandemic"] else d
        kept = trinity_periodic_timeline(period=period, max_ms=d, extra_initial=extra, refresh_at=refresh_at,
                                         refresh_max_ms=pand_new, reset_on_refresh=False)
        reset = trinity_periodic_timeline(period=period, max_ms=d, extra_initial=extra, refresh_at=refresh_at,
                                          refresh_max_ms=pand_new, reset_on_refresh=True)
        simc = simc_periodic_timeline(period=period, duration_ms=d, tick_zero=extra, refresh_at=refresh_at,
                                      refresh_duration_ms=int(simc_new))
        spec["setup"]["inputs"] = {"period_ms": period, "D_ms": d, "refresh_at_ms": refresh_at,
                                   "note": "unhasted; Trinity timeline with 1 ms updates, first update 1 ms after apply"}

        def first_after(ticks):
            return next(t for t in ticks if t > refresh_at)
        spec["models"] = [
            {"name": "Trinity pandemic branch (phase kept, whole ticks)",
             "source": "SpellAuras.cpp:976-990; SpellAuraEffects.cpp:949-958,1250-1275",
             "evidence": "trinity-consumer",
             "prediction": {"next_tick_ms": first_after(kept["ticks"]), "ticks": kept["ticks"],
                            "removed_at_ms": kept["removed_at"], "partial_last_tick": False}},
            {"name": "timer reset on refresh (Trinity non-pandemic branch)",
             "source": "Spell.cpp:3240 resetPeriodicTimer; SpellAuraEffects.cpp:949-958",
             "evidence": "trinity-consumer",
             "prediction": {"next_tick_ms": first_after(reset["ticks"]), "ticks": reset["ticks"],
                            "removed_at_ms": reset["removed_at"], "partial_last_tick": False}},
            {"name": "simc (phase kept, partial last tick)", "source": "simc dot.cpp:938-962, :693",
             "evidence": "simc-consumer",
             "prediction": {"next_tick_ms": first_after([t for t, _ in simc["ticks"]]), "ticks": simc["ticks"],
                            "removed_at_ms": simc["removed_at"], "partial_last_tick": simc["ticks"][-1][1] != "1"}},
        ]
        nexts = [m["prediction"]["next_tick_ms"] for m in spec["models"]]
        spec["observables"][0]["margin_ms"] = _margin(nexts)


def _num(v):
    if isinstance(v, Fraction):
        return int(v) if v.denominator == 1 else str(v)
    return v


def _witness_record(ctx, pops, drift, witness: dict[str, Any]) -> dict[str, Any]:
    spell = witness["spell"]
    if spell is None:
        return {"spell": None, "selectors": witness["selectors"], "status": "to-select",
                "note": "choose from applicability.candidates"}
    facts = spell_facts(ctx.data, spell)
    if facts is None:
        raise FailClosed(f"witness {spell} absent from the snapshot")
    rec = {"spell": spell, "name": ctx.name(spell), "selectors": witness["selectors"],
           "selector_results": matches(facts, witness["selectors"]),
           "player": spell in pops["player"], "build_skew": ctx.is_skew(spell),
           "facts": {k: facts[k] for k in ("duration_ms", "stack_amount", "dispel_type", "periodic", "pandemic",
                                           "dot_stacking_rule", "extra_initial_period")}}
    if drift is None:
        rec["drift"] = "not-fetched"
    else:
        dfacts = spell_facts(drift, spell)
        rec["drift"] = "unchanged" if dfacts is not None and {k: dfacts[k] for k in rec["facts"]} == rec["facts"] \
            else "changed"
    rec["status"] = "ok" if all(rec["selector_results"].values()) else "selector-mismatch"
    return rec


def applicability(facts_by_spell: dict[int, dict], pops: dict[str, frozenset[int]], selectors: list[str],
                  sample: int = 8) -> dict[str, Any]:
    """How many provider spells (per named population) an experiment's result generalises to."""
    hit = sorted(s for s, f in facts_by_spell.items() if all(matches(f, selectors).values()))
    out = {"selectors": {n: SELECTORS[n][0] for n in selectors}}
    for name in ("all", "player", "controlled"):
        out[name] = sum(1 for s in hit if s in pops[name])
    out["candidates_player"] = [s for s in hit if s in pops["player"]][:sample]
    return out


def catalogue(ctx) -> list[dict[str, Any]]:
    from .providers import populations
    pops = populations(ctx)
    drift = ctx.drift
    facts_by_spell = {s: f for s in sorted(pops["all"]) if (f := spell_facts(ctx.data, s)) is not None}
    out = []
    for spec in _core_specs():
        witness = _witness_record(ctx, pops, drift, spec["witness"])
        facts = facts_by_spell.get(spec["witness"]["spell"]) if spec["witness"]["spell"] is not None else None
        _predict(spec, facts)
        for v in spec.get("variants", []):
            v["witness"] = _witness_record(ctx, pops, drift, v["witness"])
        spec["witness"] = witness
        spec["applicability"] = applicability(facts_by_spell, pops, spec["witness"]["selectors"])
        cls = classify(spec["observables"], spec["context"])
        spec["fidelity"] = cls["fidelity"]
        spec["fidelity_basis"] = cls["basis"]
        spec["observable"] = "; ".join(f"{b['quantity']} via {b['channel'] or 'no channel'} ({b['grade']})"
                                       for b in cls["basis"])
        spec["evidence"] = sorted({e for m in spec["models"]
                                   for e in ([m["evidence"]] if isinstance(m["evidence"], str) else m["evidence"])}
                                  | {"retail-unknown"})
        spec["origin"] = "L"
        out.append(spec)
    out.sort(key=lambda e: e["id"])
    return out


# ---------------------------------------------------------------------------
# Late phase: fold other tracks' candidates in
# ---------------------------------------------------------------------------

# foreign experiment id -> catalogue id it duplicates (filled when candidates arrive).
ALIASES: dict[str, str] = {}


def _norm(text: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


def merge_candidates(entries: list[dict[str, Any]], candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe foreign ``retail_experiments`` into the catalogue.

    A candidate merges into an existing entry when ``ALIASES`` maps it or its normalised
    question is identical; otherwise it is kept under its own id with ``origin`` = its track
    and fidelity re-derived when it carries ``observables``/``context`` in this schema.
    """
    from .records import validate
    cands = list(candidates)
    validate("retail_experiments", cands)
    by_id = {e["id"]: e for e in entries}
    by_q = {_norm(e["question"]): e for e in entries}
    out = [dict(e) for e in entries]
    index = {e["id"]: e for e in out}
    for c in sorted(cands, key=lambda c: c["id"]):
        target = ALIASES.get(c["id"]) or (by_q.get(_norm(c["question"])) or {}).get("id")
        if target and target in by_id:
            e = index[target]
            e["merged_from"] = sorted(set(e.get("merged_from", [])) | {c["id"]})
            e["related"] = sorted(set(e["related"]) | set(c.get("related", [])) | {c["id"]})
            continue
        if c["id"] in index:
            raise ValueError(f"duplicate experiment id {c['id']!r}")
        new = dict(c)
        new["origin"] = c["id"].split("-")[2]
        if "observables" in c and "context" in c:
            cls = classify(c["observables"], c["context"], c.get("secrecy"))
            new["fidelity_track"] = c["fidelity"]
            new["fidelity"], new["fidelity_basis"] = cls["fidelity"], cls["basis"]
        else:
            new["surface_checked"] = False
        out.append(new)
        index[new["id"]] = new
    out.sort(key=lambda e: e["id"])
    return out


# ---------------------------------------------------------------------------
# Cross-cutting records
# ---------------------------------------------------------------------------

def unknowns() -> list[dict[str, Any]]:
    base = {"build_skew": False}
    rows = [
        ("AL-U-L-01", "auraInstanceID allocation", "What makes the client allocate a new auraInstanceID?",
         "Not on Trinity's wire (Slot + CastID only, SpellAuras.cpp:235-298); captured ids exceed MAX_AURAS 300",
         "client-side rule, undocumented", ["structural-inference", "retail-unknown"],
         ["SpellAuras.cpp:235", "SpellAuraDefines.h:22", "SRC-L-14"], "live client observation", "AL-X-L-01 run"),
        ("AL-U-L-02", "AuraData.duration precision", "Is AuraData.duration the server's integer max duration ms / 1000?",
         "Trinity sends Duration/Remaining as int32 ms (SpellAuras.cpp:270-274)", "Retail wire not documented",
         ["retail-unknown"], ["SpellAuras.cpp:270-274"], "live observation", "AL-X-L-04 shows non-round values"),
        ("AL-U-L-03", "Combat restriction trigger", "Which setups keep aura data readable (self HoT out of combat, "
         "friendly buffs, a training dummy)?", "Combat = 'actively affecting combat' (SRC-L-07)",
         "trigger conditions not documented", ["retail-unknown"], ["SRC-L-07", "SRC-L-06"],
         "live observation", "C_RestrictedActions.IsAddOnRestrictionActive(Combat) logged during each run"),
        ("AL-U-L-04", "combat log timing jitter", "Bound on receipt jitter between two server events in "
         "WoWCombatLog.txt?", "local clock ms stamps (SRC-L-13); TIMING_JITTER_MS=200 is an assumption",
         "no documentation", ["retail-unknown"], ["SRC-L-13"], "measurement", "AL-X-L-11 run"),
        ("AL-U-L-05", "advanced log unit for periodic lines", "Whose stats do advanced fields carry on "
         "SPELL_PERIODIC_* lines?", "17 advanced params listed (SRC-L-10)", "semantics per subevent undocumented",
         ["retail-unknown"], ["SRC-L-10"], "live observation", "compare with caster stats read out of combat"),
        ("AL-U-L-06", "field secrecy in 12.1", "Is auraInstanceID still readable while auras are secret?",
         "Struct_AuraData marks it NeverSecret (SRC-L-12) but 12.1.0 notes 'AuraData structs are now always fully "
         "secret' (SRC-L-11)", "sources conflict", ["retail-unknown"], ["SRC-L-11", "SRC-L-12"],
         "live observation", "issecretvalue(aura.auraInstanceID) in combat"),
        ("AL-U-L-07", "client refresh prediction", "Does GetRefreshExtendedDuration equal the server's refresh result?",
         "documented as client-predicted (SRC-L-03)", "prediction model unpublished", ["retail-unknown"],
         ["SRC-L-03"], "live observation", "AL-X-L-04 run"),
        ("AL-U-L-08", "per-spell aura secrecy source", "Where do NeverSecret/AlwaysSecret spell flags come from?",
         "C_Secrets exposes them (SRC-L-08); not found among snapshot tables inspected (SpellAuraVisibility has "
         "Type/Flags only)", "no DB2 column identified", ["unresolved"], ["SRC-L-08"],
         "a DB2/attribute column is identified", "AL-X-L-12 run cross-checked with DB2 columns"),
    ]
    return [{"id": i, "subject": s, "question": q, "known": k, "why_unresolved": w, "evidence": ev, "coords": co,
             "blocker": b, "reopen_condition": r, **base} for i, s, q, k, w, ev, co, b, r in rows]


def rules() -> list[dict[str, Any]]:
    return [
        {"id": "AL-R-L-01", "name": "addon aura channels are out-of-restriction channels",
         "definition": "AuraData/UNIT_AURA values are readable by addons only when no Combat/Encounter/"
                       "ChallengeMode/PvPMatch restriction is active, or the spell is NeverSecret",
         "population": {"name": "channels", "count": sum(1 for c in CHANNELS.values() if c["requires_nonsecret_aura"])},
         "counterexamples": [], "status": "proposed", "evidence": ["structural-inference"]},
        {"id": "AL-R-L-02", "name": "in-combat observation is combat-log-file only",
         "definition": "for secret auras in combat, the only exact channel is WoWCombatLog.txt: event kinds, sources, "
                       "stacks, amounts exact; no instance identity, durations only as event-time differences",
         "population": {"name": "quantities", "count": len(QUANTITIES)},
         "counterexamples": ["NeverSecret spells (AL-X-L-12)"], "status": "proposed",
         "evidence": ["structural-inference", "retail-unknown"]},
    ]


def falsification() -> list[dict[str, Any]]:
    return [
        {"id": "AL-F-L-01", "rule": "auraInstanceID persistence across refresh is documented",
         "attempt": "re-queried Struct_AuraData and UNIT_AURA pages for a verbatim statement (a first summary had "
                    "claimed 'persists across refresh')",
         "result": "neither page states it", "action": "kept as experiment AL-X-L-01"},
        {"id": "AL-F-L-02", "rule": "auraInstanceID is the server aura slot",
         "attempt": "compared the captured ids (SRC-L-14: 2079, 2162, 2163) with Trinity MAX_AURAS 300 "
                    "(SpellAuraDefines.h:22)",
         "result": "ids exceed the slot range", "action": "rule discarded; client-side allocation (AL-U-L-01)"},
        {"id": "AL-F-L-03", "rule": "addons can use CLEU out of combat",
         "attempt": "CombatLogDocumentation.lua:132 HasRestrictions; wiki: registration forbidden since 12.0.0",
         "result": "refuted", "action": "channel OBS-ADDON-CLEU provides nothing; WoWCombatLog.txt used instead"},
        {"id": "AL-F-L-04", "rule": "applications=0 for a non-stacking aura is a Retail-vs-Trinity divergence",
         "attempt": "read BuildUpdatePacket: Applications = IsUsingStacks ? stacks : charges (SpellAuras.cpp:261, "
                    ":1078)",
         "result": "Trinity also sends 0 (no stacks, no charges)", "action": "dropped the planned experiment"},
        {"id": "AL-F-L-05", "rule": "DoT experiments on a training dummy can read AuraData",
         "attempt": "SecretWhenUnitAuraRestricted covers Combat (SRC-L-06)",
         "result": "refuted unless the spell is NeverSecret", "action": "DoT experiments graded via the combat log"},
    ]


def corpus(ctx, candidates: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    from .records import provenance, validate_corpus
    entries = catalogue(ctx)
    entries = merge_candidates(entries, candidates)
    by_fid = {f: sum(1 for e in entries if e["fidelity"] == f) for f in FIDELITY_ORDER}
    payload = {
        "provenance": provenance("aura_lifecycle.py experiments --out docs/research/aura-lifecycle-corpora/"
                                 "retail-experiments.json", surface=dict(SURFACE_PIN)),
        "surface": {"sources": SOURCES, "channels": CHANNELS, "contexts": CONTEXTS,
                    "aura_secret_restrictions": sorted(AURA_SECRET_RESTRICTIONS), "quantities": QUANTITIES,
                    "timing_jitter_ms_assumption": TIMING_JITTER_MS},
        "counts": {"experiments": len(entries), "by_fidelity": by_fid,
                   "by_origin": {o: sum(1 for e in entries if e["origin"] == o)
                                 for o in sorted({e["origin"] for e in entries})}},
        "retail_experiments": entries,
        "unknowns": unknowns(),
        "rules": rules(),
        "falsification": falsification(),
    }
    validate_corpus(payload)
    return _jsonable(payload)


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, Fraction):
        return _num(x)
    return x
