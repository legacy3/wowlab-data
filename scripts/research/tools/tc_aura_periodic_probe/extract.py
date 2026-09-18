#!/usr/bin/env python3
"""Extract TrinityCore periodic-timer code verbatim (aura-lifecycle Track D).

The C++ side of the differential for ``aura_lifecycle/periodic.py`` is Trinity's own text,
compiled against minimal stubs (see ``probe.cpp``).

Extracted verbatim (TrinityCore @ 7f3d43b):
  SpellAuraEffects.cpp  AuraEffect::GetTotalTicks, AuraEffect::ResetPeriodic, AuraEffect::CalculatePeriodic,
                        AuraEffect::Update
  SpellAuras.cpp        Aura::UpdateOwner, Aura::Update, Aura::SetDuration, Aura::RefreshDuration,
                        Aura::RefreshTimers, Aura::CalcMaxStackAmount, Aura::ModStackAmount, Aura::IsPassive
  SpellInfo.cpp         SpellInfo::IsChanneled, SpellInfo::IsPassive
  Object.cpp            WorldObject::ModSpellDurationTime
  Spell.cpp             DoSpellEffectHit: the resetPeriodicTimer statement (3240) and the duration override
                        blocks `if (hitInfo.AuraDuration > 0) {...}` / `if (hitInfo.AuraDuration != ...) {...}`
  Unit.cpp              Unit::_UpdateSpells: the owned-aura update loop and the expiry loop
  Util.h                CalculatePct
  enums                 AuraType, AuraRemoveMode (SpellAuraDefines.h), SpellModOp (SpellDefines.h),
                        TriggerCastFlags (SpellDefines.h), Powers, WeaponAttackType, the SPELL_ATTR* bits
                        used by the bodies (SharedDefines.h), TypeID (ObjectGuid.h), TimeConstants (Common.h)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE_PARENT = Path(__file__).resolve().parents[5]
TC_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else WORKSPACE_PARENT / "TrinityCore")
OUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent)
OUT_DECLS = OUT_DIR / "tc_periodic_decls.inc"
OUT_BODIES = OUT_DIR / "tc_periodic_bodies.inc"
G = TC_ROOT / "src/server/game"

ATTRS = ("SPELL_ATTR0_PASSIVE", "SPELL_ATTR0_IS_ABILITY", "SPELL_ATTR0_IS_TRADESKILL", "SPELL_ATTR0_USES_RANGED_SLOT",
         "SPELL_ATTR1_IS_CHANNELLED", "SPELL_ATTR1_IS_SELF_CHANNELLED", "SPELL_ATTR1_AURA_UNIQUE",
         "SPELL_ATTR2_AUTO_REPEAT", "SPELL_ATTR2_NO_TARGET_PER_SECOND_COSTS",
         "SPELL_ATTR3_IGNORE_CASTER_MODIFIERS",
         "SPELL_ATTR5_EXTRA_INITIAL_PERIOD", "SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC", "SPELL_ATTR5_AURA_UNIQUE_PER_CASTER",
         "SPELL_ATTR8_HASTE_AFFECTS_DURATION", "SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC",
         "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION")


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


def unique(text: str, needle: str, source: str) -> int:
    start = text.find(needle)
    if start < 0:
        raise SystemExit(f"not found in {source}: {needle}")
    if text.find(needle, start + 1) >= 0:
        raise SystemExit(f"not unique in {source}: {needle}")
    return start


def function(text: str, signature: str, source: str) -> str:
    start = unique(text, signature, source)
    line = text.count("\n", 0, start) + 1
    _, b = braced(text, start)
    return f"// {source}:{line}\n{text[start:b]}\n"


def block(text: str, head: str, source: str) -> str:
    """A braced statement starting at ``head`` (e.g. an ``if (...) {...}``)."""
    start = unique(text, head, source)
    line = text.count("\n", 0, start) + 1
    _, b = braced(text, start)
    return f"// {source}:{line}\n{text[start:b]}\n"


def statement(text: str, first: str, last: str, source: str) -> str:
    a = unique(text, first, source)
    b = text.index(last, a)
    line = text.count("\n", 0, a) + 1
    return f"// {source}:{line}\n{text[a:b]}"


def enum_block(text: str, head: str, source: str) -> str:
    start = unique(text, head, source)
    end = text.index("};", start) + 2
    line = text.count("\n", 0, start) + 1
    return f"// {source}:{line}\n{text[start:end]}"


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
    eff_cpp = (G / "Spells/Auras/SpellAuraEffects.cpp").read_text(encoding="utf-8")
    auras_cpp = (G / "Spells/Auras/SpellAuras.cpp").read_text(encoding="utf-8")
    aura_defs = (G / "Spells/Auras/SpellAuraDefines.h").read_text(encoding="utf-8")
    spell_defs = (G / "Spells/SpellDefines.h").read_text(encoding="utf-8")
    info_cpp = (G / "Spells/SpellInfo.cpp").read_text(encoding="utf-8")
    spell_cpp = (G / "Spells/Spell.cpp").read_text(encoding="utf-8")
    obj_cpp = (G / "Entities/Object/Object.cpp").read_text(encoding="utf-8")
    unit_cpp = (G / "Entities/Unit/Unit.cpp").read_text(encoding="utf-8")
    guid_h = (G / "Entities/Object/ObjectGuid.h").read_text(encoding="utf-8")
    shared = (G / "Miscellaneous/SharedDefines.h").read_text(encoding="utf-8")
    common_h = (TC_ROOT / "src/common/Common.h").read_text(encoding="utf-8")
    util_h = (TC_ROOT / "src/common/Utilities/Util.h").read_text(encoding="utf-8")

    decls = [
        "// GENERATED by tools/tc_aura_periodic_probe/extract.py from TrinityCore -- do not edit",
        enum_block(common_h, "enum TimeConstants", "Common.h"),
        enum_block(aura_defs, "enum AuraRemoveMode", "SpellAuraDefines.h"),
        enum_block(aura_defs, "enum AuraType : uint32", "SpellAuraDefines.h"),
        enum_block(spell_defs, "enum class SpellModOp : uint8", "SpellDefines.h"),
        enum_block(spell_defs, "enum TriggerCastFlags : uint32", "SpellDefines.h"),
        enum_block(shared, "enum Powers : int8", "SharedDefines.h"),
        enum_block(shared, "enum WeaponAttackType : uint8", "SharedDefines.h"),
        enum_block(guid_h, "enum TypeID : uint8", "ObjectGuid.h"),
        *[named_values(shared, [a for a in ATTRS if a.startswith(f"SPELL_ATTR{w}_")], "SharedDefines.h", f"SpellAttr{w}")
          for w in sorted({int(re.match(r"SPELL_ATTR(\d+)_", a).group(1)) for a in ATTRS})],
        statement(util_h, "template <class T, class U>\ninline T CalculatePct", "\ntemplate <class T>\ninline float GetPctOf", "Util.h"),
    ]
    bodies = ["// GENERATED by tools/tc_aura_periodic_probe/extract.py from TrinityCore -- do not edit"]
    bodies.append(function(info_cpp, "bool SpellInfo::IsPassive() const", "SpellInfo.cpp"))
    bodies.append(function(info_cpp, "bool SpellInfo::IsChanneled() const", "SpellInfo.cpp"))
    bodies.append(function(obj_cpp, "void WorldObject::ModSpellDurationTime(SpellInfo const* spellInfo, int32& duration, Spell* spell /*= nullptr*/) const", "Object.cpp"))
    for sig in ("uint32 AuraEffect::GetTotalTicks() const",
                "void AuraEffect::ResetPeriodic(bool resetPeriodicTimer /*= false*/)",
                "void AuraEffect::CalculatePeriodic(Unit* caster, bool resetPeriodicTimer /*= true*/, bool load /*= false*/)",
                "void AuraEffect::Update(uint32 diff, Unit* caster)"):
        bodies.append(function(eff_cpp, sig, "SpellAuraEffects.cpp"))
    for sig in ("void Aura::UpdateOwner(uint32 diff, WorldObject* owner)",
                "void Aura::Update(uint32 diff, Unit* caster)",
                "void Aura::SetDuration(int32 duration, bool withMods)",
                "void Aura::RefreshDuration(bool withMods)",
                "void Aura::RefreshTimers(bool resetPeriodicTimer)",
                "uint32 Aura::CalcMaxStackAmount() const",
                "bool Aura::ModStackAmount(int32 num, AuraRemoveMode removeMode /*= AURA_REMOVE_BY_DEFAULT*/, bool resetPeriodicTimer /*= true*/)",
                "bool Aura::IsPassive() const"):
        bodies.append(function(auras_cpp, sig, "SpellAuras.cpp"))
    reset = statement(spell_cpp, "                bool const resetPeriodicTimer = (m_spellInfo->StackAmount < 2)",
                      "\n                uint32 const allAuraEffectMask", "Spell.cpp")
    bodies.append("bool Spell::ProbeResetPeriodicTimer() const\n{\n" + reset + "\n    return resetPeriodicTimer;\n}\n")
    dur = block(spell_cpp, "if (hitInfo.AuraDuration > 0)", "Spell.cpp")
    setmax = block(spell_cpp, "if (hitInfo.AuraDuration != hitInfo.HitAura->GetMaxDuration())", "Spell.cpp")
    bodies.append("void Spell::ProbeHitDuration(WorldObject* caster, TargetInfo& hitInfo, bool refresh)\n{\n"
                  + dur + "\n" + setmax + "}\n")
    loops = statement(unit_cpp, "    // m_auraUpdateIterator can be updated in indirect called code",
                      "    for (AuraApplication* visibleAura : m_visibleAurasToUpdate)", "Unit.cpp")
    bodies.append("void Unit::ProbeUpdateOwnedAuras(uint32 time)\n{\n" + loops + "}\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DECLS.write_text("\n\n".join(decls) + "\n", encoding="utf-8")
    OUT_BODIES.write_text("\n\n".join(bodies) + "\n", encoding="utf-8")
    print(f"wrote {OUT_DECLS.name} ({len(decls)} blocks), {OUT_BODIES.name} ({len(bodies)} blocks) from {TC_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
