"""``ConditionMgr`` participation in spell mechanics, plus evaluators for proved families.

Source types with a SpellID key (``ConditionMgr.h``):

* 13 ``SPELL_IMPLICIT_TARGET`` -- SourceGroup = effect mask; attached to
  ``SpellEffectInfo::ImplicitTargetConditions`` and evaluated per candidate in
  ``WorldObjectSpellTargetCheck`` (``Spell.cpp``) -- a *target filter*;
* 17 ``SPELL`` -- ``Spell::CheckCast`` (caster = target0, explicit target = target1);
  failing returns ``SPELL_FAILED_CASTER_AURASTATE`` / ``BAD_TARGETS`` / ErrorType -- a *cast gate*;
* 24 ``SPELL_PROC`` -- ``Aura::CanProc`` (actor, action target) -- a *proc gate*;
* 18 ``SPELL_CLICK_EVENT`` -- npc_spellclick (creature entry key, spell in SourceGroup);
* 21 ``VEHICLE_SPELL`` -- vehicle seat spells;
* 35 ``SKILL_LINE_ABILITY`` -- ``Player::LearnDefaultSkill*`` acquisition gate.

Evaluation (``ConditionMgr::IsObjectMeetToConditionList``): rows are grouped by
``ElseGroup``; a group passes when every row in it passes; the list passes when
any group passes; ``NegativeCondition`` inverts a row; ``ConditionTarget`` picks
which of the source objects is examined; ``ReferenceId`` (negative
SourceTypeOrReferenceId rows) recurses.

Evaluators are implemented **only** for the condition types that occur on
current-player spell sources (see :meth:`Conditions.census`), mirroring
``Condition::Meets`` exactly for those types.  Any other type raises
:class:`FailClosed`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import FailClosed
from .loaders import Bundle

SPELL_SOURCES = {13: "SPELL_IMPLICIT_TARGET", 17: "SPELL", 24: "SPELL_PROC", 18: "SPELL_CLICK_EVENT",
                 21: "VEHICLE_SPELL", 35: "SKILL_LINE_ABILITY"}
CONDITION_NAMES = {
    0: "NONE", 1: "AURA", 2: "ITEM", 3: "ITEM_EQUIPPED", 4: "ZONEID", 5: "REPUTATION_RANK", 6: "TEAM", 7: "SKILL",
    8: "QUESTREWARDED", 9: "QUESTTAKEN", 10: "DRUNKENSTATE", 11: "WORLD_STATE", 12: "ACTIVE_EVENT", 13: "INSTANCE_INFO",
    14: "QUEST_NONE", 15: "CLASS", 16: "RACE", 17: "ACHIEVEMENT", 18: "TITLE", 19: "SPAWNMASK_DEPRECATED", 20: "GENDER",
    21: "UNIT_STATE", 22: "MAPID", 23: "AREAID", 24: "CREATURE_TYPE", 25: "SPELL", 26: "PHASEID", 27: "LEVEL",
    28: "QUEST_COMPLETE", 29: "NEAR_CREATURE", 30: "NEAR_GAMEOBJECT", 31: "OBJECT_ENTRY_GUID_LEGACY", 32: "TYPE_MASK_LEGACY",
    33: "RELATION_TO", 34: "REACTION_TO", 35: "DISTANCE_TO", 36: "ALIVE", 37: "HP_VAL", 38: "HP_PCT", 39: "REALM_ACHIEVEMENT",
    40: "IN_WATER", 41: "TERRAIN_SWAP", 42: "STAND_STATE", 43: "DAILY_QUEST_DONE", 44: "CHARMED", 45: "PET_TYPE", 46: "TAXI",
    47: "QUESTSTATE", 48: "QUEST_OBJECTIVE_PROGRESS", 49: "DIFFICULTY_ID", 50: "GAMEMASTER", 51: "OBJECT_ENTRY_GUID",
    52: "TYPE_MASK", 53: "BATTLE_PET_COUNT", 54: "SCENARIO_STEP", 55: "SCENE_IN_PROGRESS", 56: "PLAYER_CONDITION",
    57: "PRIVATE_OBJECT", 58: "STRING_ID", 59: "LABEL",
}
#: ComparisionType (Util.h)
COMP_EQ, COMP_HIGH, COMP_LOW, COMP_HIGH_EQ, COMP_LOW_EQ = 0, 1, 2, 3, 4
#: RelationType (ConditionMgr.h)
RELATION_SELF, RELATION_IN_PARTY, RELATION_IN_RAID_OR_PARTY, RELATION_OWNED_BY, RELATION_PASSENGER_OF, RELATION_CREATED_BY = range(6)
#: TypeID (ObjectGuid.h) for OBJECT_ENTRY_GUID
TYPEID_UNIT, TYPEID_PLAYER, TYPEID_GAMEOBJECT = 3, 4, 5
TYPEMASK_UNIT, TYPEMASK_PLAYER, TYPEMASK_GAMEOBJECT = 0x8, 0x10, 0x20


def compare_values(comp: int, value: float, ref: float) -> bool:
    """Mirrors: ``CompareValues<T>`` (Util.h)."""
    if comp == COMP_EQ:
        return value == ref
    if comp == COMP_HIGH:
        return value > ref
    if comp == COMP_LOW:
        return value < ref
    if comp == COMP_HIGH_EQ:
        return value >= ref
    if comp == COMP_LOW_EQ:
        return value <= ref
    raise FailClosed(f"unknown ComparisionType {comp}")


@dataclass
class ObjectFacts:
    """Explicit runtime facts about one condition target (never inferred)."""
    type_id: int = TYPEID_UNIT           # TYPEID_UNIT / TYPEID_PLAYER / TYPEID_GAMEOBJECT
    entry: int = 0
    guid: int = 0
    alive: bool = True
    health: int = 1
    max_health: int = 1
    level: int = 1
    class_mask: int = 0
    aura_effects: set[tuple[int, int]] = field(default_factory=set)  # (spell, effIndex)
    spells_known: set[int] = field(default_factory=set)
    unit_state: int = 0
    relations: dict[tuple[int, int], bool] = field(default_factory=dict)  # (relation, other.guid) -> bool
    distances: dict[int, float] = field(default_factory=dict)          # other.guid -> distance
    creature_type: int | None = None
    area_ids: set[int] = field(default_factory=set)
    labels: set[int] = field(default_factory=set)
    string_ids: set[str] = field(default_factory=set)

    @property
    def type_mask(self) -> int:
        return {TYPEID_UNIT: TYPEMASK_UNIT, TYPEID_PLAYER: TYPEMASK_PLAYER | TYPEMASK_UNIT,
                TYPEID_GAMEOBJECT: TYPEMASK_GAMEOBJECT}.get(self.type_id, 0)

    @property
    def is_unit(self) -> bool:
        return self.type_id in (TYPEID_UNIT, TYPEID_PLAYER)

    @property
    def health_pct(self) -> float:
        return 100.0 * self.health / self.max_health if self.max_health else 0.0


SUPPORTED = {1, 15, 21, 25, 27, 31, 32, 33, 35, 36, 37, 38, 51, 52, 58, 59, 24, 0}


def meets(row: dict[str, Any], targets: list[ObjectFacts | None]) -> bool:
    """Mirrors: ``Condition::Meets`` for the supported types (``ConditionTarget`` selects the object)."""
    ctype = int(row["ConditionTypeOrReference"])
    if ctype not in SUPPORTED:
        raise FailClosed(f"condition type {ctype} ({CONDITION_NAMES.get(ctype, '?')}) has no evaluator")
    idx = int(row["ConditionTarget"])
    obj = targets[idx] if idx < len(targets) else None
    v1, v2, v3 = int(row["ConditionValue1"]), int(row["ConditionValue2"]), int(row["ConditionValue3"])
    met = False
    if obj is None:
        met = ctype == 0
    elif ctype == 0:
        met = True
    elif ctype == 1:      # AURA: unit->HasAuraEffect(v1, v2)
        met = obj.is_unit and (v1, v2) in obj.aura_effects
    elif ctype == 15:     # CLASS: unit->GetClassMask() & v1
        met = obj.is_unit and bool(obj.class_mask & v1)
    elif ctype == 21:     # UNIT_STATE
        met = obj.is_unit and bool(obj.unit_state & v1)
    elif ctype == 25:     # SPELL: player->HasSpell(v1)
        met = obj.type_id == TYPEID_PLAYER and v1 in obj.spells_known
    elif ctype == 27:     # LEVEL: CompareValues(v2, level, v1)
        met = obj.is_unit and compare_values(v2, obj.level, v1)
    elif ctype in (31, 51):  # OBJECT_ENTRY_GUID: typeid == v1 && (!v2 || entry == v2) && (!v3 || spawnid == v3)
        met = obj.type_id == v1 and (not v2 or obj.entry == v2) and (not v3 or obj.guid == v3)
    elif ctype in (32, 52):  # TYPE_MASK: object->isType(v1)
        met = bool(obj.type_mask & v1)
    elif ctype == 33:     # RELATION_TO: relation v2 between object and targets[v1]
        other = targets[v1] if v1 < len(targets) else None
        met = bool(other is not None and obj.is_unit and other.is_unit and obj.relations.get((v2, other.guid), False))
    elif ctype == 35:     # DISTANCE_TO: CompareValues(v3, GetDistance(targets[v1]), v2)
        other = targets[v1] if v1 < len(targets) else None
        met = other is not None and other.guid in obj.distances and compare_values(v3, obj.distances[other.guid], float(v2))
    elif ctype == 36:     # ALIVE
        met = obj.is_unit and obj.alive
    elif ctype == 37:     # HP_VAL
        met = obj.is_unit and compare_values(v2, obj.health, v1)
    elif ctype == 38:     # HP_PCT
        met = obj.is_unit and compare_values(v2, obj.health_pct, float(v1))
    elif ctype == 24:     # CREATURE_TYPE
        met = obj.creature_type is not None and obj.creature_type == v1
    elif ctype == 58:     # STRING_ID
        met = str(row.get("ConditionStringValue1", "")) in obj.string_ids
    elif ctype == 59:     # LABEL
        met = v1 in obj.labels
    if int(row.get("NegativeCondition", 0)):
        met = not met
    return met


def meets_list(rows: list[dict[str, Any]], targets: list[ObjectFacts | None], references: dict[int, list[dict[str, Any]]] | None = None) -> bool:
    """Mirrors: ``ConditionMgr::IsObjectMeetToConditionList`` (ElseGroup OR of ANDs, references recurse)."""
    groups: dict[int, bool] = {}
    for row in rows:
        g = int(row["ElseGroup"])
        groups.setdefault(g, True)
        if not groups[g]:
            continue
        ref = int(row["SourceTypeOrReferenceId"])
        ctype = int(row["ConditionTypeOrReference"])
        if ref >= 0 and ctype < 0:
            # a reference row: ConditionTypeOrReference < 0 names the reference id
            ref_rows = (references or {}).get(-ctype)
            if ref_rows is None:
                raise FailClosed(f"reference condition {-ctype} not in corpus")
            ok = meets_list(ref_rows, targets, references)
            if int(row.get("NegativeCondition", 0)):
                ok = not ok
            if not ok:
                groups[g] = False
        elif not meets(row, targets):
            groups[g] = False
    return any(groups.values()) if groups else True


class Conditions:
    def __init__(self, bundle: Bundle) -> None:
        self.b = bundle
        self.by_source = bundle.world.conditions_by_source()
        self.references: dict[int, list[dict[str, Any]]] = {}
        for st, entries in self.by_source.items():
            if st < 0:
                for rows in entries.values():
                    self.references.setdefault(-st, []).extend(rows)

    def rows_for_spell(self, spell_id: int) -> dict[str, list[dict[str, Any]]]:
        out = {}
        for st, name in SPELL_SOURCES.items():
            if st == 18:  # spell in SourceGroup for spellclick
                rows = [r for entries in self.by_source.get(st, {}).values() for r in entries if int(r["SourceGroup"]) == spell_id]
            else:
                rows = self.by_source.get(st, {}).get(spell_id, [])
            if rows:
                out[name] = rows
        return out

    def census(self, spells: set[int] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for st, name in SPELL_SOURCES.items():
            entries = self.by_source.get(st, {})
            keyed = {k: v for k, v in entries.items() if spells is None or (k in spells if st != 18 else any(int(r["SourceGroup"]) in spells for r in v))}
            types = Counter(CONDITION_NAMES.get(int(r["ConditionTypeOrReference"]), str(r["ConditionTypeOrReference"]))
                            for rows in keyed.values() for r in rows)
            unsupported = sorted({CONDITION_NAMES.get(int(r["ConditionTypeOrReference"]), str(r["ConditionTypeOrReference"]))
                                  for rows in keyed.values() for r in rows if int(r["ConditionTypeOrReference"]) not in SUPPORTED and int(r["ConditionTypeOrReference"]) >= 0})
            out[name] = {"source_type": st, "spells": len(keyed), "rows": sum(len(v) for v in keyed.values()),
                         "condition_types": dict(types.most_common()), "types_without_evaluator": unsupported,
                         "spell_ids": sorted(keyed)[:50]}
        out["reference_conditions"] = {"count": len(self.references)}
        return out
