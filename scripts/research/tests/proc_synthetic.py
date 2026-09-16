"""Tiny synthetic DB2 snapshots for the proc tests.

Only the tables and columns :mod:`procs.spells` reads are written.  Every
value is invented; a test states exactly the source facts it depends on.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from procs.definition import ProcDefinitions
from procs.eligibility import Evaluator
from procs.source import Source
from procs.spells import (
    AURA_OPTIONS_COLUMNS,
    CATEGORIES_COLUMNS,
    CLASS_OPTIONS_COLUMNS,
    COOLDOWNS_COLUMNS,
    EFFECT_COLUMNS,
    EQUIPPED_COLUMNS,
    KEY_ONLY_DIFFICULTY_NONE,
    KEY_ONLY_PER_DIFFICULTY,
    MISC_COLUMNS,
    SpellCatalog,
)

APPLY_AURA = 6
DUMMY_EFFECT = 3


@dataclass
class Eff:
    index: int = 0
    effect: int = APPLY_AURA
    aura: int = 42
    trigger: int = 0
    base_points: float = 0.0
    misc0: int = 0
    class_mask: tuple[int, int, int, int] = (0, 0, 0, 0)
    target_a: int = 1
    mechanic: int = 0
    difficulty: int = 0
    scaling_class: int = 0


@dataclass
class Spell:
    id: int
    name: str = ""
    effects: list[Eff] = field(default_factory=list)
    proc_flags: int = 0              # 64-bit: word0 | word1 << 32
    proc_chance: int = 0
    proc_charges: int = 0
    proc_cooldown: int = 0
    stack: int = 0
    ppm_id: int = 0
    attributes: dict[int, int] = field(default_factory=dict)
    school: int = 1
    dmg_class: int = 1
    family: int = 0
    family_flags: tuple[int, int, int, int] | None = None
    equipped_class: int | None = None
    equipped_subclass: int = 0
    duration_index: int = 0
    options_difficulty: int = 0
    has_options: bool = True
    mechanic: int = 0


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def write(path: Path, header, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        w.writerow(header)
        w.writerows(rows)


def build(tmp: Path, spells: list[Spell], ppm: list[tuple[int, float, int]] = (),
          ppm_mods: list[tuple[int, int, int, float, int]] = (),
          difficulties: list[tuple[int, int]] = ((1, 0), (2, 1)),
          ranks: list[tuple[int, int]] = ()) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    write(tmp / "SpellName.csv", ["ID", "Name_lang"], [[s.id, s.name or f"spell{s.id}"] for s in spells])
    write(tmp / "Difficulty.csv", ["ID", "FallbackDifficultyID"], list(difficulties))
    eff_rows, opt_rows, misc_rows, cat_rows, cls_rows, eq_rows = [], [], [], [], [], []
    rid = 1
    for s in spells:
        for e in s.effects:
            row = {
                "SpellID": s.id, "DifficultyID": e.difficulty, "EffectIndex": e.index,
                "Effect": e.effect, "EffectAura": e.aura, "EffectTriggerSpell": e.trigger,
                "EffectBasePointsF": e.base_points, "EffectMiscValue_0": e.misc0,
                "EffectMiscValue_1": 0, "ImplicitTarget_0": e.target_a, "ImplicitTarget_1": 0,
                "EffectAuraPeriod": 0, "EffectMechanic": e.mechanic, "EffectAmplitude": 1,
                "EffectAttributes": 0, "ID": rid, "ScalingClass": e.scaling_class,
            }
            for i in range(4):
                row[f"EffectSpellClassMask_{i}"] = _s32(e.class_mask[i])
            eff_rows.append(row)
            rid += 1
        if s.has_options:
            opt_rows.append({"ID": s.id, "SpellID": s.id, "DifficultyID": s.options_difficulty,
                             "CumulativeAura": s.stack, "ProcCategoryRecovery": s.proc_cooldown,
                             "ProcChance": s.proc_chance, "ProcCharges": s.proc_charges,
                             "SpellProcsPerMinuteID": s.ppm_id,
                             "ProcTypeMask_0": _s32(s.proc_flags & 0xFFFFFFFF),
                             "ProcTypeMask_1": _s32(s.proc_flags >> 32)})
        misc = {"ID": s.id, "SpellID": s.id, "DifficultyID": 0, "SchoolMask": s.school,
                "DurationIndex": s.duration_index}
        for i in range(17):
            misc[f"Attributes_{i}"] = _s32(s.attributes.get(i, 0))
        misc_rows.append(misc)
        cat_rows.append({"ID": s.id, "SpellID": s.id, "DifficultyID": 0, "Category": 0,
                         "DefenseType": s.dmg_class, "DispelType": 0, "Mechanic": s.mechanic,
                         "StartRecoveryCategory": 0, "ChargeCategory": 0, "PreventionType": 0})
        if s.family or s.family_flags:
            flags = s.family_flags or (0, 0, 0, 0)
            cls_rows.append({"ID": s.id, "SpellID": s.id, "SpellClassSet": s.family,
                             **{f"SpellClassMask_{i}": _s32(flags[i]) for i in range(4)}})
        if s.equipped_class is not None:
            eq_rows.append({"ID": s.id, "SpellID": s.id, "EquippedItemClass": s.equipped_class,
                            "EquippedItemSubclass": s.equipped_subclass, "EquippedItemInvTypes": 0})

    def dump(name, cols, rows):
        write(tmp / f"{name}.csv", list(cols) + [c for c in ("ScalingClass",) if name == "SpellEffect"],
              [[r.get(c, 0) for c in list(cols) + (["ScalingClass"] if name == "SpellEffect" else [])]
               for r in rows])

    dump("SpellEffect", EFFECT_COLUMNS, eff_rows)
    dump("SpellAuraOptions", AURA_OPTIONS_COLUMNS, opt_rows)
    dump("SpellMisc", MISC_COLUMNS, misc_rows)
    dump("SpellCategories", CATEGORIES_COLUMNS, cat_rows)
    dump("SpellCooldowns", COOLDOWNS_COLUMNS, [])
    dump("SpellClassOptions", CLASS_OPTIONS_COLUMNS, cls_rows)
    dump("SpellEquippedItems", EQUIPPED_COLUMNS, eq_rows)
    write(tmp / "SpellLabel.csv", ["SpellID", "LabelID"], [])
    write(tmp / "SpellDuration.csv", ["ID", "Duration"], [[1, 10000]])
    write(tmp / "SpellProcsPerMinute.csv", ["ID", "BaseProcRate", "Flags"], [list(p) for p in ppm])
    write(tmp / "SpellProcsPerMinuteMod.csv", ["ID", "Type", "Param", "Coeff", "SpellProcsPerMinuteID"],
          [list(m) for m in ppm_mods])
    for t in KEY_ONLY_PER_DIFFICULTY:
        write(tmp / f"{t}.csv", ["SpellID", "DifficultyID"], [])
    for t in KEY_ONLY_DIFFICULTY_NONE:
        write(tmp / f"{t}.csv", ["SpellID"], [])
    write(tmp / "SpellPowerDifficulty.csv", ["ID", "DifficultyID"], [])
    write(tmp / "SpellPower.csv", ["ID", "SpellID"], [])
    write(tmp / "SkillLineAbility.csv", ["Spell", "SupercedesSpell"], [list(r) for r in ranks])
    return tmp


class FakeOverlay:
    """Duck-typed stand-in for :class:`procs.trinity.TrinityOverlay`."""

    def __init__(self, spell_proc=None, scripts=None, conditions=None, corrections=None,
                 custom_attributes=None) -> None:
        self.spell_proc = spell_proc or {}
        self._scripts = scripts or {}
        self.conditions = conditions or {}
        self.corrections = corrections or {}
        self.custom_attributes = custom_attributes or {}
        self.enchant_proc = {}
        self.item_spell_ppm = {}
        self.provenance = {"synthetic": True}

    def bind_ranks(self, catalog) -> None:
        pass

    def script_names(self, spell_id):
        return list(self._scripts.get(spell_id, {}).get("script_names", []))

    def script_hooks(self, spell_id) -> dict[str, Any]:
        base = {"script_names": [], "proc_hooks": [], "effect_proc_bindings": [],
                "any_prevent_default_action": False, "unresolved_script_names": [], "files": []}
        base.update(self._scripts.get(spell_id, {}))
        return base

    def correction_members(self, spell_id):
        return self.corrections.get(spell_id, [])


def world(tmp: Path, spells: list[Spell], overlay: FakeOverlay | None = None, **kwargs):
    root = build(tmp, spells, **kwargs)
    source = Source(root)
    catalog = SpellCatalog(source, overlay.custom_attributes if overlay else None)
    defs = ProcDefinitions(catalog, overlay)
    return catalog, defs, Evaluator(catalog)
