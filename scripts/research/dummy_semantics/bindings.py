"""Complete script-binding map: SpellID -> spell_script_names -> class -> hooks -> facts.

Mirrors:
* ``ObjectMgr::LoadSpellScriptNames`` (rank expansion) via :meth:`ServerOverlay.script_names`;
* ``ScriptMgr::CreateSpellScripts/CreateAuraScripts`` (a registration name yields a
  SpellScript, an AuraScript, or both) via :meth:`ScriptIndex.resolve_script_name`;
* ``SpellScriptBase::EffectHook::GetAffectedEffectsMask`` and the three
  ``CheckEffect`` overloads (``SpellScript::EffectBase``, ``AuraScript::EffectBase``,
  ``SpellScript::TargetHook``) -- which SpellEffect rows each effect-matched hook fires for.
  A hook whose mask is 0 is registered but never executes (Trinity logs
  "did not match dbc effect data" in ``_Validate`` and continues).

Everything else recorded per handler comes from the structural index and is
navigation: call categories, referenced SpellIDs, prevent-default calls,
mutable fields, RNG/delay markers.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from procs._trinity_names import SPELL_TARGET_NAMES
from procs.enums import aura, aura_name, effect, effect_name
from procs.spells import DIFFICULTY_NONE, SpellInfo

from .hooks import BY_LIST, PREVENTING_LISTS
from .loaders import Bundle, ScriptClass

EFFECT_ALL, EFFECT_FIRST_FOUND = "EFFECT_ALL", "EFFECT_FIRST_FOUND"
SPELL_EFFECT_ANY, SPELL_AURA_ANY = "SPELL_EFFECT_ANY", "SPELL_AURA_ANY"
_TARGET_BY_NAME = {v: k for k, v in SPELL_TARGET_NAMES.items()}
TARGET_LISTS = {"OnObjectAreaTargetSelect": "area", "OnObjectTargetSelect": "object", "OnDestinationTargetSelect": "dest"}
AURA_EFFECT_LISTS = {"OnEffectApply", "AfterEffectApply", "OnEffectRemove", "AfterEffectRemove", "OnEffectPeriodic",
                     "OnEffectUpdatePeriodic", "DoEffectCalcAmount", "DoEffectCalcPeriodic", "DoEffectCalcSpellMod",
                     "DoEffectCalcCritChance", "DoEffectCalcDamageAndHealing", "OnEffectAbsorb", "AfterEffectAbsorb",
                     "OnEffectAbsorbHeal", "AfterEffectAbsorbHeal", "OnEffectManaShield", "AfterEffectManaShield",
                     "OnEffectSplit", "DoCheckEffectProc", "OnEffectProc", "AfterEffectProc"}
SPELL_EFFECT_LISTS = {"OnEffectLaunch", "OnEffectLaunchTarget", "OnEffectHit", "OnEffectHitTarget", "OnEffectSuccessfulDispel"}

# PRIVATE transcription of ``SpellImplicitTargetInfo::_data`` (SpellInfo.cpp:246-400 @ 7f3d43b):
# target -> (SelectionCategory, ReferenceType, ObjectType), prefixes TARGET_SELECT_CATEGORY_ /
# TARGET_REFERENCE_TYPE_ / TARGET_OBJECT_TYPE_ stripped.  Only what TargetHook::CheckEffect reads.
# Request (targeting pass, track E): switch to ``targeting.selectors`` (track A) once published and
# differential-test the two.  TOTAL_SPELL_TARGETS = 153; larger values index out of bounds (UB) -> fail closed.
_TC_SELECTOR_DATA: dict[int, tuple[str, str, str]] = {
    0: ('NYI', 'NONE', 'NONE'), 1: ('DEFAULT', 'CASTER', 'UNIT'), 2: ('NEARBY', 'CASTER', 'UNIT'),
    3: ('NEARBY', 'CASTER', 'UNIT'), 4: ('NEARBY', 'CASTER', 'UNIT'), 5: ('DEFAULT', 'CASTER', 'UNIT'),
    6: ('DEFAULT', 'TARGET', 'UNIT'), 7: ('AREA', 'SRC', 'UNIT'), 8: ('AREA', 'DEST', 'UNIT'),
    9: ('DEFAULT', 'CASTER', 'DEST'), 10: ('NYI', 'NONE', 'NONE'), 11: ('NYI', 'SRC', 'UNIT'),
    12: ('NYI', 'NONE', 'NONE'), 13: ('NYI', 'NONE', 'NONE'), 14: ('NYI', 'NONE', 'NONE'),
    15: ('AREA', 'SRC', 'UNIT'), 16: ('AREA', 'DEST', 'UNIT'), 17: ('DEFAULT', 'CASTER', 'DEST'),
    18: ('DEFAULT', 'CASTER', 'DEST'), 19: ('NYI', 'NONE', 'NONE'), 20: ('AREA', 'CASTER', 'UNIT'),
    21: ('DEFAULT', 'TARGET', 'UNIT'), 22: ('DEFAULT', 'CASTER', 'SRC'), 23: ('DEFAULT', 'TARGET', 'GOBJ'),
    24: ('CONE', 'CASTER', 'UNIT'), 25: ('DEFAULT', 'TARGET', 'UNIT'), 26: ('DEFAULT', 'TARGET', 'GOBJ_ITEM'),
    27: ('DEFAULT', 'CASTER', 'UNIT'), 28: ('DEFAULT', 'DEST', 'DEST'), 29: ('DEFAULT', 'DEST', 'DEST'),
    30: ('AREA', 'SRC', 'UNIT'), 31: ('AREA', 'DEST', 'UNIT'), 32: ('DEFAULT', 'CASTER', 'DEST'),
    33: ('AREA', 'SRC', 'UNIT'), 34: ('AREA', 'DEST', 'UNIT'), 35: ('DEFAULT', 'TARGET', 'UNIT'),
    36: ('NYI', 'CASTER', 'DEST'), 37: ('AREA', 'LAST', 'UNIT'), 38: ('NEARBY', 'CASTER', 'UNIT'),
    39: ('DEFAULT', 'CASTER', 'DEST'), 40: ('NEARBY', 'CASTER', 'GOBJ'), 41: ('DEFAULT', 'CASTER', 'DEST'),
    42: ('DEFAULT', 'CASTER', 'DEST'), 43: ('DEFAULT', 'CASTER', 'DEST'), 44: ('DEFAULT', 'CASTER', 'DEST'),
    45: ('DEFAULT', 'TARGET', 'UNIT'), 46: ('NEARBY', 'CASTER', 'DEST'), 47: ('DEFAULT', 'CASTER', 'DEST'),
    48: ('DEFAULT', 'CASTER', 'DEST'), 49: ('DEFAULT', 'CASTER', 'DEST'), 50: ('DEFAULT', 'CASTER', 'DEST'),
    51: ('AREA', 'SRC', 'GOBJ'), 52: ('AREA', 'DEST', 'GOBJ'), 53: ('DEFAULT', 'TARGET', 'DEST'),
    54: ('CONE', 'CASTER', 'UNIT'), 55: ('DEFAULT', 'CASTER', 'DEST'), 56: ('AREA', 'CASTER', 'UNIT'),
    57: ('DEFAULT', 'TARGET', 'UNIT'), 58: ('NEARBY', 'CASTER', 'UNIT'), 59: ('CONE', 'CASTER', 'UNIT'),
    60: ('CONE', 'CASTER', 'UNIT'), 61: ('AREA', 'TARGET', 'UNIT'), 62: ('DEFAULT', 'CASTER', 'DEST'),
    63: ('DEFAULT', 'TARGET', 'DEST'), 64: ('DEFAULT', 'TARGET', 'DEST'), 65: ('DEFAULT', 'TARGET', 'DEST'),
    66: ('DEFAULT', 'TARGET', 'DEST'), 67: ('DEFAULT', 'TARGET', 'DEST'), 68: ('DEFAULT', 'TARGET', 'DEST'),
    69: ('DEFAULT', 'TARGET', 'DEST'), 70: ('DEFAULT', 'TARGET', 'DEST'), 71: ('DEFAULT', 'TARGET', 'DEST'),
    72: ('DEFAULT', 'CASTER', 'DEST'), 73: ('DEFAULT', 'CASTER', 'DEST'), 74: ('DEFAULT', 'TARGET', 'DEST'),
    75: ('DEFAULT', 'TARGET', 'DEST'), 76: ('CHANNEL', 'CASTER', 'DEST'), 77: ('CHANNEL', 'CASTER', 'UNIT'),
    78: ('DEFAULT', 'DEST', 'DEST'), 79: ('DEFAULT', 'DEST', 'DEST'), 80: ('DEFAULT', 'DEST', 'DEST'),
    81: ('DEFAULT', 'DEST', 'DEST'), 82: ('DEFAULT', 'DEST', 'DEST'), 83: ('DEFAULT', 'DEST', 'DEST'),
    84: ('DEFAULT', 'DEST', 'DEST'), 85: ('DEFAULT', 'DEST', 'DEST'), 86: ('DEFAULT', 'DEST', 'DEST'),
    87: ('DEFAULT', 'DEST', 'DEST'), 88: ('DEFAULT', 'DEST', 'DEST'), 89: ('TRAJ', 'DEST', 'DEST'),
    90: ('DEFAULT', 'TARGET', 'UNIT'), 91: ('DEFAULT', 'DEST', 'DEST'), 92: ('DEFAULT', 'CASTER', 'UNIT'),
    93: ('AREA', 'SRC', 'CORPSE'), 94: ('DEFAULT', 'CASTER', 'UNIT'), 95: ('DEFAULT', 'TARGET', 'UNIT'),
    96: ('DEFAULT', 'CASTER', 'UNIT'), 97: ('DEFAULT', 'CASTER', 'UNIT'), 98: ('DEFAULT', 'CASTER', 'UNIT'),
    99: ('DEFAULT', 'CASTER', 'UNIT'), 100: ('DEFAULT', 'CASTER', 'UNIT'), 101: ('DEFAULT', 'CASTER', 'UNIT'),
    102: ('DEFAULT', 'CASTER', 'UNIT'), 103: ('DEFAULT', 'CASTER', 'UNIT'), 104: ('CONE', 'CASTER', 'UNIT'),
    105: ('AREA', 'CASTER', 'UNIT'), 106: ('DEFAULT', 'CASTER', 'DEST'), 107: ('NEARBY', 'CASTER', 'DEST'),
    108: ('CONE', 'CASTER', 'GOBJ'), 109: ('CONE', 'CASTER', 'GOBJ'), 110: ('CONE', 'CASTER', 'UNIT'),
    111: ('NYI', 'NONE', 'NONE'), 112: ('DEFAULT', 'CASTER', 'DEST'), 113: ('DEFAULT', 'TARGET', 'DEST'),
    114: ('NYI', 'NONE', 'NONE'), 115: ('AREA', 'SRC', 'UNIT'), 116: ('AREA', 'LAST', 'UNIT_AND_DEST'),
    117: ('NYI', 'NONE', 'NONE'), 118: ('AREA', 'TARGET', 'UNIT'), 119: ('AREA', 'CASTER', 'CORPSE'),
    120: ('AREA', 'CASTER', 'UNIT'), 121: ('DEFAULT', 'TARGET', 'CORPSE'), 122: ('AREA', 'CASTER', 'UNIT'),
    123: ('AREA', 'CASTER', 'UNIT'), 124: ('DEFAULT', 'CASTER', 'UNIT'), 125: ('DEFAULT', 'CASTER', 'DEST'),
    126: ('NYI', 'NONE', 'UNIT'), 127: ('NYI', 'NONE', 'DEST'), 128: ('CONE', 'CASTER', 'UNIT'),
    129: ('CONE', 'CASTER', 'UNIT'), 130: ('CONE', 'CASTER', 'UNIT'), 131: ('DEFAULT', 'CASTER', 'DEST'),
    132: ('DEFAULT', 'TARGET', 'DEST'), 133: ('LINE', 'DEST', 'UNIT'), 134: ('LINE', 'DEST', 'UNIT'),
    135: ('LINE', 'DEST', 'UNIT'), 136: ('CONE', 'CASTER', 'UNIT'), 137: ('DEFAULT', 'CASTER', 'DEST'),
    138: ('DEFAULT', 'DEST', 'DEST'), 139: ('NYI', 'NONE', 'NONE'), 140: ('NYI', 'NONE', 'DEST'),
    141: ('NYI', 'NONE', 'NONE'), 142: ('NEARBY', 'CASTER', 'DEST'), 143: ('NYI', 'NONE', 'NONE'),
    144: ('NYI', 'NONE', 'NONE'), 145: ('NYI', 'NONE', 'NONE'), 146: ('NYI', 'NONE', 'NONE'),
    147: ('NYI', 'NONE', 'NONE'), 148: ('DEFAULT', 'DEST', 'DEST'), 149: ('DEFAULT', 'CASTER', 'DEST'),
    150: ('DEFAULT', 'CASTER', 'UNIT'), 151: ('AREA', 'CASTER', 'UNIT'), 152: ('NYI', 'NONE', 'NONE'),

}
TOTAL_SPELL_TARGETS = 153
assert len(_TC_SELECTOR_DATA) == TOTAL_SPELL_TARGETS


class TargetHookUndecidable(ValueError):
    """Raised when TargetHook::CheckEffect would read outside ``SpellImplicitTargetInfo::_data``."""


def target_hook_check_effect(target_type: int, area: bool, dest: bool, target_a: int | None, target_b: int | None) -> bool:
    """``SpellScript::TargetHook::CheckEffect`` for one effect row.

    Mirrors: SpellScript.cpp:203-254 (pinned 7f3d43b).
    ``area``/``dest`` are the TargetHook constructor flags (SpellScript.h:523/561/599):
    OnObjectAreaTargetSelect=(True, False), OnObjectTargetSelect=(False, False),
    OnDestinationTargetSelect=(False, True).  ``target_a``/``target_b`` are None when the
    effect index is past ``GetEffects().size()`` (the size check at :208); a blank
    (gap) effect is passed as 0/0.
    """
    if not target_type:                                   # :205
        return False
    if target_a is None:                                  # :208
        return False
    if target_a != target_type and target_b != target_type:   # :212-214
        return False
    if not 0 <= target_type < TOTAL_SPELL_TARGETS:
        raise TargetHookUndecidable(f"target {target_type} outside SpellImplicitTargetInfo::_data")
    category, reference, obj = _TC_SELECTOR_DATA[target_type]   # :216
    if category == "CHANNEL":                              # :219
        return not area
    if category == "NEARBY":                               # :221
        return True
    if category in ("CONE", "LINE"):                       # :223-225
        return area
    if category == "AREA":                                 # :226-229
        if obj == "UNIT_AND_DEST":
            return area or dest
        return area
    if category == "DEFAULT":                              # :230-248
        if obj == "SRC":
            return False
        if obj == "DEST":
            return dest
        if reference == "CASTER":
            return not area
        if reference == "TARGET":
            return True
        return False
    return False                                           # NYI / TRAJ -> :250-253


def target_hook_equality_only(target_type: int, target_a: int | None, target_b: int | None) -> bool:
    """The pre-fix approximation (TargetA/TargetB equality only); kept for erratum diffs."""
    return target_a is not None and (target_a == target_type or target_b == target_type)


TARGET_HOOK_FLAGS = {"OnObjectAreaTargetSelect": (True, False), "OnObjectTargetSelect": (False, False),
                     "OnDestinationTargetSelect": (False, True)}


def parse_eff_index(token: str) -> int | str | None:
    token = token.strip()
    if token in (EFFECT_ALL, EFFECT_FIRST_FOUND):
        return token
    m = re.fullmatch(r"EFFECT_(\d+)", token)
    if m:
        return int(m.group(1))
    if token.isdigit():
        return int(token)
    return None


def parse_effect_name(token: str) -> int | str | None:
    token = token.strip()
    if token == SPELL_EFFECT_ANY:
        return token
    if token.startswith("SPELL_EFFECT_"):
        try:
            return effect(token[len("SPELL_EFFECT_"):])
        except KeyError:
            return None
    return None


def parse_aura_name(token: str) -> int | str | None:
    token = token.strip()
    if token == SPELL_AURA_ANY:
        return token
    if token.startswith("SPELL_AURA_"):
        try:
            return aura(token[len("SPELL_AURA_"):])
        except KeyError:
            return None
    return None


def parse_target(token: str) -> int | None:
    return _TARGET_BY_NAME.get(token.strip())


def affected_mask(info: SpellInfo, eff_index: int | str, check) -> int:
    """Mirrors: ``SpellScriptBase::EffectHook::GetAffectedEffectsMask``."""
    mask = 0
    indices = sorted(e.index for e in info.effects)
    if eff_index in (EFFECT_ALL, EFFECT_FIRST_FOUND):
        for i in indices:
            if eff_index == EFFECT_FIRST_FOUND and mask:
                return mask
            if check(i):
                mask |= 1 << i
    elif isinstance(eff_index, int):
        if check(eff_index):
            mask |= 1 << eff_index
    return mask


@dataclass
class HookBinding:
    list: str
    kind: str                     # SpellScript | AuraScript
    handler: str
    line: int
    eff_index: int | str | None
    eff_name: str | None
    eff_value: int | str | None   # resolved effect/aura/target value
    affected_mask: int | None     # None = cannot be decided (unresolved token)
    affected_effects: list[int]
    executes: bool
    can_prevent_default: bool
    facts: dict[str, Any]         # handler body facts (structural)
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["facts"] = summarize_facts(self.facts)
        return d


def summarize_facts(f: dict[str, Any]) -> dict[str, Any]:
    if not f:
        return {}
    cats = Counter(c["cat"] for c in f.get("calls", []))
    callees = Counter(c["callee"] for c in f.get("calls", []))
    ints = sorted({v for c in f.get("calls", []) for v in c.get("ints", [])} | set(f.get("refs", {}).values()))
    return {
        "categories": dict(cats), "callees": dict(callees.most_common(20)),
        "prevents_default": any(c["cat"] == "prevent" for c in f.get("calls", [])),
        "refs": ints[:40], "tokens": f.get("tokens", [])[:30],
        "case_ints": [c["value"] for c in f.get("case_ints", []) if c.get("value") is not None][:20],
        "rng": any(c["cat"] == "rng" for c in f.get("calls", [])),
        "delayed": any(c["cat"] == "delay" for c in f.get("calls", [])),
        "loops": f.get("loops", 0), "lambdas": f.get("lambdas", 0), "line": f.get("line"),
    }


@dataclass
class Binding:
    spell_id: int
    script_name: str
    resolved: bool
    classes: list[dict[str, Any]]
    hooks: list[HookBinding]
    ctor_args: list[str]
    registration: list[str]
    fields: list[str] = field(default_factory=list)
    validate_spells: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    load_gate: dict[str, Any] = field(default_factory=dict)   # Load() predicate facts: aura/spell queries that gate the whole script

    @property
    def kinds(self) -> list[str]:
        return sorted({c["kind"] for c in self.classes})

    def to_dict(self) -> dict[str, Any]:
        return {"spell_id": self.spell_id, "script_name": self.script_name, "resolved": self.resolved,
                "classes": self.classes, "kinds": self.kinds, "hooks": [h.to_dict() for h in self.hooks],
                "ctor_args": self.ctor_args, "registration": self.registration, "fields": self.fields,
                "validate_spells": self.validate_spells, "notes": self.notes, "load_gate": self.load_gate,
                "any_prevent_default": any(h.facts and any(c["cat"] == "prevent" for c in h.facts.get("calls", [])) for h in self.hooks),
                "executing_hooks": sum(1 for h in self.hooks if h.executes)}


class BindingMap:
    def __init__(self, bundle: Bundle) -> None:
        self.b = bundle
        self.by_spell: dict[int, list[Binding]] = defaultdict(list)
        self.unresolved_names: Counter = Counter()
        self.script_spells: dict[str, set[int]] = defaultdict(set)
        self._build()

    def _hook_binding(self, sc: ScriptClass, hook: dict[str, Any], info: SpellInfo) -> HookBinding:
        lst = hook["list"]
        spec = BY_LIST.get(lst)
        kind = sc.kind
        handler = hook["handler"]
        facts = self.b.index.merged_facts(sc, handler)
        eff_index = parse_eff_index(hook["eff_index"]) if "eff_index" in hook else None
        eff_name = hook.get("eff_name")
        eff_value: int | str | None = None
        mask: int | None = None
        note = None
        if spec is None:
            note = f"unknown hook list {lst}"
        if lst in SPELL_EFFECT_LISTS:
            eff_value = parse_effect_name(eff_name or "")
            if eff_index is None or eff_value is None:
                note = f"unresolved hook tokens {hook.get('args')}"
            else:
                def check(i: int, v=eff_value) -> bool:  # SpellScript::EffectBase::CheckEffect
                    e = info.effect(i)
                    return e is not None and (v == SPELL_EFFECT_ANY or e.effect == v)
                mask = affected_mask(info, eff_index, check)
        elif lst in AURA_EFFECT_LISTS:
            eff_value = parse_aura_name(eff_name or "")
            if eff_index is None or eff_value is None:
                note = f"unresolved hook tokens {hook.get('args')}"
            else:
                def check(i: int, v=eff_value) -> bool:  # AuraScript::EffectBase::CheckEffect
                    e = info.effect(i)
                    if e is None:
                        return False
                    if not e.aura and v == 0:
                        return True
                    if not e.aura:
                        return False
                    return v == SPELL_AURA_ANY or e.aura == v
                mask = affected_mask(info, eff_index, check)
        elif lst in TARGET_LISTS:
            eff_value = parse_target(eff_name or "")
            if eff_index is None or eff_value is None:
                note = f"unresolved hook tokens {hook.get('args')}"
            else:
                area, dest = TARGET_HOOK_FLAGS[lst]

                def check(i: int, v=eff_value, area=area, dest=dest) -> bool:  # SpellScript::TargetHook::CheckEffect
                    e = info.effect(i)
                    return target_hook_check_effect(v, area, dest, e.target_a if e else None, e.target_b if e else None)
                mask = affected_mask(info, eff_index, check)
                eq_mask = affected_mask(info, eff_index, lambda i, v=eff_value: target_hook_equality_only(
                    v, info.effect(i).target_a if info.effect(i) else None, info.effect(i).target_b if info.effect(i) else None))
                if eq_mask != mask:
                    note = f"target-hook-category-rule: TargetA/B equality mask {eq_mask} != CheckEffect mask {mask}"
        else:
            mask = None  # not effect-matched: always executes
        executes = True if mask is None and note is None else bool(mask)
        if mask is None and note and note.startswith("unresolved"):
            executes = False
        return HookBinding(list=lst, kind=kind, handler=handler, line=hook["line"], eff_index=eff_index,
                           eff_name=eff_name, eff_value=eff_value, affected_mask=mask,
                           affected_effects=[i for i in range(32) if mask and mask & (1 << i)],
                           executes=executes, can_prevent_default=lst in PREVENTING_LISTS, facts=facts, note=note)

    def _build(self) -> None:
        idx = self.b.index
        cat = self.b.catalog
        for spell_id, names in self.b.script_names.items():
            info = cat.get(spell_id)
            if info is None:
                continue
            for name in names:
                res = idx.resolve_script_name(name)
                classes = []
                hooks: list[HookBinding] = []
                fields: list[str] = []
                validate: set[int] = set()
                load_gate: dict[str, Any] = {}
                for sc in res["classes"]:
                    classes.append({"name": sc.name, "kind": sc.kind, "file": sc.file, "line": sc.line})
                    fields.extend(f"{sc.name}: {f}" for f in sc.fields)
                    load = sc.methods.get("Load")
                    if load:
                        gate_calls = [c for c in load.get("calls", []) if c["callee"] in ("HasAura", "HasAuraEffect", "HasSpell", "GetAura", "GetAuraEffect", "GetScript", "GetPrimarySpecialization", "IsPlayer", "ToPlayer", "GetTypeId", "GetClass")]
                        load_gate = {"callees": sorted({c["callee"] for c in gate_calls}),
                                     "spells": sorted({v for c in gate_calls for v in c.get("ints", []) if v >= 100}),
                                     "line": load.get("line")}
                    if sc.validate:
                        validate.update(sc.validate.get("spells", []))
                    for hook in sc.hooks:
                        hooks.append(self._hook_binding(sc, hook, info))
                b = Binding(spell_id=spell_id, script_name=name, resolved=res["resolved"], classes=classes,
                            hooks=hooks, ctor_args=res["ctor_args"], registration=res["registration"],
                            fields=fields, validate_spells=sorted(validate), load_gate=load_gate)
                if not res["resolved"]:
                    self.unresolved_names[name] += 1
                    b.notes.append("no SpellScript/AuraScript registration found for this ScriptName")
                self.by_spell[spell_id].append(b)
                self.script_spells[name].add(spell_id)

    # ------------------------------------------------------------------
    def bindings(self, spell_id: int) -> list[Binding]:
        return self.by_spell.get(spell_id, [])

    def summary(self, spells: set[int] | None = None) -> dict[str, Any]:
        bs = [b for sid, lst in self.by_spell.items() if spells is None or sid in spells for b in lst]
        hook_lists = Counter(h.list for b in bs for h in b.hooks)
        exec_lists = Counter(h.list for b in bs for h in b.hooks if h.executes)
        dead = Counter(h.list for b in bs for h in b.hooks if h.affected_mask == 0)
        kinds = Counter("+".join(b.kinds) or "unresolved" for b in bs)
        return {
            "bound_spells": len({b.spell_id for b in bs}), "bindings": len(bs),
            "distinct_scripts": len({b.script_name for b in bs}),
            "unresolved_bindings": sum(1 for b in bs if not b.resolved),
            "kinds": dict(kinds.most_common()),
            "hooks_registered": dict(hook_lists.most_common()),
            "hooks_never_executing_(mask_0)": dict(dead.most_common()),
            "hooks_executing": dict(exec_lists.most_common()),
            "bindings_with_prevent_default": sum(1 for b in bs if b.to_dict()["any_prevent_default"]),
            "bindings_with_ctor_args": sum(1 for b in bs if b.ctor_args),
            "bindings_with_mutable_fields": sum(1 for b in bs if b.fields),
            "bindings_with_load_gate": sum(1 for b in bs if b.load_gate.get("callees")),
            "load_gate_callees": dict(Counter(c for b in bs for c in b.load_gate.get("callees", [])).most_common()),
            "scripts_shared_by_many_spells": {n: len(s) for n, s in self.script_spells.items()
                                              if len(s) > 1 and (spells is None or s & spells)},
        }
