#!/usr/bin/env python3
"""Extract TrinityCore aura target-map code verbatim (targeting Track J).

Same idea as ``tools/tc_target_geom_probe``: the C++ side of the differential for
``targeting/auratargets.py`` is Trinity's own text, compiled against minimal stubs
(see ``probe.cpp``).  ``Position.h`` is included directly.

Extracted verbatim (TrinityCore @ 7f3d43b):
  SpellAuras.cpp  Aura::BuildEffectMaskForOwner, Aura::UpdateTargetMap, Aura::CanBeAppliedOn,
                  Aura::CheckAreaTarget, UnitAura::FillTargetMap, UnitAura::AddStaticApplication,
                  DynObjAura::FillTargetMap, and the target-map timer statement of Aura::UpdateOwner
  SpellInfo.cpp   SpellEffectInfo::IsEffect (x2), IsAreaAuraEffect, IsUnitOwnedAuraEffect, HasRadius, CalcRadius
  Spell.cpp       Spell::GetSearcherTypeMask, WorldObjectSpellAreaTargetCheck ctor + operator()
  Object.cpp      WorldObject::IsInRange2d, WorldObject::IsInRange3d
  enums           SpellTargetCheckTypes / SpellTargetObjectTypes / SpellTargetReferenceTypes (SpellInfo.h),
                  SpellTargetIndex (SharedDefines.h), GridMapTypeMask (GridDefines.h), SpellEffectName values,
                  the SPELL_ATTR* bits and AURA_STATE_BANISHED used by the bodies (SharedDefines.h),
                  UPDATE_TARGET_MAP_INTERVAL (SpellAuras.h), EXTRA_CELL_SEARCH_RADIUS (ObjectDefines.h)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE_PARENT = Path(__file__).resolve().parents[5]
TC_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else WORKSPACE_PARENT / "TrinityCore")
OUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent)
OUT_DECLS = OUT_DIR / "tc_auramap_decls.inc"
OUT_BODIES = OUT_DIR / "tc_auramap_bodies.inc"
G = TC_ROOT / "src/server/game"

ATTRS = ("SPELL_ATTR3_ONLY_ON_PLAYER", "SPELL_ATTR3_ONLY_ON_GHOSTS", "SPELL_ATTR5_NOT_ON_PLAYER",
         "SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD", "SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE",
         "SPELL_ATTR9_NO_MOVEMENT_RADIUS_BONUS")
TARGETS = ("TARGET_DEST_CASTER_RANDOM", "TARGET_DEST_TARGET_RANDOM", "TARGET_DEST_DEST_RANDOM")


def braced(text: str, start: int) -> tuple[int, int]:
    open_brace = text.index("{", start)
    depth = 0
    for pos in range(open_brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return open_brace, pos + 1
    raise SystemExit("unbalanced braces")


def function(text: str, signature: str, source: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"signature not found in {source}: {signature}")
    if text.find(signature, start + 1) >= 0:
        raise SystemExit(f"signature not unique in {source}: {signature}")
    line = text.count("\n", 0, start) + 1
    _, b = braced(text, start)
    return f"// {source}:{line}\n{text[start:b]}\n"


def enum_block(text: str, head: str, source: str) -> str:
    start = text.index(head)
    end = text.index("};", start) + 2
    line = text.count("\n", 0, start) + 1
    return f"// {source}:{line}\n{text[start:end]}"


def statement(text: str, first: str, last: str, source: str) -> str:
    a = text.index(first)
    b = text.index(last, a)
    line = text.count("\n", 0, a) + 1
    return f"// {source}:{line}\n{text[a:b]}"


def define(text: str, name: str, source: str) -> str:
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith(f"#define {name} "):
            return f"// {source}:{n}\n{line}"
    raise SystemExit(f"#define {name} not found in {source}")


def named_values(text: str, names, source: str, enum: str, underlying: str = "uint32") -> str:
    rows = []
    for name in names:
        m = re.search(rf"^\s*({re.escape(name)})\s*=\s*([0-9A-Fa-fx]+)", text, re.MULTILINE)
        if not m:
            raise SystemExit(f"{name} not found in {source}")
        line = text.count("\n", 0, m.start()) + 1
        rows.append(f"    {name} = {m.group(2)}, // {source}:{line}")
    return f"enum {enum} : {underlying}\n{{\n" + "\n".join(rows) + "\n};"


def main() -> int:
    auras_cpp = (G / "Spells/Auras/SpellAuras.cpp").read_text(encoding="utf-8")
    auras_h = (G / "Spells/Auras/SpellAuras.h").read_text(encoding="utf-8")
    info_cpp = (G / "Spells/SpellInfo.cpp").read_text(encoding="utf-8")
    info_h = (G / "Spells/SpellInfo.h").read_text(encoding="utf-8")
    spell_cpp = (G / "Spells/Spell.cpp").read_text(encoding="utf-8")
    obj_cpp = (G / "Entities/Object/Object.cpp").read_text(encoding="utf-8")
    obj_defs = (G / "Entities/Object/ObjectDefines.h").read_text(encoding="utf-8")
    shared = (G / "Miscellaneous/SharedDefines.h").read_text(encoding="utf-8")
    grid = (G / "Grids/GridDefines.h").read_text(encoding="utf-8")

    effects = re.findall(r"^\s*(SPELL_EFFECT_[A-Z0-9_]+)\s*=\s*(\d+),", shared, re.MULTILINE)
    effect_enum = "// SharedDefines.h SpellEffectName (values)\nenum SpellEffects : uint32\n{\n" + \
        "\n".join(f"    {n} = {v}," for n, v in effects) + "\n};"
    decls = [
        "// GENERATED by tools/tc_target_auramap_probe/extract.py from TrinityCore -- do not edit",
        define(auras_h, "UPDATE_TARGET_MAP_INTERVAL", "SpellAuras.h"),
        define(obj_defs, "EXTRA_CELL_SEARCH_RADIUS", "ObjectDefines.h"),
        effect_enum,
        enum_block(info_h, "enum SpellTargetReferenceTypes", "SpellInfo.h"),
        enum_block(info_h, "enum SpellTargetObjectTypes : uint8", "SpellInfo.h"),
        enum_block(info_h, "enum SpellTargetCheckTypes : uint8", "SpellInfo.h"),
        enum_block(shared, "enum class SpellTargetIndex : uint8", "SharedDefines.h"),
        enum_block(grid, "enum GridMapTypeMask", "GridDefines.h"),
        *[named_values(shared, [a for a in ATTRS if a.startswith(f"SPELL_ATTR{w}_")], "SharedDefines.h", f"SpellAttr{w}")
          for w in sorted({a[10] for a in ATTRS})],
        named_values((G / "Spells/Auras/SpellAuraDefines.h").read_text(encoding="utf-8"),
                     ("SPELL_AURA_SUPPRESS_ITEM_PASSIVE_EFFECT_BY_SPELL_LABEL",), "SpellAuraDefines.h", "AuraType"),
        named_values(shared, TARGETS, "SharedDefines.h", "Targets"),
        named_values(shared, ("AURA_STATE_BANISHED",), "SharedDefines.h", "AuraStateType"),
    ]
    bodies = ["// GENERATED by tools/tc_target_auramap_probe/extract.py from TrinityCore -- do not edit"]
    for sig in ("bool SpellEffectInfo::IsEffect() const",
                "bool SpellEffectInfo::IsEffect(SpellEffects effectName) const",
                "bool SpellEffectInfo::IsAreaAuraEffect() const",
                "bool SpellEffectInfo::IsUnitOwnedAuraEffect() const",
                "bool SpellEffectInfo::HasRadius(SpellTargetIndex targetIndex) const",
                "SpellRange SpellEffectInfo::CalcRadius(WorldObject const* caster /*= nullptr*/, SpellTargetIndex targetIndex /*=SpellTargetIndex::TargetA*/, Spell* spell /*= nullptr*/) const"):
        bodies.append(function(info_cpp, sig, "SpellInfo.cpp"))
    for sig in ("bool WorldObject::IsInRange2d(Position const* pos, float minRange, float maxRange) const",
                "bool WorldObject::IsInRange3d(Position const* pos, float minRange, float maxRange) const"):
        bodies.append(function(obj_cpp, sig, "Object.cpp"))
    bodies.append(function(spell_cpp, "uint32 Spell::GetSearcherTypeMask(SpellInfo const* spellInfo, SpellEffectInfo const& spellEffectInfo, SpellTargetObjectTypes objType, ConditionContainer const* condList)", "Spell.cpp"))
    bodies.append("namespace Trinity\n{")
    for sig in ("WorldObjectSpellAreaTargetCheck::WorldObjectSpellAreaTargetCheck(",
                "bool WorldObjectSpellAreaTargetCheck::operator()(WorldObject* target) const"):
        bodies.append(function(spell_cpp, sig, "Spell.cpp"))
    bodies.append("}")
    for sig in ("uint32 Aura::BuildEffectMaskForOwner(SpellInfo const* spellProto, uint32 availableEffectMask, WorldObject* owner)",
                "void Aura::UpdateTargetMap(Unit* caster, bool apply)",
                "bool Aura::CanBeAppliedOn(Unit* target)",
                "bool Aura::CheckAreaTarget(Unit* target)",
                "void UnitAura::FillTargetMap(std::unordered_map<Unit*, uint32>& targets, Unit* caster)",
                "void UnitAura::AddStaticApplication(Unit* target, uint32 effMask)",
                "void DynObjAura::FillTargetMap(std::unordered_map<Unit*, uint32>& targets, Unit* /*caster*/)"):
        bodies.append(function(auras_cpp, sig, "SpellAuras.cpp"))
    timer = statement(auras_cpp, "    if (m_updateTargetMapInterval <= int32(diff))", "    // update aura effects", "SpellAuras.cpp")
    bodies.append("void Aura::UpdateOwnerTargetMapTimer(uint32 diff, Unit* caster)\n{\n" + timer + "}\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DECLS.write_text("\n\n".join(decls) + "\n", encoding="utf-8")
    OUT_BODIES.write_text("\n\n".join(bodies) + "\n", encoding="utf-8")
    print(f"wrote {OUT_DECLS.name} ({len(decls)} blocks), {OUT_BODIES.name} ({len(bodies)} blocks) from {TC_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
