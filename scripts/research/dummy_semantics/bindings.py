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

# SpellImplicitTargetInfo category/object/reference tables are needed for TargetHook::CheckEffect.
# They are transcribed in procs? no -- fail closed: TargetHook masks are computed only on
# TargetA/TargetB equality; the area/dest refinement is reported as "target-hook-approximate".


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
                def check(i: int, v=eff_value) -> bool:  # SpellScript::TargetHook::CheckEffect (TargetA/B equality only)
                    e = info.effect(i)
                    return e is not None and (e.target_a == v or e.target_b == v)
                mask = affected_mask(info, eff_index, check)
                note = "target-hook-approximate: selection-category refinement of TargetHook::CheckEffect not ported"
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
