"""Track J command: ``aura-targets`` (aura-side recipient selection).

    cd scripts/research
    python3 targeting.py aura-targets --out ../../docs/research/targeting-corpora/aura-targets.json

Census and per-effect classification of every current-player effect whose recipients are chosen by an
aura target map (``Aura::UpdateTargetMap``): SPELL_EFFECT_PERSISTENT_AREA_AURA (27), the area-aura effects
(35, 65, 119, 128, 129, 143, 202, 271) and APPLY_AURA_ON_PET (174).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import PINS
from . import auratargets as J
from .cli import emit

CMD = "python3 targeting.py aura-targets --out ../../docs/research/targeting-corpora/aura-targets.json"

SEARCH_TAGS = {35: ["party", "area"], 271: ["party", "area"], 65: ["raid", "area"], 128: ["ally", "area"],
               129: ["enemy", "area"], 202: ["summons", "area"], 119: ["pet", "owner"], 143: ["owner"],
               174: ["pet"], 27: ["area", "dynobj", "world-geometry"]}

TC = "src/server/game/"
FINDINGS = [
    {"id": "TG-J-F01", "evidence": "trinity-consumer",
     "consumer": TC + "Spells/Spell.cpp:3226-3305; Spells/Auras/SpellAuras.cpp:2650-2662; Spells/SpellEffects.cpp:127,157,211,220,221,235,294,363",
     "claim": "Area-aura effects never reach a unit through the spell hit: their hit handler is EffectUnused and "
              "AddStaticApplication drops every non-APPLY_AURA bit. The aura is created on each unit hit by any "
              "unit-owned aura effect; every recipient of 35/65/119/128/129/143/202/271 and 174, the owner included, "
              "comes from UnitAura::FillTargetMap."},
    {"id": "TG-J-F02", "evidence": "trinity-consumer",
     "consumer": TC + "Spells/Spell.cpp:3241-3243; Spells/Auras/SpellAuras.cpp:357, 2511",
     "claim": "The aura carries all unit-owned aura effects of the spell (BuildEffectMaskForOwner(MAX_EFFECT_MASK)), "
              "whichever effect hit the owner, so every area-aura effect searches around every owner (e.g. Deathmark "
              "360194:1 searches around the enemy target; 440040:8 around the caster and each of its summons)."},
    {"id": "TG-J-F03", "evidence": "trinity-probe",
     "consumer": TC + "Spells/Auras/SpellAuras.cpp:2587-2646",
     "claim": "Per-type switch: PARTY/PARTY_NONRANDOM -> TARGET_CHECK_PARTY, RAID -> RAID, FRIEND -> ALLY, ENEMY -> "
              "ENEMY (+EXTRA_CELL_SEARCH_RADIUS 40), SUMMONS -> owner + SUMMONED, PET -> owner + (fallthrough) OWNER, "
              "OWNER -> GetCharmerOrOwner if in world, same phase and IsInRange3d(Position*) (aura owner's combat reach "
              "only), APPLY_AURA_ON_PET -> the owner's pet by GUID in the owner's map (no range, phase or alive gate); "
              "plain APPLY_AURA and all other types: no search."},
    {"id": "TG-J-F04", "evidence": "trinity-probe",
     "consumer": TC + "Spells/Auras/SpellAuras.cpp:2546-2548, 2581, 2634-2638; Spells/Spell.cpp:9418-9452 (9430); Entities/Object/Object.cpp:617",
     "claim": "Search = WorldObjectSpellAreaTargetCheck(radius=CalcRadius(ref) [TargetA entry], position=aura owner, "
              "caster=ref, referer=aura owner) with ref = aura caster, else the owner; AlwaysVisible phase shift; no LOS "
              "check. Accept = 2D distance < Max + target combat reach (strict) and |dz| <= Max (no reach), "
              "AoETarget immunity, CheckTarget(implicit), relation check with PARTY/RAID membership against the owner."},
    {"id": "TG-J-F05", "evidence": "trinity-probe",
     "consumer": TC + "Spells/Auras/SpellAuras.cpp:2709-2737; Spells/SpellEffects.cpp:1533-1538",
     "claim": "DynObjAura: check type = TargetA's, or TargetB's when TargetB references DEST (Rain of Fire 5740:0 / "
              "1214467:0 -> ENEMY from TARGET_DEST_DYNOBJ_ENEMY); caster = referer = the dynobj caster; radius {0, "
              "dynobj Radius}, where Radius = max CalcRadius(caster).Max over PAA effects at creation (the caster's "
              "movement bonus at cast time is baked in); searcher phase = the dynobj's; no GetSearcherTypeMask."},
    {"id": "TG-J-F06", "evidence": "trinity-probe",
     "consumer": TC + "Spells/Auras/SpellAuras.cpp:658-792, 839-842, 480; Spells/Auras/SpellAuras.h:58",
     "claim": "Update: the interval starts at 0 (the first owner update refreshes at once), then refreshes when "
              "interval <= diff and resets to 500 ms. Units missing from the refreshed map are unapplied; existing "
              "units failing IsImmunedToSpell(purge=true)/CanBeAppliedOn are unapplied; new units need a non-empty "
              "mask after effect immunity, no spell immunity, CanBeAppliedOn, IsHighestExclusiveAura, (dynobj) not "
              "in flight and (non-owner) CanStackWith against every applied aura."},
    {"id": "TG-J-F07", "evidence": "trinity-consumer",
     "consumer": TC + "Spells/SpellMgr.cpp:5285-5290; Spells/SpellInfo.cpp:480-497",
     "claim": "LoadSpellInfoCorrections rewrites only IsAreaAuraEffect effects whose TargetA/B is an area selector to "
              "(TARGET_UNIT_CASTER, 0); APPLY_AURA_ON_PET (174) is not an area aura effect (440040:8 keeps "
              "TARGET_UNIT_CASTER_AND_SUMMONS) and TARGET_DEST_DYNOBJ_ALLY (740:1) is not an area selector. The client "
              "TargetA/B of a unit area aura is read for recipients only through the owner (spell selection) and the "
              "TargetA radius entry; DynObjAura reads the TargetA/B check type."},
    {"id": "TG-J-F08", "evidence": "trinity-consumer",
     "consumer": TC + "Spells/SpellInfo.cpp:2443-2450, 2482",
     "claim": "The implicit CheckTarget inside the area check already rejects in-flight units and applies "
              "ONLY_ON_PLAYER / NOT_ON_PLAYER, so the dynobj in-flight filter (SpellAuras.cpp:725) only adds to it for "
              "CU_ALLOW_INFLIGHT_TARGET spells and the missing dynobj GetSearcherTypeMask only loses the per-effect "
              "PlayersOnly attribute."},
    {"id": "TG-J-F09", "evidence": "trinity-probe",
     "consumer": TC + "Spells/SpellInfo.cpp:783-798",
     "claim": "RadiusIndex 0 -> CalcRadius returns {0,0} before the level/mod/movement adjustments; the PARTY search "
              "then still accepts units whose centre lies within their own combat reach of the owner at exactly equal "
              "z (the owner itself always: distance 0 < reach)."},
    {"id": "TG-J-F10", "evidence": "trinity-probe",
     "consumer": TC + "Spells/Auras/SpellAuras.cpp:684-701, 717-749, 762-770",
     "claim": "Probe-confirmed update quirks: an existing application whose effects all became immune stays applied "
              "with mask 0; an existing application whose mask changed but fails IsHighestExclusiveAura or "
              "CanStackWith is left unchanged (neither updated nor removed); existing applications never reach "
              "_ApplyAura in UpdateTargetMap (UpdateApplyEffectMask applies the delta)."},
]

DEFECTS = [
    {"id": "TG-J-D01",
     "file_line": TC + "Spells/Auras/SpellAuras.cpp:2599, 2638, 2730; Grids/Cells/CellImpl.h:33-44, 53",
     "description": "Only AREA_AURA_ENEMY widens the cell search by EXTRA_CELL_SEARCH_RADIUS; PARTY/RAID/FRIEND/SUMMONS "
                    "searches visit cells for radius.Max and DynObjAura for Radius + 0 (WorldObject combat reach), while "
                    "the accept predicate extends by the target's combat reach (Spell.cpp:9430). Spell-side area searches "
                    "add the extra radius for every check (Spell.cpp:1300, 2215).",
     "effect_on_recipients": "a unit between Max and Max + reach whose grid cell lies outside the visited rectangle is "
                             "not selected, depending on where cell boundaries fall",
     "oracle_behaviour": "not modelled: the fixture visit_order is authoritative and must omit such units (TG-J-01)"},
    {"id": "TG-J-D02",
     "file_line": TC + "Spells/Auras/SpellAuras.cpp:2581; Spells/SpellInfo.cpp:810-818; Spells/SpellEffects.cpp:1537",
     "description": "Aura maps call CalcRadius(ref) every 500 ms, so the movement leeway meant for casting "
                    "(CanIncreaseRangeByMovement: Min-2, Max+2) resizes a persistent aura whenever the aura caster moves, even "
                    "when the search is centred on another unit; the caster's level and radius mods apply to auras owned "
                    "by other units. Persistent area auras freeze the value at creation.",
     "effect_on_recipients": "raid/party aura radius oscillates by 2 yd with the caster's movement state",
     "oracle_behaviour": "reproduced (fixture fact range_movement_bonus on the aura caster)"},
    {"id": "TG-J-D03",
     "file_line": TC + "Spells/Auras/SpellAuras.cpp:2587-2589, 2636; Spells/Spell.cpp:9341-9350",
     "description": "An APPLY_AREA_AURA_PARTY effect on an aura owned by a hostile unit (Deathmark 360194:1, "
                    "SPELL_AURA_MOD_SPELL_DAMAGE_FROM_CASTER, TargetA TARGET_UNIT_TARGET_ENEMY) checks "
                    "caster->IsValidAssistTarget and owner->IsInPartyWith for every candidate, the owner included, so "
                    "the effect never applies to anyone.",
     "effect_on_recipients": "Deathmark's damage-from-caster amplification is never applied in Trinity",
     "oracle_behaviour": "reproduced (empty recipient set); likely defect by structural inference (the aura type "
                         "only makes sense on the target)"},
    {"id": "TG-J-D04",
     "file_line": TC + "Spells/Auras/SpellAuras.cpp:691-697, 762-765",
     "description": "An existing application whose remaining effects all become immune is not removed: its mask "
                    "is updated to 0 and it stays in m_applications (new applications with mask 0 are refused at 717).",
     "effect_on_recipients": "the unit keeps an empty application of the area aura until it leaves the map",
     "oracle_behaviour": "reproduced (update with an empty index list); probe-confirmed"},
    {"id": "TG-J-D05",
     "file_line": TC + "Spells/Auras/SpellAuras.cpp:2728-2730 vs 2634",
     "description": "DynObjAura::FillTargetMap never calls Spell::GetSearcherTypeMask, so an effect's PlayersOnly "
                    "attribute (and a condition list's searcher mask) is ignored for persistent area auras.",
     "effect_on_recipients": "latent: no current-player PERSISTENT_AREA_AURA effect has PlayersOnly or conditions",
     "oracle_behaviour": "reproduced (mask not applied); probe-confirmed"},
]

UNKNOWNS = [
    {"id": "TG-J-01", "subject": "all aura-target-map effects", "evidence": "trinity-consumer",
     "known": "the recipient set given the grid visit list; the map is a std::unordered_map",
     "unknown": "which units the grid visit reaches near cell boundaries (TG-J-D01); application order",
     "why_unresolved": "cell layout / unordered_map iteration are map-internal and implementation-defined",
     "reopen_condition": "a fixture states cell membership, or a consumer of application order is found",
     "build_skew": "n/a"},
    {"id": "TG-J-02", "subject": "area-search auras with RadiusIndex 0 (effect_classes tag radius-zero)",
     "evidence": "trinity-probe", "known": "Trinity: radius {0,0}; owner plus units within their own reach at equal z",
     "unknown": "Retail recipients (owner only, whole party, or reach-based)",
     "why_unresolved": "no Retail observation", "reopen_condition": "RE-J-02 result", "build_skew": False},
    {"id": "TG-J-03", "subject": "360194:1 Deathmark", "evidence": "structural-inference",
     "known": "Trinity never applies the effect (TG-J-D03)", "unknown": "Retail recipient (target expected)",
     "why_unresolved": "no Retail observation; no Trinity script", "reopen_condition": "RE-J-03 result",
     "build_skew": False},
    {"id": "TG-J-04", "subject": "unit area auras with a radius entry", "evidence": "trinity-consumer",
     "known": "Trinity adds the caster movement leeway to aura radii (TG-J-D02)",
     "unknown": "whether Retail aura radii change while the caster moves", "why_unresolved": "no Retail observation",
     "reopen_condition": "RE-J-04 result", "build_skew": False},
    {"id": "TG-J-05", "subject": "1291885:2 Soulcoiler Ritual Vessel", "evidence": "build-skew",
     "known": "RAID area aura in 12.1 data", "unknown": "whether Trinity (<= 12.0.7) loads the spell as authored",
     "why_unresolved": "spell newer than the pinned Trinity build", "reopen_condition": "Trinity supports 12.1",
     "build_skew": True},
    {"id": "TG-J-06", "subject": "area auras of spells with a unit-owned aura effect on a non-unit selector "
                                 "(effect_classes tag owner-explicit-or-caster)", "evidence": "trinity-consumer",
     "known": "the owner comes from EFFECT_IMPLICIT_TARGET_EXPLICIT (explicit unit, else caster; Spell.cpp:2080) or "
              "from the spell's other aura effects",
     "unknown": "Retail owner when an explicit friendly target is passed", "why_unresolved": "no Retail observation",
     "reopen_condition": "RE-J-05 result", "build_skew": False},
    {"id": "TG-J-07", "subject": "all aura-target-map effects", "evidence": "trinity-consumer",
     "known": "Trinity refreshes every 500 ms of owner updates, removal at the next refresh",
     "unknown": "Retail refresh cadence and removal latency", "why_unresolved": "no Retail observation",
     "reopen_condition": "RE-J-01 timing result", "build_skew": False},
    {"id": "TG-J-08", "subject": "440040:8 APPLY_AURA_ON_PET on TARGET_UNIT_CASTER_AND_SUMMONS", "evidence": "trinity-consumer",
     "known": "each hit unit (caster and summons) owns an aura; each applies eff 5/8 to its own pet only",
     "unknown": "Retail meaning of 174 on summons", "why_unresolved": "no Retail observation",
     "reopen_condition": "a consumer or Retail observation of Xalan's Cruelty on summons", "build_skew": False},
]

RETAIL = [
    {"id": "RE-J-01", "question": "raid/party aura edge and refresh timing",
     "model_a": "Trinity: in iff 2D distance < R + target reach and |dz| <= R; removed at the next 500 ms refresh",
     "model_b": "plain R sphere, or immediate removal",
     "setup": "Devotion Aura 465; party member steps across 40..41.5 yd on flat ground, then onto a ledge > 40 yd above",
     "observable": "UNIT_AURA / COMBAT_LOG SPELL_AURA_APPLIED/REMOVED timestamps vs position", "fidelity": "approximate"},
    {"id": "RE-J-02", "question": "RadiusIndex-0 party auras",
     "model_a": "Trinity: owner plus units within their own reach at equal z",
     "model_b": "owner only / whole party",
     "setup": "Forestwalk 400129 or Blessing of Dawn 183416; party member stacked on the owner and 3 yd away",
     "observable": "aura presence on the party member (UnitAura)", "fidelity": "exact"},
    {"id": "RE-J-03", "question": "Deathmark 360194 effect 1 recipient",
     "model_a": "Trinity: nobody", "model_b": "the Deathmark target",
     "setup": "cast Deathmark on a dummy; inspect the target's debuff effect points / damage taken from the rogue",
     "observable": "UnitAura points on the target; combat log damage delta", "fidelity": "approximate"},
    {"id": "RE-J-04", "question": "does a moving caster enlarge its aura radius",
     "model_a": "Trinity: +2 yd while the caster moves", "model_b": "fixed radius",
     "setup": "paladin with Devotion Aura strafes while a party member stands at 41 yd",
     "observable": "aura presence toggling with caster movement", "fidelity": "approximate"},
    {"id": "RE-J-05", "question": "owner of a (29,0) / (0,0) area aura cast with a friendly target",
     "model_a": "Trinity: explicit unit (EFFECT_IMPLICIT_TARGET_EXPLICIT) or caster",
     "model_b": "always the caster",
     "setup": "Xathuux's Last Roar 1254180 or Tranquility 740 with a friendly target selected",
     "observable": "which unit's surroundings receive the aura", "fidelity": "approximate"},
    {"id": "RE-J-06", "question": "raid aura on other subgroups and on party pets",
     "model_a": "Trinity: RAID includes all subgroups and pets of raid members; PARTY includes party pets",
     "model_b": "players of the own subgroup only",
     "setup": "Devotion Aura in a 2-subgroup raid with a hunter pet", "observable": "aura presence on pet / other subgroup",
     "fidelity": "exact"},
]


def classify(row: dict[str, Any]) -> dict[str, Any]:
    t = row["type"]
    tags = ["aura-target-map", *SEARCH_TAGS[t]]
    unknowns = ["TG-J-01", "TG-J-07"]
    defects: list[str] = []
    cls = "fixture-dependent"
    carriers = {tuple(c) for c in row["owner_carriers"]}
    if t in J.SELECTION or t == 27:
        defects.append("TG-J-D01")
    if t in J.SELECTION and row["radius_index"][0]:
        defects.append("TG-J-D02")
        unknowns.append("TG-J-04")
    if t in J.SELECTION and not row["radius_index"][0]:
        tags.append("radius-zero")
        unknowns.append("TG-J-02")
    if t == 27:
        defects.append("TG-J-D05")
    if row["effect_attributes"] & 0x4000:
        tags.append("players-only")
    if any(c[0] == "TARGET_UNIT_TARGET_ENEMY" for c in carriers):
        tags.append("hostile-owner")
        if t in (35, 271):
            cls = "understood-with-defect"
            defects.append("TG-J-D03")
            unknowns.append("TG-J-03")
    if any(c[0] == "TARGET_UNIT_CASTER_AND_SUMMONS" for c in carriers):
        tags += ["controlled-unit", "summons"]
        unknowns.append("TG-J-08")
    if any(c[0] in ("NONE", "TARGET_DEST_DYNOBJ_ALLY") for c in carriers) and t != 27:
        tags.append("owner-explicit-or-caster")
        unknowns.append("TG-J-06")
    if t == 174 and carriers <= {("TARGET_UNIT_CASTER", "NONE")}:
        cls = "understood"
        unknowns.remove("TG-J-01")
    if row["build_skew"]:
        unknowns.append("TG-J-05")
    return {"class": cls, "tags": sorted(set(tags)), "unknowns": sorted(set(unknowns)), "defects": sorted(set(defects)),
            "build_skew": row["build_skew"], "type": row["type_name"],
            "selection": J.SELECTION.get(t, {27: "TargetA/B check", 119: "owner+master", 143: "master",
                                             174: "pet"}.get(t)),
            "owner": "dynobj at the dest" if t == 27 else "every unit hit by a unit-owned aura effect of the spell"}


def build() -> dict[str, Any]:
    c = J.census()
    rows = c["rows"]
    classes = {f"{r['spell']}:{r['effect']}": classify(r) for r in rows}
    witnesses = []
    for path in J.witness_paths():
        res = J.evaluate_witness(path)
        witnesses.append({"file": str(path.relative_to(path.parents[3])), "spell": res["spell"], "effect": res["effect"],
                          "owner": res["owner"], "recipients": res["recipients"], "ok": res["ok"]})
    zero = sorted(v for k, v in c["all_types"].items() if v not in c["by_type"])
    unknowns = []
    for u in UNKNOWNS:
        members = sorted((k for k, v in classes.items() if u["id"] in v["unknowns"]),
                         key=lambda k: tuple(map(int, k.split(":"))))
        unknowns.append({**u, "effects": members})
    return {
        "provenance": {"pins": PINS, "command": CMD,
                       "sources": ["data/tables SpellEffect/SpellRadius/SpellMisc (targeting.data)",
                                   "docs/research/dummy-corpora/corrections.json (LoadSpellInfoCorrections)",
                                   "TrinityCore SpellAuras.cpp / Spell.cpp / SpellInfo.cpp / SpellEffects.cpp (hand-read)",
                                   "tools/tc_target_auramap_probe (verbatim differential, tests/test_tg_j_probe.py)"],
                       "scope": "ctx.scope.reach, IsEffect, DIFFICULTY_NONE"},
        "summary": {"effects": len(rows), "spells": len({r["spell"] for r in rows}), "by_type": c["by_type"],
                    "types_without_current_player_effects": zero,
                    "by_class": dict(sorted(Counter(v["class"] for v in classes.values()).items())),
                    "build_skew": sum(1 for v in classes.values() if v["build_skew"]),
                    "specs_with_effects": len(c["per_spec"])},
        "findings": FINDINGS,
        "census": {"rows": sorted(rows, key=lambda r: (r["spell"], r["effect"])), "per_spec": c["per_spec"],
                   "effect_types": c["all_types"]},
        "effect_classes": dict(sorted(classes.items(), key=lambda kv: tuple(map(int, kv[0].split(":"))))),
        "witnesses": witnesses,
        "oracle": {"module": "scripts/research/targeting/auratargets.py",
                   "entry_points": ["fill_target_map(world, sv, aura_owner_id, effect, trace)", "target_map",
                                    "update_target_map", "recipients", "evaluate_witness(path_or_doc)", "next_update",
                                    "dynobj_radius", "census"],
                   "fixture_contract": "targeting-fixture/1 + `aura` block {owner, caster, static_applications, applications}",
                   "witness_schema": J.WITNESS_SCHEMA},
        "trinity_defects": DEFECTS,
        "unknowns": unknowns,
        "retail_experiments": RETAIL,
    }


def _add(p) -> None:
    p.add_argument("--out", help="write JSON here instead of stdout")


def _run(args) -> int:
    emit(build(), args.out)
    return 0


COMMANDS = {"aura-targets": ("aura target maps (area auras / persistent area auras): census + classes", _add, _run)}
