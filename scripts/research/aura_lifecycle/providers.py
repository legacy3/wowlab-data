"""The shared denominator: every aura-producing (spell, effect) of the snapshot.

A *provider effect* is a ``DIFFICULTY_NONE`` ``SpellEffect`` row for which
``SpellEffectInfo::IsAura`` holds (``APPLY_AURA``, ``APPLY_AURA_ON_PET``, the
area-aura effects and ``PERSISTENT_AREA_AURA`` with a nonzero ``EffectAura``).
A *provider spell* has at least one provider effect.  Every census in the pass
counts against these lists and names the population it restricts to:

* ``all``            -- every provider in the snapshot
* ``player``         -- ``Scope.reach`` (current-player class trees, spec spells,
  current gear/sets/gems/enchants + authored trigger reach)
* ``player+class-skills`` -- ``player`` plus the spells of *class* skill lines
  and their authored trigger reach: baseline class abilities such as
  Power Word: Shield 17, Renew 139 or Bear Form 5487 that ``Scope.reach``
  omits.  A class skill line is a ``SkillLine.CategoryID = 7`` line whose
  ``SkillRaceClassInfo`` rows name exactly one player class; this drops the
  Mounts / Companions / Internal (``ClassMask -1``) and pet-family (no row)
  lines that ``Scope(include_class_skills=True)`` also admits (R1-01: 1,741 of
  its 2,125 additions were mounts).  Supplementary; ``player`` stays the
  population prior passes used.
* ``controlled``     -- current controlled-unit abilities (controlled-unit-corpora/spells.json)

Membership never implies executable support anywhere.

Mirrors: ``SpellEffectInfo::IsAura`` / ``IsUnitOwnedAuraEffect`` / ``IsAreaAuraEffect``
(SpellInfo.cpp, via :func:`procs.enums.is_aura_effect`).
"""

from __future__ import annotations

import json
from functools import lru_cache

from procs.enums import AREA_AURA_EFFECTS, is_aura_effect

from . import ROOT

CU_SPELLS = ROOT / "docs" / "research" / "controlled-unit-corpora" / "spells.json"


@lru_cache(maxsize=1)
def controlled_spells() -> frozenset[int]:
    if not CU_SPELLS.exists():
        return frozenset()
    payload = json.loads(CU_SPELLS.read_text(encoding="utf-8"))
    out: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("spell", "spell_id", "SpellID") and isinstance(v, int):
                    out.add(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(payload)
    return frozenset(out)


def provider_effects(data) -> list[dict]:
    """All DIFFICULTY_NONE aura effects, sorted by (spell, index)."""
    out = []
    for (spell, diff), effects in data.table("SpellEffect").items():
        if diff != 0:
            continue
        for index, e in effects.items():
            if is_aura_effect(e["Effect"], e["EffectAura"]):
                out.append({"spell": spell, "index": index, "effect": e["Effect"], "aura": e["EffectAura"],
                            "area": e["Effect"] in AREA_AURA_EFFECTS or e["Effect"] == 27})
    out.sort(key=lambda r: (r["spell"], r["index"]))
    return out


def provider_spells(data) -> list[int]:
    return sorted({r["spell"] for r in provider_effects(data)})


def class_skill_lines(source) -> frozenset[int]:
    """Category-7 skill lines whose SkillRaceClassInfo rows all name the same single class."""
    cat7 = {sid for sid, cat in source.project("SkillLine", ("ID", "CategoryID")) if cat == 7}
    masks: dict[int, set[int]] = {}
    for skill, mask in source.project("SkillRaceClassInfo", ("SkillID", "ClassMask")):
        if skill in cat7:
            masks.setdefault(skill, set()).add(mask)
    return frozenset(k for k, v in masks.items()
                     if len(v) == 1 and (m := next(iter(v))) > 0 and m & (m - 1) == 0)


@lru_cache(maxsize=1)
def _class_skill_reach(ctx) -> frozenset[int]:
    from dummy_semantics.scope import Scope

    lines = class_skill_lines(ctx.bundle.source)

    class _ClassScope(Scope):
        def _roots(self):
            roots = super()._roots()
            for spell in list(roots.by_spell):
                kinds = roots.by_spell[spell]
                if "class-skill" not in kinds:
                    continue
                keep = [d for d in roots.detail[spell]
                        if d["kind"] != "class-skill" or d.get("skill_line") in lines]
                roots.detail[spell] = keep
                if not any(d["kind"] == "class-skill" for d in keep):
                    kinds.discard("class-skill")
                    if not kinds:
                        del roots.by_spell[spell]
            return roots

    return frozenset(_ClassScope(ctx.bundle, include_class_skills=True).reach)


def populations(ctx, with_class_skills: bool = False) -> dict[str, frozenset[int]]:
    spells = frozenset(provider_spells(ctx.data))
    out = {
        "all": spells,
        "player": spells & frozenset(ctx.scope.reach),
        "controlled": spells & controlled_spells(),
    }
    if with_class_skills:
        out["player+class-skills"] = spells & _class_skill_reach(ctx)
    return out
