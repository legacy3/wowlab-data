"""Targeting-relevant attributes and restrictions (Track A).

Which SpellAttr bits, ``SpellEffectAttributes``, ``SpellCustomAttributes``,
``SpellTargetRestrictions`` / ``SpellAuraRestrictions`` /
``SpellCastingRequirements`` columns, shapeshift restrictions and the dead-target
policy are read by *targeting* consumers, by which function and at which stage.

The attribute-bit consumer list is **scanned** from the pinned Trinity sources
(:func:`scan_consumers`): every ``SPELL_ATTR*`` / ``SpellEffectAttributes::*``
token inside a function listed in :data:`STAGES` is reported with its
``file:line``.  Functions outside :data:`STAGES` are not targeting consumers for
this audit (payload, proc, aura lifecycle ...).  Restriction-column consumers are
listed by hand in :data:`COLUMN_CONSUMERS` and anchor-checked by the tests.

Also: ``LoadSpellInfoCorrections`` rewrites that change targeting inputs
(:func:`corrections`), from the Dummy-pass ``corrections.json`` plus the
generic in-loop rules (SpellMgr.cpp:5244-5306), and ``LoadSpellInfoTargetCaps``
(SpellMgr.cpp:5407) sqrt target-limit registrations.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import DUMMY_CORPORA, TC_ROOT, FailClosed

GAME = "src/server/game"

# function (qualified name) -> stage.  Stages, in pipeline order.
STAGE_ORDER = (
    "load-rewrite", "explicit-mask", "explicit-init", "explicit-redirect", "explicit-validation", "explicit-range",
    "selection", "candidate-search", "per-candidate", "relation", "dead-policy", "cap", "chain", "per-effect",
    "application-entry", "post-selection", "channel-update",
)
STAGES: dict[str, str] = {
    "SpellMgr::LoadSpellInfoCorrections": "load-rewrite",
    "SpellMgr::LoadSpellInfoCustomAttributes": "load-rewrite",
    "SpellInfo::_InitializeExplicitTargetMask": "explicit-mask",
    "Spell::InitExplicitTargets": "explicit-init",
    "Spell::SelectExplicitTargets": "explicit-redirect",
    "WorldObject::GetMagicHitRedirectTarget": "explicit-redirect",
    "Unit::GetMeleeHitRedirectTarget": "explicit-redirect",
    "SpellInfo::CheckExplicitTarget": "explicit-validation",
    "Spell::CheckCast": "explicit-validation",
    "Spell::CheckRange": "explicit-range",
    "Spell::GetMinMaxRange": "explicit-range",
    "Spell::SelectSpellTargets": "post-selection",
    "Spell::SelectEffectImplicitTargets": "selection",
    "Spell::SelectImplicitChannelTargets": "selection",
    "Spell::SelectImplicitNearbyTargets": "selection",
    "Spell::SelectImplicitConeTargets": "selection",
    "Spell::SelectImplicitAreaTargets": "selection",
    "Spell::SelectImplicitCasterDestTargets": "selection",
    "Spell::SelectImplicitTargetDestTargets": "selection",
    "Spell::SelectImplicitDestDestTargets": "selection",
    "Spell::SelectImplicitCasterObjectTargets": "selection",
    "Spell::SelectImplicitTargetObjectTargets": "selection",
    "Spell::SelectImplicitTrajTargets": "selection",
    "Spell::SelectImplicitLineTargets": "selection",
    "Spell::SelectEffectTypeImplicitTargets": "selection",
    "SpellEffectInfo::CalcRadius": "selection",
    "Spell::GetSearcherTypeMask": "candidate-search",
    "Spell::SearchNearbyTarget": "candidate-search",
    "Spell::SearchAreaTargets": "candidate-search",
    "WorldObjectSpellTargetCheck::operator()": "per-candidate",
    "WorldObjectSpellNearbyTargetCheck::operator()": "per-candidate",
    "WorldObjectSpellAreaTargetCheck::operator()": "per-candidate",
    "WorldObjectSpellConeTargetCheck::operator()": "per-candidate",
    "WorldObjectSpellTrajTargetCheck::operator()": "per-candidate",
    "WorldObjectSpellLineTargetCheck::operator()": "per-candidate",
    "SpellInfo::CheckTarget": "per-candidate",
    "SpellInfo::CheckTargetCreatureType": "per-candidate",
    "WorldObject::IsValidAttackTarget": "relation",
    "WorldObject::IsValidAssistTarget": "relation",
    "SpellInfo::IsAllowingDeadTarget": "dead-policy",
    "SpellInfo::IsRequiringDeadTarget": "dead-policy",
    "SpellInfo::IsSingleTarget": "cap",
    "Spell::SearchChainTargets": "chain",
    "Spell::SelectImplicitChainTargets": "chain",
    "Spell::CheckEffectTarget": "per-effect",
    "Spell::AddUnitTarget": "application-entry",
    "Spell::UpdateChanneledTargetList": "channel-update",
}
# functions only partly about targeting: (start anchor, end anchor) text inside the body
REGIONS = {
    "Spell::CheckCast": ("// Don't check explicit target for passive spells", "// Spell cast only in battleground"),
}
SOURCES = ("Spells/Spell.cpp", "Spells/SpellInfo.cpp", "Spells/SpellMgr.cpp", "Entities/Unit/Unit.cpp",
           "Entities/Object/Object.cpp")

_DEF = re.compile(r"^(?!\s)(?!#)(?!//)[^;{}()]*?\b((?:\w+::)+(?:operator\(\)|~?\w+))\s*\(")
_TOKEN = re.compile(r"\b(SPELL_ATTR\d+_\w+|SpellEffectAttributes::\w+)\b")


def _tc_file(rel: str) -> Path:
    p = TC_ROOT / GAME / rel
    if not p.is_file():
        raise FailClosed(f"attributes: pinned Trinity source {p} not present")
    return p


def functions(text: str) -> list[tuple[str, int, int]]:
    """Top-level function definitions: (qualified name, first line, last line), 1-based."""
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        m = _DEF.match(lines[i])
        if not m:
            i += 1
            continue
        # find the opening brace of the body (skip prototypes ending in ';')
        j = i
        while j < len(lines) and "{" not in lines[j] and not lines[j].rstrip().endswith(";"):
            j += 1
        if j >= len(lines) or "{" not in lines[j]:
            i += 1
            continue
        depth = 0
        k = j
        started = False
        while k < len(lines):
            depth += lines[k].count("{") - lines[k].count("}")
            started = started or "{" in lines[k]
            if started and depth <= 0:
                break
            k += 1
        out.append((m.group(1), i + 1, k + 1))
        i = k + 1
    return out


@lru_cache(maxsize=1)
def scan_consumers() -> list[dict[str, Any]]:
    """Every attribute token read inside a targeting function.  Evidence: trinity-consumer."""
    rows = []
    for rel in SOURCES:
        text = _tc_file(rel).read_text(encoding="utf-8")
        lines = text.split("\n")
        for name, a, b in functions(text):
            stage = STAGES.get(name)
            if stage is None:
                continue
            lo, hi = a, b
            if name in REGIONS:
                first, last = REGIONS[name]
                body = lines[a - 1:b]
                lo = a + next(i for i, ln in enumerate(body) if first in ln)
                hi = a + next(i for i, ln in enumerate(body) if last in ln)
            for n in range(lo, hi + 1):
                code = lines[n - 1].split("//")[0]
                for tok in _TOKEN.findall(code):
                    rows.append({"token": tok, "function": name, "stage": stage,
                                 "consumer": f"{Path(rel).name}:{n}", "code": code.strip()[:200]})
    return rows


def consumers_by_token() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scan_consumers():
        out[r["token"]].append({k: r[k] for k in ("function", "stage", "consumer")})
    return dict(out)


# -- restriction / requirement columns (hand list, anchor-checked) ------------------------
COLUMN_CONSUMERS: dict[str, list[dict[str, str]]] = {
    "SpellTargetRestrictions.ConeDegrees": [
        {"consumer": "SpellInfo.cpp:1505", "stage": "load", "what": "ConeAngle = ConeDegrees"},
        {"consumer": "SpellMgr.cpp:5282", "stage": "load-rewrite", "what": "cone effect with ConeAngle ~0 -> 90"},
        {"consumer": "Spell.cpp:1281", "stage": "selection", "what": "cone angle (TARGET_UNIT_CONE_180_DEG_ENEMY 0 -> 180 unreachable)"},
    ],
    "SpellTargetRestrictions.Width": [
        {"consumer": "SpellInfo.cpp:1506", "stage": "load", "what": "Width"},
        {"consumer": "SpellMgr.cpp:3244", "stage": "load-rewrite", "what": "Width != 0 -> SPELL_ATTR0_CU_CONE_LINE"},
        {"consumer": "Spell.cpp:1301", "stage": "selection", "what": "cone line width (else caster combat reach)"},
        {"consumer": "Spell.cpp:1994", "stage": "selection", "what": "line width (else caster combat reach)"},
    ],
    "SpellTargetRestrictions.MaxTargets": [
        {"consumer": "SpellInfo.cpp:1509", "stage": "load", "what": "MaxAffectedTargets"},
        {"consumer": "SpellMgr.cpp:5304", "stage": "load-rewrite", "what": "SPELL_ATTR5_LIMIT_N and 0 -> 1"},
        {"consumer": "Spell.cpp:448", "stage": "cap", "what": "SpellValue::MaxAffectedTargets (+SpellModOp::MaxTargets, Spell.cpp:507)"},
        {"consumer": "Spell.cpp:1310", "stage": "cap", "what": "cone RandomResize"},
        {"consumer": "Spell.cpp:1445", "stage": "cap", "what": "area RandomResize / furthest truncate"},
        {"consumer": "Spell.cpp:2003", "stage": "cap", "what": "line nearest truncate"},
        {"consumer": "Unit.cpp:3477", "stage": "application", "what": "single-target aura instance limit (not selection)"},
    ],
    "SpellTargetRestrictions.Targets": [
        {"consumer": "SpellInfo.cpp:4604", "stage": "explicit-mask", "what": "ExplicitTargetMask |= Targets (required unless SPELL_ATTR13_DO_NOT_FAIL_IF_NO_TARGET, :4606)"},
        {"consumer": "SpellInfo.cpp:1840", "stage": "dead-policy", "what": "CORPSE_* / UNIT_DEAD flags allow dead targets"},
    ],
    "SpellTargetRestrictions.TargetCreatureType": [
        {"consumer": "SpellInfo.cpp:2633", "stage": "per-candidate", "what": "CheckTargetCreatureType (via CheckTarget:2463)"},
    ],
    "SpellTargetRestrictions.MaxTargetLevel": [
        {"consumer": "SpellInfo.cpp:1510", "stage": "load", "what": "loaded only; no Trinity reader (no consumer != no Retail behaviour)"},
    ],
    "SpellAuraRestrictions.TargetAuraState": [
        {"consumer": "SpellInfo.cpp:2496", "stage": "per-candidate", "what": "skipped if caster is a vehicle or target is caster's charmer/owner"}],
    "SpellAuraRestrictions.ExcludeTargetAuraState": [{"consumer": "SpellInfo.cpp:2499", "stage": "per-candidate", "what": "same guard"}],
    "SpellAuraRestrictions.TargetAuraSpell": [{"consumer": "SpellInfo.cpp:2504", "stage": "per-candidate", "what": "HasAura(any caster)"}],
    "SpellAuraRestrictions.ExcludeTargetAuraSpell": [{"consumer": "SpellInfo.cpp:2507", "stage": "per-candidate", "what": "HasAura(any caster)"}],
    "SpellAuraRestrictions.TargetAuraType": [{"consumer": "SpellInfo.cpp:2510", "stage": "per-candidate", "what": "HasAuraType"}],
    "SpellAuraRestrictions.ExcludeTargetAuraType": [{"consumer": "SpellInfo.cpp:2513", "stage": "per-candidate", "what": "HasAuraType"}],
    "SpellCastingRequirements.FacingCasterFlags": [
        {"consumer": "Spell.cpp:7300", "stage": "explicit-range", "what": "player caster: explicit unit must be in front (pi arc) "
                                                                         "unless within boundary radius; implicit targets unaffected"}],
    "SpellShapeshift.ShapeshiftMask/Exclude": [
        {"consumer": "SpellInfo.cpp:2111", "stage": "cast (caster form only)", "what": "CheckShapeshift reads the caster's form; "
                                                                                    "no target-side consumer"}],
}

# custom attributes that targeting reads (SpellInfo.h:125-151)
CUSTOM_ATTRIBUTES = {
    "SPELL_ATTR0_CU_CONE_BACK": 0x00000002, "SPELL_ATTR0_CU_CONE_LINE": 0x00000004,
    "SPELL_ATTR0_CU_PICKPOCKET": 0x00000400, "SPELL_ATTR0_CU_REQ_TARGET_FACING_CASTER": 0x00010000,
    "SPELL_ATTR0_CU_REQ_CASTER_BEHIND_TARGET": 0x00020000, "SPELL_ATTR0_CU_ALLOW_INFLIGHT_TARGET": 0x00040000,
    "SPELL_ATTR0_CU_CAN_TARGET_ANY_PRIVATE_OBJECT": 0x02000000,
}

EFFECT_ATTRIBUTES = {  # DBCEnums.h:2400
    "NoImmunity": 0x1, "PositionIsFacingRelative": 0x2, "JumpChargeUnitMeleeRange": 0x4,
    "JumpChargeUnitStrictPathCheck": 0x8, "ExcludeOwnParty": 0x10, "AlwaysAoeLineOfSight": 0x20,
    "SuppressPointsStacking": 0x40, "ChainFromInitialTarget": 0x80, "UncontrolledNoBackwards": 0x100,
    "AuraPointsStack": 0x200, "NoCopyDamageInterruptsOrProcs": 0x400, "AddTargetCombatReachToAOE": 0x800,
    "IsHarmful": 0x1000, "ForceScaleToOverrideCameraMinHeight": 0x2000, "PlayersOnly": 0x4000,
    "ComputePointsOnlyAtCastTime": 0x8000, "EnforceLineOfSightToChainTargets": 0x10000,
    "AreaEffectsUseTargetRadius": 0x20000, "TeleportWithVehicle": 0x40000,
    "ScalePointsByChallengeModeDamageScaler": 0x80000, "DontFailSpellOnTargetingFailure": 0x100000,
    "IgnoreDuringCooldownTimeRateCalculation": 0x800000, "DamageOnlyAbsorbShields": 0x4000000,
}
EFFECT_ATTRIBUTES_NYI = {"PositionIsFacingRelative", "JumpChargeUnitMeleeRange", "JumpChargeUnitStrictPathCheck",
                         "ExcludeOwnParty", "UncontrolledNoBackwards", "NoCopyDamageInterruptsOrProcs",
                         "AddTargetCombatReachToAOE", "ForceScaleToOverrideCameraMinHeight",
                         "ComputePointsOnlyAtCastTime", "AreaEffectsUseTargetRadius", "TeleportWithVehicle",
                         "ScalePointsByChallengeModeDamageScaler", "DamageOnlyAbsorbShields"}
# NYI but *targeting-shaped* by name (Trinity has no reader; name is not semantics)
EFFECT_ATTRIBUTES_TARGETING_NYI = {"ExcludeOwnParty", "AddTargetCombatReachToAOE", "AreaEffectsUseTargetRadius",
                                   "PositionIsFacingRelative"}


# -- corrections --------------------------------------------------------------------------
AREA_AURA_EFFECTS = {35, 65, 119, 128, 129, 143, 202, 271}  # SpellInfo.cpp:480 IsAreaAuraEffect


@lru_cache(maxsize=1)
def _correction_rows() -> list[dict[str, Any]]:
    path = DUMMY_CORPORA / "corrections.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["all_fixes"]


TARGETING_MEMBERS = ("TargetA", "TargetB", "TargetARadiusEntry", "TargetBRadiusEntry", "MaxAffectedTargets",
                     "ConeAngle", "Width", "RangeEntry", "Effect", "ChainTargets", "AttributesEx", "Attributes",
                     "AttributesEx2", "AttributesEx3", "AttributesEx4", "AttributesEx5", "AttributesEx6",
                     "AttributesEx7", "AttributesEx8", "AttributesEx9", "AttributesEx13", "AttributesCu",
                     "TargetAuraSpell", "ExcludeTargetAuraSpell", "Targets", "TargetCreatureType")


def _target_value(value: str) -> int | None:
    from .selectors import tid
    m = re.fullmatch(r"SpellImplicitTargetInfo\((\w*)\)", value.strip())
    if not m:
        return None
    return tid(m.group(1)) if m.group(1) else 0


@lru_cache(maxsize=None)
def corrections(spell: int) -> list[dict[str, Any]]:
    """``LoadSpellInfoCorrections`` writes for ``spell`` whose member can change targeting inputs."""
    out = []
    for block in _correction_rows():
        if spell not in block["ids"]:
            continue
        for w in block["writes"]:
            if w["member"] in TARGETING_MEMBERS:
                out.append({"member": w["member"], "effect": w["effect"], "op": w["op"], "value": w["value"],
                            "consumer": f"SpellMgr.cpp:{w['line']}", "class": w["class"]})
    return out


def corrected_effects(spell: int, effects: list) -> tuple[list[dict[str, Any]], list[str]]:
    """Effect slots after the target-relevant load rewrites.

    Mirrors: SpellMgr.cpp ``LoadSpellInfoCorrections`` (per-spell ``TargetA``/``TargetB``/``Effect``
    writes; SpellMgr.cpp:5286-5291 area-aura rewrite).  Returns ([{index, effect, a, b, attributes}], notes).
    Unparsable target writes fail closed.
    """
    from procs.enums import effect as effect_id
    rows = {e.index: {"index": e.index, "effect": e.effect, "a": e.target_a, "b": e.target_b,
                      "attributes": e.attributes} for e in effects}
    notes = []
    for w in corrections(spell):
        if w["member"] not in ("TargetA", "TargetB", "Effect"):
            continue
        m = re.fullmatch(r"EFFECT_(\d+)", w["effect"] or "")
        if not m:
            raise FailClosed(f"correction {w} has no effect index")
        idx = int(m.group(1))
        if idx not in rows:
            notes.append(f"{w['consumer']}: {w['member']} on missing EFFECT_{idx} ignored (ApplySpellEffectFix asserts)")
            continue
        if w["member"] == "Effect":
            rows[idx]["effect"] = effect_id(w["value"].removeprefix("SPELL_EFFECT_")) if w["value"] != "SPELL_EFFECT_NONE" else 0
        else:
            v = _target_value(w["value"])
            if v is None:
                raise FailClosed(f"correction {w} target value not parsable")
            rows[idx]["a" if w["member"] == "TargetA" else "b"] = v
        notes.append(f"{w['consumer']}: EFFECT_{idx} {w['member']} = {w['value']}")
    from .selectors import info
    for r in rows.values():
        if r["effect"] and r["effect"] in AREA_AURA_EFFECTS and (info(r["a"]).is_area or info(r["b"]).is_area):
            notes.append(f"SpellMgr.cpp:5286: EFFECT_{r['index']} area-aura targeting area ({r['a']},{r['b']}) -> (1,0)")
            r["a"], r["b"] = 1, 0
    return [rows[k] for k in sorted(rows)], notes


TARGET_CAPS = {  # SpellMgr.cpp:5407-5555 _LoadSqrtTargetLimit(maxTargets, numNonDiminishedTargets, ...)
    198030: 5, 453035: 8, 258860: 8, 258926: 5, 390137: 5, 53385: 5, 404358: 5, 157997: 8, 400254: 5,
    212680: 5, 115310: 5, 388615: 5, 121253: 5, 385060: 8, 385061: 8, 385062: 8, 205472: 8, 2120: 8,
    1254851: 8, 351140: 8, 199667: 5, 44949: 5, 199852: 5, 199851: 5, 307046: 5, 389860: 5, 400370: 5,
    435222: 5, 1265579: 5, 1265580: 5, 1265581: 5, 1265582: 5, 1225827: 5, 1279200: 5,
}


def target_caps_from_source() -> dict[int, int]:
    """Parse ``LoadSpellInfoTargetCaps`` (anchor for :data:`TARGET_CAPS`)."""
    text = _tc_file("Spells/SpellMgr.cpp").read_text(encoding="utf-8")
    start = text.index("void SpellMgr::LoadSpellInfoTargetCaps()")
    end = text.index("void SpellMgr::LoadPetFamilySpellsStore()")
    out = {}
    for ids, n in re.findall(r"ApplySpellFix\(\{\s*([\d,\s]+)\}.*?_LoadSqrtTargetLimit\((\d+),", text[start:end], re.S):
        for s in ids.split(","):
            out[int(s)] = int(n)
    return out


def summarize_tokens(tokens: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    out = {}
    for tok, rows in tokens.items():
        out[tok] = {"stages": sorted({r["stage"] for r in rows}, key=STAGE_ORDER.index),
                    "consumers": sorted({f"{r['function']} @ {r['consumer']}" for r in rows})}
    return out


def stage_counts() -> Counter:
    return Counter(r["stage"] for r in scan_consumers())


# -- corpus -----------------------------------------------------------------------------------
PICKPOCKET_EFFECT = 71  # SPELL_EFFECT_PICKPOCKET (SharedDefines.h) -> CU_PICKPOCKET (SpellMgr.cpp:3119-3120)


def custom_attributes(ctx, spell: int) -> int:
    """AttributesCu bits targeting reads: world-DB ``spell_custom_attr`` + code-set bits.

    Mirrors: SpellMgr.cpp:2981 (DB), 3119-3120 (PICKPOCKET), 3244-3245 (CONE_LINE from Width).
    """
    info = ctx.catalog.get(spell)
    cu = info.attributes_cu if info else 0
    restr = ctx.data.restrictions(spell)
    if restr and float(restr["Width"]) != 0.0:  # G3D::fuzzyNe(Width, 0) -- epsilon irrelevant for DB2 floats seen
        cu |= CUSTOM_ATTRIBUTES["SPELL_ATTR0_CU_CONE_LINE"]
    if any(e.effect == PICKPOCKET_EFFECT for e in ctx.data.effects(spell)):
        cu |= CUSTOM_ATTRIBUTES["SPELL_ATTR0_CU_PICKPOCKET"]
    return cu


def _witnesses(ctx, spells: list[int], n: int = 5) -> list[int]:
    good = [s for s in sorted(spells) if not ctx.is_skew(s)]
    return (good or sorted(spells))[:n]


def build_corpus(ctx, census: dict[str, Any]) -> dict[str, Any]:
    from procs.enums import attr as attr_key
    from .selectors import effect_rows
    reach = sorted(ctx.scope.reach)
    rows = census["_rows"]
    tokens = consumers_by_token()
    summary = summarize_tokens(tokens)

    # spell attribute bits
    bits = []
    for tok, s in sorted(summary.items()):
        if tok.startswith("SpellEffectAttributes::"):
            continue
        if set(s["stages"]) == {"load-rewrite"}:
            continue  # only written by per-spell corrections; listed under corrections
        if "_CU_" in tok:
            mask = CUSTOM_ATTRIBUTES.get(tok)
            if mask is None:
                raise FailClosed(f"custom attribute {tok} read by targeting but not in CUSTOM_ATTRIBUTES")
            spells = [sp for sp in reach if custom_attributes(ctx, sp) & mask]
            source = "AttributesCu (spell_custom_attr + SpellMgr code)"
        else:
            word, bit = attr_key(tok)
            spells = []
            for sp in reach:
                m = ctx.data.misc(sp)
                if m and int(m[f"Attributes_{word}"]) & 0xFFFFFFFF & bit:
                    spells.append(sp)
            source = f"SpellMisc.Attributes_{word} & 0x{bit:08X}"
        bits.append({"attribute": tok, "source": source, "stages": s["stages"],
                     "consumers": [c for c in s["consumers"] if not c.startswith("SpellMgr::")],
                     "player_spells": len(spells), "build_skew_spells": sum(ctx.is_skew(x) for x in spells),
                     "witnesses": _witnesses(ctx, spells), "evidence": "trinity-consumer"})

    # effect attributes
    eff_attr = []
    for name, mask in EFFECT_ATTRIBUTES.items():
        hit = [r for r in rows if r["attributes"] & mask]
        tok = f"SpellEffectAttributes::{name}"
        s = summary.get(tok)
        eff_attr.append({"attribute": name, "mask": f"0x{mask:08X}", "player_effects": len(hit),
                         "player_spells": len({r["spell"] for r in hit}),
                         "trinity_nyi": name in EFFECT_ATTRIBUTES_NYI,
                         "targeting_stages": s["stages"] if s else [],
                         "targeting_consumers": s["consumers"] if s else [],
                         "witnesses": [f"{r['spell']}:{r['effect']}" for r in hit if not r["build_skew"]][:5],
                         "evidence": "trinity-consumer" if s else ("unresolved" if name in EFFECT_ATTRIBUTES_TARGETING_NYI
                                                                   else "n/a")})
    known = 0
    for m in EFFECT_ATTRIBUTES.values():
        known |= m
    unknown_eff_bits = sorted({f"0x{r['attributes'] & ~known & 0xFFFFFFFF:08X}" for r in rows if r["attributes"] & ~known})

    # restriction columns
    def col_count(getter, pred) -> tuple[int, list[int]]:
        spells = [sp for sp in reach if (row := getter(sp)) is not None and pred(row)]
        return len(spells), _witnesses(ctx, spells)

    col_rows = []
    specs = {
        "SpellTargetRestrictions.ConeDegrees": (ctx.data.restrictions, lambda r: float(r["ConeDegrees"]) != 0.0),
        "SpellTargetRestrictions.Width": (ctx.data.restrictions, lambda r: float(r["Width"]) != 0.0),
        "SpellTargetRestrictions.MaxTargets": (ctx.data.restrictions, lambda r: int(r["MaxTargets"]) != 0),
        "SpellTargetRestrictions.Targets": (ctx.data.restrictions, lambda r: int(r["Targets"]) != 0),
        "SpellTargetRestrictions.TargetCreatureType": (ctx.data.restrictions, lambda r: int(r["TargetCreatureType"]) != 0),
        "SpellTargetRestrictions.MaxTargetLevel": (ctx.data.restrictions, lambda r: int(r["MaxTargetLevel"]) != 0),
        "SpellAuraRestrictions.TargetAuraState": (ctx.data.aura_restrictions, lambda r: int(r["TargetAuraState"]) != 0),
        "SpellAuraRestrictions.ExcludeTargetAuraState": (ctx.data.aura_restrictions, lambda r: int(r["ExcludeTargetAuraState"]) != 0),
        "SpellAuraRestrictions.TargetAuraSpell": (ctx.data.aura_restrictions, lambda r: int(r["TargetAuraSpell"]) != 0),
        "SpellAuraRestrictions.ExcludeTargetAuraSpell": (ctx.data.aura_restrictions, lambda r: int(r["ExcludeTargetAuraSpell"]) != 0),
        "SpellAuraRestrictions.TargetAuraType": (ctx.data.aura_restrictions, lambda r: int(r["TargetAuraType"]) != 0),
        "SpellAuraRestrictions.ExcludeTargetAuraType": (ctx.data.aura_restrictions, lambda r: int(r["ExcludeTargetAuraType"]) != 0),
        "SpellCastingRequirements.FacingCasterFlags": (ctx.data.casting_requirements, lambda r: int(r["FacingCasterFlags"]) != 0),
    }
    for col, (getter, pred) in specs.items():
        n, w = col_count(getter, pred)
        col_rows.append({"column": col, "player_spells_nonzero": n, "witnesses": w, "consumers": COLUMN_CONSUMERS[col],
                         "evidence": "trinity-consumer" if not col.endswith("MaxTargetLevel") else "unresolved"})
    targets_flags = Counter()
    for sp in reach:
        r = ctx.data.restrictions(sp)
        if r and int(r["Targets"]):
            targets_flags[f"0x{int(r['Targets']) & 0xFFFFFFFF:08X}"] += 1
    facing = Counter()
    for sp in reach:
        r = ctx.data.casting_requirements(sp)
        if r and int(r["FacingCasterFlags"]):
            facing[str(int(r["FacingCasterFlags"]))] += 1

    # dead-target policy
    dead = Counter()
    for sp in reach:
        m = ctx.data.misc(sp)
        r = ctx.data.restrictions(sp)
        allow = bool(m and int(m["Attributes_2"]) & attr_key("SPELL_ATTR2_ALLOW_DEAD_TARGET")[1])
        tflags = int(r["Targets"]) if r else 0
        allow_flags = bool(tflags & (0x8000 | 0x200 | 0x400))
        corpse_sel = any(e.is_effect and ("CORPSE" in (_sel_obj(e.target_a), _sel_obj(e.target_b)))
                         for e in ctx.data.effects(sp))
        ghosts = bool(m and int(m["Attributes_3"]) & attr_key("SPELL_ATTR3_ONLY_ON_GHOSTS")[1])
        key = ("allow-dead" if (allow or allow_flags or corpse_sel) else "alive-only") + ("+ghosts-only" if ghosts else "")
        dead[key] += 1

    # corrections
    player_corr = []
    for sp in reach:
        for w in corrections(sp):
            player_corr.append({"spell": sp, **w})
    caps = {str(s): n for s, n in sorted(TARGET_CAPS.items())}
    return {
        "stages": list(STAGE_ORDER),
        "stage_functions": {k: v for k, v in sorted(STAGES.items())},
        "consumer_scan": {"sources": [f"{GAME}/{s}" for s in SOURCES], "regions": REGIONS,
                          "token_reads": len(scan_consumers()), "by_stage": dict(sorted(stage_counts().items())),
                          "rows": scan_consumers()},
        "spell_attributes": bits,
        "effect_attributes": eff_attr,
        "effect_attribute_unknown_bits_in_player_data": unknown_eff_bits,
        "restriction_columns": col_rows,
        "targets_flags_values": dict(sorted(targets_flags.items())),
        "facing_caster_flags_values": dict(sorted(facing.items())),
        "dead_target_policy": {"consumer": "SpellInfo.cpp:1838 IsAllowingDeadTarget via CheckTarget SpellInfo.cpp:2452; "
                                           "ghosts SpellInfo.cpp:2368 (ONLY_ON_GHOSTS == target HasAuraType(GHOST))",
                               "player_spells": dict(sorted(dead.items())), "evidence": "trinity-consumer"},
        "shapeshift": {"consumer": "SpellInfo.cpp:2111 CheckShapeshift(form) -- caster form only",
                       "target_side_consumer": None, "evidence": "trinity-consumer"},
        "load_corrections_player": sorted(player_corr, key=lambda r: (r["spell"], r["consumer"])),
        "load_generic_rules": [
            {"rule": "cone effect with ConeAngle fuzzyEq 0 -> 90", "consumer": "SpellMgr.cpp:5281-5283"},
            {"rule": "area-aura effect with IsArea A/B -> (TARGET_UNIT_CASTER, 0)", "consumer": "SpellMgr.cpp:5286-5291"},
            {"rule": "SPELL_ATTR5_LIMIT_N and MaxAffectedTargets 0 -> 1", "consumer": "SpellMgr.cpp:5304-5305"},
            {"rule": "Width != 0 -> SPELL_ATTR0_CU_CONE_LINE", "consumer": "SpellMgr.cpp:3244-3245"},
            {"rule": "TRAJ trigger spell range raised to the main spell's", "consumer": "SpellMgr.cpp:5251-5263"},
            {"rule": "load order: Corrections -> CustomAttributes (explicit mask) -> ... -> TargetCaps",
             "consumer": "World.cpp:1369-1390"},
        ],
        "target_caps": {"consumer": "SpellMgr.cpp:5407 LoadSpellInfoTargetCaps (_LoadSqrtTargetLimit max targets)",
                        "spells": caps,
                        "player_spells": sorted(s for s in TARGET_CAPS if s in ctx.scope.reach),
                        "note": "sqrt damage scaling / cap semantics belong to Track C (caps.json)"},
    }


def _sel_obj(t: int) -> str:
    from .selectors import TOTAL_SPELL_TARGETS, info
    return info(t).object if 0 <= t < TOTAL_SPELL_TARGETS else "?"
