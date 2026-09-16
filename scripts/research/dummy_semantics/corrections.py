"""``SpellMgr::LoadSpellInfoCorrections`` audit.

Source: ``script-index.json`` -> ``engine[SpellMgr.cpp].corrections`` (every
``ApplySpellFix({ids}, lambda)`` with the members its lambda writes, tree-sitter
extracted).  Each write is classified by the *member* it targets; the
classification vocabulary is the brief's:

``source-correction``          a DB2 field is overwritten with a value the author
                               believes the client has wrong/missing (attribute bits,
                               ranges, radius, mechanics, targets, duration).
``missing-client-fact``        a relationship the client does not carry at all is
                               supplied (``TriggerSpell`` set from 0, ``ApplyAuraPeriod``
                               set when 0, ``ProcFlags`` added).
``implementation-workaround``  the effect/spell is disabled or retargeted to make
                               Trinity's engine behave (``Effect = SPELL_EFFECT_NONE``,
                               ``TargetA/B`` swapped to caster, ``AttributesCu`` bits).
``server-authored-policy``     ``AttributesCu`` / ``SPELL_ATTR0_CU_*`` bits and
                               ``ExplicitTargetMask`` rewrites: Trinity-only semantics.
``unknown``                    a member not in the table (never guessed).

Whether a corrected spell is *current-player reachable* comes from :mod:`scope`;
whether the id is newer than Trinity's supported build from ``build-skew.json``.
The "patch input vs runtime semantics" flag is mechanical: a write whose value
is a literal / enum constant is a **data patch** (it could be expressed as a
field override); a write whose value calls code or depends on other members is
**runtime semantics**.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .loaders import Bundle

MEMBER_CLASS: dict[str, str] = {
    "TriggerSpell": "missing-client-fact",
    "ApplyAuraPeriod": "missing-client-fact",
    "ProcFlags": "missing-client-fact",
    "ProcChance": "missing-client-fact",
    "ProcCharges": "missing-client-fact",
    "ProcCooldown": "missing-client-fact",
    "Effect": "implementation-workaround",
    "ApplyAuraName": "implementation-workaround",
    "TargetA": "implementation-workaround",
    "TargetB": "implementation-workaround",
    "AttributesCu": "server-authored-policy",
    "ExplicitTargetMask": "server-authored-policy",
    "Attributes": "source-correction", "AttributesEx": "source-correction", "AttributesEx2": "source-correction",
    "AttributesEx3": "source-correction", "AttributesEx4": "source-correction", "AttributesEx5": "source-correction",
    "AttributesEx6": "source-correction", "AttributesEx7": "source-correction", "AttributesEx8": "source-correction",
    "AttributesEx9": "source-correction", "AttributesEx10": "source-correction", "AttributesEx11": "source-correction",
    "AttributesEx12": "source-correction", "AttributesEx13": "source-correction", "AttributesEx14": "source-correction",
    "AttributesEx15": "source-correction", "AttributesEx16": "source-correction",
    "RangeEntry": "source-correction", "RadiusEntry": "source-correction", "MaxRadiusEntry": "source-correction",
    "DurationEntry": "source-correction", "MaxAffectedTargets": "source-correction", "Mechanic": "source-correction",
    "EffectMechanic": "source-correction", "Dispel": "source-correction", "InterruptFlags": "source-correction",
    "AuraInterruptFlags": "source-correction", "AuraInterruptFlags2": "source-correction",
    "ChannelInterruptFlags": "source-correction", "ChannelInterruptFlags2": "source-correction",
    "RecoveryTime": "source-correction", "CategoryRecoveryTime": "source-correction", "StartRecoveryTime": "source-correction",
    "StackAmount": "source-correction", "BasePoints": "source-correction", "MiscValue": "source-correction",
    "MiscValueB": "source-correction", "SpellFamilyFlags": "source-correction", "ChainTargets": "source-correction",
    "Speed": "source-correction", "SchoolMask": "source-correction", "CasterAuraSpell": "source-correction",
    "TargetAuraSpell": "source-correction", "ExcludeCasterAuraSpell": "source-correction", "ExcludeTargetAuraSpell": "source-correction",
    "CasterAuraState": "source-correction", "TargetAuraState": "source-correction", "MaxLevel": "source-correction",
    "BaseLevel": "source-correction", "SpellLevel": "source-correction", "Stances": "source-correction", "StancesNot": "source-correction",
    "Targets": "source-correction", "Amplitude": "source-correction", "PreventionType": "source-correction",
    "DmgClass": "source-correction", "ConeAngle": "source-correction", "Width": "source-correction",
    "RequiredAreasID": "source-correction", "ItemType": "source-correction", "BonusCoefficient": "source-correction",
    "BonusCoefficientFromAP": "source-correction", "RealPointsPerLevel": "source-correction", "PointsPerResource": "source-correction",
    "MaxTargetLevel": "source-correction", "EmpowerStageThresholds": "source-correction", "SpellVisual": "source-correction",
    "CastTimeEntry": "source-correction", "FacingCasterFlags": "source-correction", "ChargeCategoryId": "source-correction",
    "EquippedItemClass": "source-correction", "EquippedItemSubClassMask": "source-correction",
    "EquippedItemInventoryTypeMask": "source-correction", "Scaling": "source-correction",
    "CategoryId": "source-correction", "ContentTuningId": "source-correction",
}
LITERAL_VALUE = re.compile(r"^[-\w:\s|&~()*.]+$")   # constants / enum names / arithmetic on constants only


def classify_write(w: dict[str, Any]) -> dict[str, Any]:
    target = w["target"]
    member = target.split("->", 1)[1].split("[")[0].split(".")[0]
    cls = MEMBER_CLASS.get(member, "unknown")
    value = w["value"]
    data_patch = bool(LITERAL_VALUE.match(value)) and "(" not in value.replace("SpellImplicitTargetInfo(", "").replace("sSpellRadiusStore.LookupEntry(", "").replace("sSpellDurationStore.LookupEntry(", "").replace("sSpellRangeStore.LookupEntry(", "").replace("sSpellCastTimesStore.LookupEntry(", "")
    if "LookupEntry(" in value or "SpellImplicitTargetInfo(" in value:
        data_patch = bool(re.fullmatch(r"[\w:]+\(\s*[\w:]+\s*\)", value.strip()))
    return {"member": member, "effect": w.get("effect"), "op": w["op"], "value": value, "class": cls,
            "patch_shape": "data-patch" if data_patch else "runtime-code", "line": w["line"]}


class Corrections:
    def __init__(self, bundle: Bundle) -> None:
        self.b = bundle
        raw = bundle.index.engine.get("src/server/game/Spells/SpellMgr.cpp", {}).get("corrections", [])
        self.fixes: list[dict[str, Any]] = []
        self.by_spell: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for fx in raw:
            writes = [classify_write(w) for w in fx["writes"]]
            ids = [i["id"] for i in fx["ids"] if i["id"] is not None]
            unresolved = [i["text"] for i in fx["ids"] if i["id"] is None]
            rec = {"line": fx["line"], "note": fx["note"], "ids": ids, "unresolved_ids": unresolved,
                   "id_notes": {i["id"]: i.get("note") for i in fx["ids"] if i["id"] is not None and i.get("note")},
                   "writes": writes, "other_statements": fx.get("other", []),
                   "classes": sorted({w["class"] for w in writes}) or (["unknown"] if not fx.get("other") else ["runtime-code-only"]),
                   "exists": [i for i in ids if bundle.catalog.exists(i)],
                   "missing_in_snapshot": [i for i in ids if not bundle.catalog.exists(i)]}
            self.fixes.append(rec)
            for i in ids:
                self.by_spell[i].append(rec)

    def census(self, spells: set[int] | None = None) -> dict[str, Any]:
        fixes = [f for f in self.fixes if spells is None or any(i in spells for i in f["ids"])]
        ids = {i for f in fixes for i in f["ids"] if spells is None or i in spells}
        cls = Counter(c for f in fixes for c in f["classes"])
        members = Counter(w["member"] for f in fixes for w in f["writes"])
        shape = Counter(w["patch_shape"] for f in fixes for w in f["writes"])
        return {"fix_blocks": len(fixes), "spell_ids": len(ids),
                "spell_ids_missing_in_snapshot": sum(1 for f in fixes for i in f["missing_in_snapshot"]),
                "classes": dict(cls.most_common()), "members": dict(members.most_common()),
                "patch_shape": dict(shape.most_common()),
                "newer_than_trinity": sum(1 for i in ids if self.b.skew.is_newer_than_trinity(i))}
