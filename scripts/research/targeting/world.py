"""World-database target policy (track E).

Which rows of the pinned world database (``dummy-corpora/trinity-server-overlay.json``,
TDB 1200.26021 + 522 updates) change how a current-player spell chooses recipients,
and an evaluator for **exactly** the condition types those rows use.

Consumers (pinned 7f3d43b):

* ``CONDITION_SOURCE_TYPE_SPELL_IMPLICIT_TARGET`` (13): ``ConditionMgr::isSourceTypeValid``
  (ConditionMgr.cpp:1851-1924: effects without NEARBY/CONE/AREA/TRAJ/LINE selectors, chain
  targets or area-aura effects are stripped from SourceGroup) ->
  ``addToSpellImplicitTargetConditions`` (ConditionMgr.cpp:1575-1682: shared per-effect lists)
  -> ``WorldObjectSpellTargetCheck::operator()`` (Spell.cpp:9392-9395, targets
  ``[candidate, caster]``) and ``Spell::GetSearcherTypeMask`` (Spell.cpp:2158 ->
  ``ConditionMgr::GetSearcherTypeMaskForConditionList`` ConditionMgr.cpp:983).
* ``CONDITION_SOURCE_TYPE_SPELL`` (17): ``Spell::CheckCast`` (Spell.cpp:5918-5933, targets
  ``[caster, explicit object]``; failure -> ErrorType, else CASTER_AURASTATE when the failed
  row targets the caster, BAD_TARGETS otherwise).
* ``CONDITION_SOURCE_TYPE_SPELL_CLICK_EVENT`` (18): key is ``{SourceGroup = creature entry,
  SourceEntry = spell}`` (ConditionMgr.cpp:1187).  NOTE: ``dummy_semantics.conditions``
  filters source 18 by SourceGroup as if it were the spell (erratum request in the handoff).
* ``spell_linked_spell``: Spell.cpp:3949 (CAST, target = explicit unit or caster),
  Spell.cpp:3353 (HIT, hit unit casts on itself), SpellAuras.cpp:1407-1447 (AURA / REMOVE).
* ``spell_target_position``: Spell.cpp:1477-1492 (TARGET_DEST_DB), 1157-1172
  (TARGET_DEST_NEARBY_ENTRY_OR_DB).
* TARGET_CHECK_ENTRY without conditions: Spell.cpp:1120-1180 ("emergency case"); the ENTRY
  check itself has no case in WorldObjectSpellTargetCheck (Spell.cpp:9322-9373).
* ``serverside_spell``: ignored when the DB2 has the spell (SpellMgr.cpp:2887).

The evaluator is not a ConditionMgr: it implements ``Condition::Meets`` only for
:data:`SUPPORTED_TYPES` and fails closed for everything else.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable

from . import PINS, FailClosed
from .trace import Trace

SOURCE_IMPLICIT_TARGET, SOURCE_SPELL, SOURCE_SPELL_CLICK, SOURCE_SPELL_PROC = 13, 17, 18, 24
SOURCE_VEHICLE_SPELL, SOURCE_SKILL_LINE_ABILITY = 21, 35
SOURCE_NAMES = {13: "SPELL_IMPLICIT_TARGET", 17: "SPELL", 18: "SPELL_CLICK_EVENT", 24: "SPELL_PROC",
                21: "VEHICLE_SPELL", 35: "SKILL_LINE_ABILITY"}
MAX_EFFECT_MASK = 0xFFFFFFFF  # SharedDefines.h: (1 << MAX_SPELL_EFFECTS) - 1 with MAX_SPELL_EFFECTS 32

CONDITION_NONE, CONDITION_UNIT_STATE = 0, 21
#: Condition types with an evaluator (the only types current-player spell rows use; see census).
SUPPORTED_TYPES = {CONDITION_NONE: "NONE", CONDITION_UNIT_STATE: "UNIT_STATE"}
#: UnitState (Unit.h:260-300) names for the values the rows use.
UNIT_STATE_NAMES = {0x400: "UNIT_STATE_ROOT"}
UNIT_STATE_ALL_STATE_SUPPORTED = 0x3FF7DFFF  # Unit.h:293-299 (every state except ISOLATED_DEPRECATED 0x2000 and FOLLOW_FORMATION 0x80000)

GRID_MAP_TYPE_MASK_CORPSE, GRID_MAP_TYPE_MASK_CREATURE = 0x01, 0x02
GRID_MAP_TYPE_MASK_DYNAMICOBJECT, GRID_MAP_TYPE_MASK_GAMEOBJECT = 0x04, 0x08
GRID_MAP_TYPE_MASK_PLAYER, GRID_MAP_TYPE_MASK_AREATRIGGER = 0x10, 0x20
GRID_MAP_TYPE_MASK_SCENEOBJECT, GRID_MAP_TYPE_MASK_CONVERSATION = 0x40, 0x80
GRID_MAP_TYPE_MASK_ALL = 0xFF

#: Condition::GetSearcherTypeMaskForCondition (ConditionMgr.cpp:696-909) for the supported types.
SEARCHER_MASK = {CONDITION_NONE: GRID_MAP_TYPE_MASK_ALL,
                 CONDITION_UNIT_STATE: GRID_MAP_TYPE_MASK_CREATURE | GRID_MAP_TYPE_MASK_PLAYER}

# selector categories needed by isSourceTypeValid (SpellInfo.cpp _data; same private table as
# dummy_semantics.bindings, whose values track A's targeting.selectors is tested against)
AREA_LIKE = {"NEARBY", "CONE", "AREA", "TRAJ", "LINE"}
AREA_AURA_EFFECT_NAMES = ("PERSISTENT_AREA_AURA", "APPLY_AREA_AURA_PARTY", "APPLY_AREA_AURA_RAID",
                          "APPLY_AREA_AURA_FRIEND", "APPLY_AREA_AURA_ENEMY", "APPLY_AREA_AURA_PET",
                          "APPLY_AREA_AURA_OWNER", "APPLY_AURA_ON_PET", "APPLY_AREA_AURA_SUMMONS",
                          "APPLY_AREA_AURA_PARTY_NONRANDOM")
ENTRY_SELECTORS = {7, 8, 38, 40, 46, 60, 107, 110, 142}   # TARGET_CHECK_ENTRY rows of SpellInfo.cpp _data
DB_SELECTORS = {17: "TARGET_DEST_DB", 106: "TARGET_DEST_NEARBY_DB", 142: "TARGET_DEST_NEARBY_ENTRY_OR_DB"}


def _category(target: int) -> str:
    from dummy_semantics.bindings import _TC_SELECTOR_DATA
    if target not in _TC_SELECTOR_DATA:
        raise FailClosed(f"selector {target} outside SpellImplicitTargetInfo::_data")
    return _TC_SELECTOR_DATA[target][0]


def _i(row: dict[str, Any], key: str) -> int:
    return int(row[key])


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def valid_implicit_target_group(effects: Iterable[Any], source_group: int) -> int:
    """SourceGroup after ``isSourceTypeValid`` for source 13.

    Mirrors: ConditionMgr.cpp:1851-1924.  ``effects`` are DIFFICULTY_NONE effect rows
    (``index, effect, target_a, target_b, chain_targets``).  Returns 0 when the row is dropped.
    """
    from procs.enums import effect as eff
    area_aura = {eff(n) for n in AREA_AURA_EFFECT_NAMES}
    if source_group > MAX_EFFECT_MASK or not source_group:
        return 0
    group = source_group
    for e in effects:
        if not (1 << e.index) & group:
            continue
        if e.chain_targets > 0:
            continue
        if _category(e.target_a) in AREA_LIKE or _category(e.target_b) in AREA_LIKE:
            continue
        if e.effect in area_aura:
            continue
        group &= ~(1 << e.index)
    return group


def implicit_target_lists(effects: list[Any], rows: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """Per-effect ``ImplicitTargetConditions`` after loading ``rows`` (source 13, one spell).

    Mirrors: ConditionMgr.cpp:1575-1682 ``addToSpellImplicitTargetConditions`` (shared lists
    keyed by pointer identity; only the first shared mask overlapping SourceGroup receives the
    row; overlapping-mask rows are ignored).  ``effects`` must be dense (gaps as blank rows).
    Returns effect index -> list (lists shared between effects are the same object).
    """
    lists: dict[int, list[dict[str, Any]] | None] = {e.index: None for e in effects}
    size = max(lists) + 1 if lists else 0
    for row in rows:
        cond_mask = row["_group"]
        if row["ConditionTypeOrReference"] == 51 or row["ConditionTypeOrReference"] == 31:
            raise FailClosed("OBJECT_ENTRY_GUID implicit-target validation (ConditionMgr.cpp:1589-1614) not modelled")
        shared: list[int] = []
        for i in range(size):
            if any(m & (1 << i) for m in shared):
                continue
            m = 1 << i
            for j in range(i + 1, size):
                if lists.get(j) is lists.get(i):
                    m |= 1 << j
            shared.append(m)
        for m in shared:
            common = m & cond_mask
            if not common:
                continue
            first = next(i for i in range(size) if m & (1 << i))
            lst = lists.get(first)
            if lst is not None:
                if cond_mask != m:
                    break  # "effect masks are overlapping" -> ignored (return)
            else:
                lst = []
                assigned = False
                for i in range(first, size):
                    if (1 << i) & common:
                        lists[i] = lst
                        assigned = True
                if not assigned:
                    break
            lst.append(row)
            break
    return {i: l for i, l in lists.items() if l is not None}


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------
def meets(row: dict[str, Any], targets: list[dict[str, Any] | None], trace: Trace | None = None) -> bool:
    """``Condition::Meets`` for :data:`SUPPORTED_TYPES`.

    Mirrors: ConditionMgr.cpp:193-694.  ``targets`` are fixture facts
    ``{"is_unit": bool, "unit_state": int}`` (``None`` = absent object).  A missing object
    for an object-reading type returns **false before** ``NegativeCondition`` is applied
    (ConditionMgr.cpp:281-287).  ``ScriptName`` rows (``OnConditionCheck``) fail closed.
    """
    ctype = _i(row, "ConditionTypeOrReference")
    if ctype not in SUPPORTED_TYPES:
        raise FailClosed(f"condition type {ctype} has no evaluator (track E supports {sorted(SUPPORTED_TYPES)})")
    if row.get("ScriptName"):
        raise FailClosed(f"condition row has ScriptName {row['ScriptName']!r} (ScriptMgr::OnConditionCheck)")
    target_index = _i(row, "ConditionTarget")
    if not 0 <= target_index < 3:
        raise FailClosed("ConditionTarget >= MAX_CONDITION_TARGETS (ASSERT ConditionMgr.cpp:195)")
    if ctype == CONDITION_NONE:
        met = True
    else:
        obj = targets[target_index] if target_index < len(targets) else None
        if obj is None:
            if trace is not None:
                trace.add("condition.missing-object", "ConditionMgr.cpp:281-287", output=False)
            return False
        met = False
        if ctype == CONDITION_UNIT_STATE:           # ConditionMgr.cpp:535-540
            if "is_unit" not in obj:
                raise FailClosed("condition target does not state is_unit")
            if obj["is_unit"]:
                if "unit_state" not in obj:
                    raise FailClosed("UNIT_STATE condition needs the target's unit_state bits")
                met = (int(obj["unit_state"]) & _i(row, "ConditionValue1")) != 0   # Unit::HasUnitState Unit.h:742
    if _i(row, "NegativeCondition"):
        met = not met
    if trace is not None:
        trace.add("condition.meets", "ConditionMgr.cpp:193-694", output=met,
                  inputs={"type": SUPPORTED_TYPES[ctype], "target": target_index, "negative": _i(row, "NegativeCondition")})
    return met


def meets_list(rows: list[dict[str, Any]], targets: list[dict[str, Any] | None],
               trace: Trace | None = None) -> tuple[bool, dict[str, Any] | None]:
    """``ConditionMgr::IsObjectMeetToConditions`` -> (result, last failed row).

    Mirrors: ConditionMgr.cpp:1021-1066, 1080-1087: empty list -> true; ElseGroups are ANDs,
    the list is their OR; a group stops evaluating after its first failure; the *last* failed
    row (``mLastFailedCondition``) is the one reported.  Reference rows fail closed (the
    overlay only carries CONDITION_AURA reference rows).
    """
    if not rows:
        return True, None
    groups: dict[int, bool] = {}
    last_failed = None
    for row in rows:
        g = _i(row, "ElseGroup")
        groups.setdefault(g, True)
        if not groups[g]:
            continue
        if _i(row, "SourceTypeOrReferenceId") < 0 or _i(row, "ConditionTypeOrReference") < 0:
            raise FailClosed("reference conditions are not evaluated (overlay carries only CONDITION_AURA references)")
        obj_missing_before = row
        if not meets(row, targets, trace):
            groups[g] = False
            # mLastFailedCondition is set only when Meets reached the end (object present)
            ctype = _i(row, "ConditionTypeOrReference")
            tgt = _i(row, "ConditionTarget")
            if ctype == CONDITION_NONE or (tgt < len(targets) and targets[tgt] is not None):
                last_failed = obj_missing_before
    result = any(groups[g] for g in sorted(groups))
    return result, (None if result else last_failed)


SPELL_FAILED_CUSTOM_ERROR = 214  # SharedDefines.h:1923
#: SharedDefines.h:1812/1833 SpellCastResult values the rows use (pinned enum, 12.x numbering).
SPELL_CAST_RESULT_NAMES = {103: "SPELL_FAILED_NO_ENDURANCE", 124: "SPELL_FAILED_ROOTED"}


def load_valid(row: dict[str, Any]) -> bool:
    """Load-time validity for the supported types.  Mirrors: ConditionMgr.cpp:2578-2585 (UNIT_STATE)."""
    ctype = _i(row, "ConditionTypeOrReference")
    if ctype == CONDITION_UNIT_STATE:
        return bool(_i(row, "ConditionValue1") & UNIT_STATE_ALL_STATE_SUPPORTED)
    if ctype == CONDITION_NONE:
        return True
    raise FailClosed(f"load validation for condition type {ctype} not modelled")


def cast_check(rows: list[dict[str, Any]], caster: dict[str, Any], explicit: dict[str, Any] | None,
               trace: Trace | None = None) -> str:
    """Source 17 gate in ``Spell::CheckCast``.  Mirrors: Spell.cpp:5917-5933."""
    ok, failed = meets_list([r for r in rows if load_valid(r)], [caster, explicit, None], trace)
    if ok:
        return "SPELL_CAST_OK"
    if failed is not None and int(failed.get("ErrorType") or 0):
        err = int(failed["ErrorType"])
        if err == SPELL_FAILED_CUSTOM_ERROR:
            raise FailClosed("SPELL_FAILED_CUSTOM_ERROR text mapping not modelled")
        return SPELL_CAST_RESULT_NAMES.get(err, f"SpellCastResult({err})")
    if failed is None or not _i(failed, "ConditionTarget"):
        return "SPELL_FAILED_CASTER_AURASTATE"
    return "SPELL_FAILED_BAD_TARGETS"


def implicit_target_check(rows: list[dict[str, Any]], candidate: dict[str, Any], caster: dict[str, Any],
                          trace: Trace | None = None) -> bool:
    """Source 13 per candidate.  Mirrors: Spell.cpp:9296-9299, 9392-9395 (targets [candidate, caster])."""
    return meets_list(rows, [candidate, caster, None], trace)[0]


def searcher_type_mask(rows: list[dict[str, Any]]) -> int:
    """Mirrors: ConditionMgr.cpp:983-1019 (+ per-condition masks 696-909; negative rows -> ALL)."""
    if not rows:
        return GRID_MAP_TYPE_MASK_ALL
    groups: dict[int, int] = {}
    for row in rows:
        g = _i(row, "ElseGroup")
        groups.setdefault(g, GRID_MAP_TYPE_MASK_ALL)
        if not groups[g]:
            continue
        if _i(row, "SourceTypeOrReferenceId") < 0 or _i(row, "ConditionTypeOrReference") < 0:
            raise FailClosed("reference condition searcher mask not modelled")
        if _i(row, "NegativeCondition"):
            m = GRID_MAP_TYPE_MASK_ALL
        else:
            ctype = _i(row, "ConditionTypeOrReference")
            if ctype not in SEARCHER_MASK:
                raise FailClosed(f"searcher mask for condition type {ctype} not modelled")
            m = SEARCHER_MASK[ctype]
        groups[g] &= m
    out = 0
    for m in groups.values():
        out |= m
    return out


# ---------------------------------------------------------------------------
# census / corpus
# ---------------------------------------------------------------------------
def _tiers(ctx):
    from dummy_semantics.bindings import BindingMap

    from . import adapters
    return adapters.compute_tiers(ctx, BindingMap(ctx.bundle))


def _conditions_by_source(ctx) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in ctx.bundle.world.table("conditions").dicts():
        out[int(row["SourceTypeOrReferenceId"])].append(row)
    return out


def _dense_effects(ctx, spell: int) -> list[Any]:
    from types import SimpleNamespace
    rows = {e.index: e for e in ctx.data.effects(spell)}
    size = max(rows) + 1 if rows else 0
    return [rows.get(i) or SimpleNamespace(index=i, effect=0, target_a=0, target_b=0, chain_targets=0)
            for i in range(size)]


def spell_policy(ctx, spell: int, tiers=None, by_source=None) -> dict[str, Any]:
    """Every world-database row that bears on ``spell``'s recipients."""
    b = ctx.bundle
    by_source = by_source if by_source is not None else _conditions_by_source(ctx)
    tiers = tiers or _tiers(ctx)
    out: dict[str, Any] = {"spell": spell, "name": ctx.name(spell), "tier": tiers.tier(spell)}
    effects = _dense_effects(ctx, spell)
    # source 13
    rows13 = [dict(r) for r in by_source.get(SOURCE_IMPLICIT_TARGET, []) if int(r["SourceEntry"]) == spell]
    if rows13:
        kept = []
        for r in rows13:
            r["_group"] = valid_implicit_target_group(effects, int(r["SourceGroup"]))
            if r["_group"]:
                kept.append(r)
        lists = implicit_target_lists(effects, kept)
        out["implicit_target_conditions"] = {
            "rows": len(rows13), "rows_kept": len(kept),
            "per_effect": {str(i): {"rows": len(l), "types": sorted({int(x["ConditionTypeOrReference"]) for x in l}),
                                    "searcher_mask": _safe(lambda l=l: searcher_type_mask(l))}
                           for i, l in sorted(lists.items())}}
    # source 17
    rows17 = [r for r in by_source.get(SOURCE_SPELL, []) if int(r["SourceEntry"]) == spell and int(r["SourceGroup"]) == 0
              and int(r["SourceId"]) == 0]
    if rows17:
        out["cast_conditions"] = {
            "consumer": "Spell.cpp:5917-5933 (targets [caster, explicit object])",
            "rows": [_row_view(r) for r in rows17],
            "class": "cast-gate",
            "scope": "character" if all(int(r["ConditionTarget"]) == 0 for r in rows17) else "character+explicit-target"}
    # source 18 (key: SourceGroup=creature, SourceEntry=spell)
    rows18 = [r for r in by_source.get(SOURCE_SPELL_CLICK, []) if int(r["SourceEntry"]) == spell]
    if rows18:
        out["spellclick_conditions"] = {"creatures": sorted({int(r["SourceGroup"]) for r in rows18}), "rows": len(rows18),
                                        "class": "world (npc_spellclick)"}
    for st in (SOURCE_SPELL_PROC, SOURCE_VEHICLE_SPELL, SOURCE_SKILL_LINE_ABILITY):
        key = "SourceGroup" if st == SOURCE_VEHICLE_SPELL else "SourceEntry"
        rs = [r for r in by_source.get(st, []) if int(r[key]) == spell]
        if rs:
            out[f"conditions_{SOURCE_NAMES[st].lower()}"] = [_row_view(r) for r in rs]
    # linked spells
    linked = b.world.linked(b.catalog)
    lk = []
    for (typ, trig), effs in sorted(linked.items()):
        if trig == spell:
            for e in effs:
                lk.append(_linked_policy(typ, trig, e, tiers))
        elif spell in effs or -spell in effs:
            lk.append(_linked_policy(typ, trig, spell if spell in effs else -spell, tiers) | {"as_child": True})
    if lk:
        out["linked"] = lk
    # selectors needing world data
    sel = []
    stp = b.world.rows_by("spell_target_position", "ID")
    for e in effects:
        if not e.effect:
            continue
        for slot, t in (("A", e.target_a), ("B", e.target_b)):
            if t in DB_SELECTORS:
                rows = [r for r in stp.get(spell, []) if int(r["EffectIndex"]) == e.index]
                sel.append({"effect": e.index, "slot": slot, "selector": DB_SELECTORS[t], "spell_target_position_rows": len(rows),
                            "policy": _db_policy(t, rows)})
            if t in ENTRY_SELECTORS:
                lst = (out.get("implicit_target_conditions") or {}).get("per_effect", {}).get(str(e.index))
                sel.append({"effect": e.index, "slot": slot, "selector": t, "category": _category(t),
                            "conditions": bool(lst), "policy": _entry_policy(t, bool(lst))})
    if sel:
        out["world_selectors"] = sel
    # spell_area / serverside
    sa = b.world.rows_by("spell_area", "spell").get(spell)
    if sa:
        out["spell_area_rows"] = len(sa)
    if any(k[0] == spell for k in b.world.serverside_spells()):
        out["serverside_spell"] = ("ignored: DB2 has the spell (SpellMgr.cpp:2887)" if b.catalog.exists(spell)
                                   else "serverside-only spell")
    return out


def _safe(fn):
    try:
        return fn()
    except FailClosed as exc:
        return f"fail-closed: {exc}"


def _row_view(r: dict[str, Any]) -> dict[str, Any]:
    ctype = int(r["ConditionTypeOrReference"])
    return {"type": ctype, "type_name": SUPPORTED_TYPES.get(ctype, "unsupported"), "target": int(r["ConditionTarget"]),
            "value1": int(r["ConditionValue1"]), "value2": int(r["ConditionValue2"]), "value3": int(r["ConditionValue3"]),
            "negative": int(r["NegativeCondition"]), "else_group": int(r["ElseGroup"]),
            "error_type": int(r.get("ErrorType") or 0), "script": r.get("ScriptName") or "",
            "value1_name": UNIT_STATE_NAMES.get(int(r["ConditionValue1"])) if ctype == CONDITION_UNIT_STATE else None,
            "comment": r.get("Comment", "")}


LINK_POLICY = {
    0: ("SPELL_LINK_CAST", "Spell.cpp:3949-3960 (Spell::_cast after AfterCast hooks)",
        "caster casts the linked spell (TRIGGERED_FULL_MASK) at m_targets unit target, else at itself; negative id: caster removes the aura"),
    1: ("SPELL_LINK_HIT", "Spell.cpp:3353-3364 (per hit unit)",
        "each hit unit casts the linked spell on ITSELF (original caster = spell caster); negative id: hit unit removes the aura"),
    2: ("SPELL_LINK_AURA", "SpellAuras.cpp:1405-1447 (aura application/removal on each target)",
        "apply: caster->AddAura(linked, target) (negative: immunity to that spell); removal: target removes the linked aura by caster"),
    3: ("SPELL_LINK_REMOVE", "SpellAuras.cpp:1419-1428",
        "on removal (not by death for positive ids): target casts linked spell on itself; negative id: target removes the aura"),
}


def _linked_policy(typ: int, trig: int, child: int, tiers) -> dict[str, Any]:
    name, consumer, rule = LINK_POLICY[typ]
    return {"type": name, "trigger": trig, "linked": child, "linked_tier": tiers.tier(abs(child)),
            "consumer": consumer, "recipient_rule": rule}


def _db_policy(t: int, rows: list[dict[str, Any]]) -> str:
    if t == 17:
        if rows:
            return "dest = spell_target_position row (teleport/bind: full location; else only when on the caster's map)"
        return "no spell_target_position row: dest = explicit object target position if any, else caster (Spell.cpp:1487-1491)"
    if t == 142:
        return ("row on caster map within range -> dest = row; else caster moved by radius (Spell.cpp:1157-1172)" if rows
                else "no row: nearby-entry search falls through (Spell.cpp:1176)")
    return "TARGET_DEST_NEARBY_DB has no Spell.cpp case: dest defaults to the caster (Spell.cpp:1467)"


def _entry_policy(t: int, has_conditions: bool) -> str:
    cat = _category(t)
    if has_conditions:
        return "candidates filtered by the implicit-target condition list"
    if cat == "NEARBY":
        return ("no conditions: 'emergency case' (Spell.cpp:1120) -> no entry filter; nearest object passing CheckTarget "
                "within range (WorldObjectLastSearcher); nothing found -> cast fails BAD_IMPLICIT_TARGETS (Spell.cpp:1196-1201)")
    return ("no conditions: TARGET_CHECK_ENTRY has no case in WorldObjectSpellTargetCheck (Spell.cpp:9322) -> every "
            "object of the selector's type passing CheckTarget is a candidate")


UNKNOWNS = [
    {"id": "TG-E-10", "subject": "reference conditions", "evidence": "world-db-fact",
     "known": "the overlay keeps only CONDITION_AURA reference rows", "unknown": "other reference lists",
     "why_unresolved": "tools/tdb_server_overlay.py filter", "reopen_condition": "a current-player row references a list",
     "build_skew": "n/a"},
    {"id": "TG-E-11", "subject": "TARGET_CHECK_ENTRY effects without conditions (in scope: 386276:0, 451125:0, 197214:2, 114852:2, 152588:0)",
     "evidence": "trinity-consumer", "known": "Trinity applies no entry filter (emergency case)",
     "unknown": "which creature entries Retail restricts these selectors to",
     "why_unresolved": "no world-db rows; client data has no entry list", "reopen_condition": "TDB adds source-13 rows or a sniff",
     "build_skew": False},
]

DEFECTS = [
    {"id": "TD-E-12", "file_line": "world DB conditions (SourceType 17, SourceEntry 36554 / 47482, ErrorType 103)",
     "description": "ErrorType 103 was SPELL_FAILED_ROOTED in the 3.3.5 enum; in the pinned enum 103 is "
                    "SPELL_FAILED_NO_ENDURANCE (SharedDefines.h:1812; ROOTED is 124 at :1833)",
     "effect_on_recipients": "none (the cast is still rejected); the client receives the wrong error",
     "oracle_behaviour": "report SPELL_FAILED_NO_ENDURANCE, mark defect"},
    {"id": "TD-E-10", "file_line": "src/server/game/Spells/Spell.cpp:1120-1180",
     "description": "current-player TARGET_CHECK_ENTRY selectors have no implicit-target conditions in the pinned world DB; "
                    "Trinity falls back to an unfiltered search",
     "effect_on_recipients": "any unit (area/cone) or the nearest object (nearby dest) instead of a specific entry",
     "oracle_behaviour": "reproduce the unfiltered search; mark defect"},
    {"id": "TD-E-11", "file_line": "scripts/research/dummy_semantics/conditions.py (Conditions.census / rows_for_spell)",
     "description": "research-tool defect, not Trinity: source 18 keyed by SourceGroup as if it were the spell "
                    "(Trinity key: SourceGroup = creature entry, ConditionMgr.cpp:1187); meets() negates a missing-object "
                    "result (Trinity returns false before negation, ConditionMgr.cpp:281-287)",
     "effect_on_recipients": "dummy-corpora/conditions.json player SPELL_CLICK_EVENT lists 46598/408907 (creature ids "
                             "31884/207684 collide with player SpellIDs)",
     "oracle_behaviour": "targeting.world uses the Trinity key and missing-object rule"},
]


def corpus(ctx, command: str) -> dict[str, Any]:
    b = ctx.bundle
    tiers = _tiers(ctx)
    by_source = _conditions_by_source(ctx)
    scoped = sorted(tiers.reach | tiers.controlled_unit | tiers.script_reach)
    # candidates: spells with any world row
    keyed: set[int] = set()
    for st, rows in by_source.items():
        for r in rows:
            if st in (SOURCE_IMPLICIT_TARGET, SOURCE_SPELL, SOURCE_SPELL_PROC, SOURCE_SPELL_CLICK, SOURCE_SKILL_LINE_ABILITY):
                keyed.add(int(r["SourceEntry"]))
            elif st == SOURCE_VEHICLE_SPELL:
                keyed.add(int(r["SourceGroup"]))
    for (typ, trig), effs in b.world.linked(b.catalog).items():
        keyed.add(trig)
        keyed.update(abs(e) for e in effs)
    keyed |= set(b.world.rows_by("spell_target_position", "ID"))
    keyed |= {int(s) for s in b.world.rows_by("spell_area", "spell")}
    keyed |= {k[0] for k in b.world.serverside_spells()}
    sel_spells = set()
    for s in scoped:
        for e in ctx.data.effects(s):
            if e.effect and ({e.target_a, e.target_b} & (ENTRY_SELECTORS | set(DB_SELECTORS))):
                sel_spells.add(s)
    policies = []
    for s in scoped:
        if s in keyed or s in sel_spells:
            p = spell_policy(ctx, s, tiers, by_source)
            if len(p) > 3:
                policies.append(p)
    # all-spell source-13 census (types used anywhere) for context
    types13_all = Counter(int(r["ConditionTypeOrReference"]) for r in by_source.get(SOURCE_IMPLICIT_TARGET, []))
    types13_scope = Counter(int(r["ConditionTypeOrReference"]) for r in by_source.get(SOURCE_IMPLICIT_TARGET, [])
                            if int(r["SourceEntry"]) in set(scoped))
    types17_scope = Counter(int(r["ConditionTypeOrReference"]) for r in by_source.get(SOURCE_SPELL, [])
                            if int(r["SourceEntry"]) in set(scoped))
    spellclick_player_false_positive = sorted({int(r["SourceEntry"]) for r in by_source.get(SOURCE_SPELL_CLICK, [])
                                               if int(r["SourceGroup"]) in tiers.reach})
    effect_classes: dict[str, Any] = {}
    extended: dict[str, Any] = {}
    for p in policies:
        s = p["spell"]
        tags: set[str] = set()
        klass = "understood"
        unknowns: list[str] = []
        info = b.catalog.get(s)
        effs = [e.index for e in info.effects if e.effect] if info else []
        per_eff: dict[int, tuple[str, set[str], list[str]]] = {}
        def bump(idx, k, t, u=()):
            c, ts, us = per_eff.get(idx, ("understood", set(), []))
            order = ("understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved")
            per_eff[idx] = (max(c, k, key=order.index), ts | set(t), us + list(u))
        if "cast_conditions" in p:
            for i in effs:
                bump(i, "fixture-dependent", {"condition", "world-db", "cast-gate"})
        if "implicit_target_conditions" in p:
            for i, d in p["implicit_target_conditions"]["per_effect"].items():
                unsupported = [t for t in d["types"] if t not in SUPPORTED_TYPES]
                bump(int(i), "blocked" if unsupported else "fixture-dependent", {"condition", "world-db"})
        for l in p.get("linked", []):
            if l.get("as_child"):
                continue
            for i in effs:
                bump(i, "understood", {"linked-spell", "world-db"})
        for w in p.get("world_selectors", []):
            if "spell_target_position_rows" in w:
                bump(w["effect"], "understood", {"spell-target-position", "world-db", "dest"})
            else:
                bump(w["effect"], "understood-with-defect", {"condition", "world-db", "entry-without-conditions"}, ["TG-E-11"])
        for i, (c, ts, us) in per_eff.items():
            row = {"class": c, "tags": sorted(ts), "unknowns": sorted(set(us)), "build_skew": b.skew.is_newer_than_trinity(s)}
            if p["tier"] == "reach":
                effect_classes[f"{s}:{i}"] = row
            else:
                extended[f"{s}:{i}"] = row | {"tier": p["tier"]}
    totals = {
        "scoped_spells": len(scoped),
        "spells_with_world_policy": len(policies),
        "by_tier": dict(sorted(Counter(p["tier"] for p in policies).items())),
        "implicit_target_condition_spells_in_scope": sum(1 for p in policies if "implicit_target_conditions" in p),
        "implicit_target_condition_types_in_scope": {str(k): v for k, v in sorted(types13_scope.items())},
        "implicit_target_condition_types_all_spells": {str(k): v for k, v in sorted(types13_all.items())},
        "implicit_target_condition_rows_all_spells": sum(types13_all.values()),
        "cast_condition_spells_in_scope": sum(1 for p in policies if "cast_conditions" in p),
        "cast_condition_types_in_scope": {str(k): v for k, v in sorted(types17_scope.items())},
        "linked_rows_touching_scope": sum(len(p.get("linked", [])) for p in policies),
        "spell_target_position_spells_in_scope": sum(1 for p in policies for w in p.get("world_selectors", [])
                                                     if w.get("spell_target_position_rows")),
        "db_selector_effects_in_scope": sum(1 for p in policies for w in p.get("world_selectors", []) if "spell_target_position_rows" in w),
        "entry_selector_effects_in_scope": sum(1 for p in policies for w in p.get("world_selectors", []) if "category" in w),
        "entry_selector_effects_without_conditions": sum(1 for p in policies for w in p.get("world_selectors", [])
                                                         if "category" in w and not w["conditions"]),
        "spell_area_spells_in_scope": sum(1 for p in policies if "spell_area_rows" in p),
        "serverside_rows_in_scope": sum(1 for p in policies if "serverside_spell" in p),
        "evaluator_supported_types": {str(k): v for k, v in SUPPORTED_TYPES.items()},
        "dummy_conditions_spellclick_false_positives": spellclick_player_false_positive,
        "effect_classes": len(effect_classes), "effect_classes_extended": len(extended),
    }
    return {
        "provenance": {"pins": PINS, "command": command, "evidence": ["world-db-fact", "trinity-consumer"],
                       "world_database": "docs/research/dummy-corpora/trinity-server-overlay.json",
                       "scope": "targeting.adapters.compute_tiers (reach + controlled-unit + script-reach)"},
        "consumers": {
            "implicit_target": "ConditionMgr.cpp:1851-1924 (validity), 1575-1682 (shared lists), Spell.cpp:9392-9395 (per candidate, targets [candidate, caster]), Spell.cpp:2158 (searcher mask)",
            "cast": "Spell.cpp:5917-5933 (targets [caster, explicit object])",
            "spellclick": "ConditionMgr.cpp:1187 key {creature, spell}",
            "linked": {v[0]: v[1] for v in LINK_POLICY.values()},
            "target_position": "Spell.cpp:1477-1492, 1157-1172",
            "entry_without_conditions": "Spell.cpp:1120-1180; WorldObjectSpellTargetCheck has no ENTRY case (Spell.cpp:9322-9373)",
            "searcher_mask": "ConditionMgr.cpp:983-1019: per ElseGroup AND of condition masks, OR over groups; negative row -> ALL; empty -> ALL",
            "missing_object": "ConditionMgr.cpp:281-287: returns false before NegativeCondition; mLastFailedCondition stays null -> CheckCast CASTER_AURASTATE",
        },
        "totals": totals,
        "policies": policies,
        "effect_classes": effect_classes,
        "effect_classes_extended": extended,
        "unknowns": UNKNOWNS,
        "retail_experiments": [
            {"id": "RX-E-10", "question": "Can Shadowstep be cast while rooted?", "model_a": "Trinity: condition UNIT_STATE_ROOT negated -> CASTER_AURASTATE",
             "model_b": "castable while rooted", "setup": "root the rogue (e.g. Frost Nova) and cast Shadowstep on an enemy",
             "observable": "UI error / cast success", "fidelity": "exact"},
            {"id": "RX-E-11", "question": "Which units can Bonedust Brew (386276 effect 0, TARGET_UNIT_DEST_AREA_ENTRY) hit?",
             "model_a": "Trinity: any unit passing CheckTarget (no entry conditions)", "model_b": "a specific creature entry only",
             "setup": "cast among enemies, allies and pets", "observable": "combat log recipients of 386276", "fidelity": "exact"},
        ],
        "trinity_defects": DEFECTS,
    }
