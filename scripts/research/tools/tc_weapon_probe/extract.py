#!/usr/bin/env python3
"""Extract TrinityCore weapon/damage arithmetic verbatim into generated headers.

Same principle as ``tools/tc_probe`` and ``tools/tc_proc_probe``: the C++ side
of the differential test is *Trinity's own text* compiled against minimal
stubs, never a retyping.  Two kinds of extraction are used and every piece is
labelled in the generated file:

``extracted`` (whole function body, brace matched from its signature):
  Unit.cpp        Unit::GetAPMultiplier, Unit::GetTotalAttackPowerValue,
                  Unit::GetWeaponDamageRange, Unit::CalculateDamage
  StatSystem.cpp  Player::CalculateMinMaxDamage
  Player.cpp      Player::GetBlockPercent
  Util.h          CalculatePct / GetPctOf / AddPct / ApplyPct / RoundToInterval
  SharedDefines.h GetMaxLevelForExpansion (+ enum Expansions), Classes,
                  WeaponAttackType
  ItemTemplate.h  ItemSubclassWeapon
  Unit.h          VictimState, UnitModifierFlatType, UnitModifierPctType,
                  WeaponDamageRange, UnitMods, CombatRating, MeleeHitOutcome,
                  CalcDamageInfo
  UnitDefines.h   HitInfo, BASE_MINDAMAGE/BASE_MAXDAMAGE/BASE_ATTACK_TIME
  DBCEnums.h      ExpectedStatType
  SpellAuraDefines.h AuraType, ShapeshiftForm
  g3dmath.h       eps(double,double), fuzzyLe(double,double), fuzzyEpsilon64

``cut`` (a documented text range inside a larger function whose remaining
parts are aura/object plumbing the probe cannot host):
  Unit::CalcArmorReducedDamage   [ArP-cap]  from "// no more than 100%" to
                                            "armor -= CalculatePct(maxArmorPen, arpPct);"
  Unit::CalcArmorReducedDamage   [tail]     from "if (G3D::fuzzyLe(armor, 0.0f))" to
                                            the final "return uint32(std::max(damage * (1.0f - mitigation), 0.0f));"
  Unit::CalculateMeleeDamage     [outcome]  the whole "switch (damageInfo->HitOutCome)" statement
  Spell::EffectWeaponDmg         [loops]    from "bool normalized = false;" to
                                            "weaponDamage = std::max(std::round(weaponDamage), 0.0);"
                                            (both effect loops, addPctMods, CalculateDamage call, rounding);
                                            the three locals declared above the cut (totalDamagePercentMod,
                                            fixed_bonus, spell_bonus) are re-typed with their source initialisers

Anything the probe adds on top of these (the ``bonus``, ``taken`` and
``special`` commands in probe.cpp) is a *re-typed* expression and is labelled
``re-typed`` there; it is weaker evidence than an extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE_PARENT = Path(__file__).resolve().parents[4].parent
TC_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else WORKSPACE_PARENT / "TrinityCore")
OUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent)
OUT_DECLS = OUT_DIR / "tc_weapon_decls.inc"
OUT_BODIES = OUT_DIR / "tc_weapon_bodies.inc"
G = TC_ROOT / "src/server/game"


def read(rel: str) -> str:
    return (G / rel).read_text(encoding="utf-8")


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


def enum_block(text: str, header: str) -> str:
    start = text.index(header)
    _, end = braced(text, start)
    return text[start:end] + ";"


def body(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"signature not found: {signature}")
    a, b = braced(text, start)
    return text[a:b]


def between(text: str, first: str, last: str, *, after: str | None = None) -> str:
    base = text.index(after) if after else 0
    a = text.index(first, base)
    b = text.index(last, a) + len(last)
    return text[a:b]


def cut(text: str, function_signature: str, first: str, last: str) -> str:
    """A text range strictly inside the named function (the cut points are documented above)."""
    start = text.index(function_signature)
    open_brace, end = braced(text, start)
    inner = text[open_brace:end]
    a = inner.index(first)
    b = inner.index(last, a) + len(last)
    return inner[a:b]


def main() -> int:
    unit_cpp = read("Entities/Unit/Unit.cpp")
    unit_h = read("Entities/Unit/Unit.h")
    unit_defines = read("Entities/Unit/UnitDefines.h")
    stat_cpp = read("Entities/Unit/StatSystem.cpp")
    player_cpp = read("Entities/Player/Player.cpp")
    shared = read("Miscellaneous/SharedDefines.h")
    dbc_enums = read("DataStores/DBCEnums.h")
    aura_defs = read("Spells/Auras/SpellAuraDefines.h")
    item_template_h = read("Entities/Item/ItemTemplate.h")
    spell_effects_cpp = read("Spells/SpellEffects.cpp")
    spell_defines_h = read("Spells/SpellDefines.h")
    util_h = (TC_ROOT / "src/common/Utilities/Util.h").read_text(encoding="utf-8")
    g3d = (TC_ROOT / "dep/g3dlite/include/G3D/g3dmath.h").read_text(encoding="utf-8")

    decls = [
        "// GENERATED by extract.py -- do not edit.  Every block is verbatim TrinityCore text.",
        f"// TrinityCore: {TC_ROOT}",
        "",
        "// --- SharedDefines.h ---",
        enum_block(shared, "enum Expansions"),
        "",
        body(shared, "constexpr uint32 GetMaxLevelForExpansion(uint32 expansion)").join(
            ["constexpr uint32 GetMaxLevelForExpansion(uint32 expansion)\n", "\n"]),
        enum_block(shared, "enum Classes : uint8"),
        "",
        enum_block(shared, "enum WeaponAttackType : uint8"),
        "",
        enum_block(shared, "enum SpellSchools : uint16"),
        "",
        enum_block(shared, "enum SpellSchoolMask : uint32"),
        "",
        enum_block(shared, "enum SpellAttr6 : uint32"),
        "",
        enum_block(shared, "enum Mechanics : uint32"),
        "",
        enum_block(shared, "enum SpellEffects\n"),
        "",
        "// --- SpellDefines.h ---",
        between(spell_defines_h, "using SpellEffectValue = double;", "\n"),
        "",
        "// --- ItemTemplate.h ---",
        enum_block(item_template_h, "enum ItemSubclassWeapon"),
        "",
        "// --- Unit.h ---",
        enum_block(unit_h, "enum VictimState"),
        "",
        enum_block(unit_h, "enum UnitModifierFlatType"),
        "",
        enum_block(unit_h, "enum UnitModifierPctType"),
        "",
        enum_block(unit_h, "enum WeaponDamageRange"),
        "",
        enum_block(unit_h, "enum UnitMods"),
        "",
        enum_block(unit_h, "enum CombatRating"),
        "",
        enum_block(unit_h, "enum MeleeHitOutcome : uint8"),
        "",
        "// --- UnitDefines.h ---",
        between(unit_defines, "#define BASE_MINDAMAGE", "#define BASE_ATTACK_TIME 2000"),
        "",
        enum_block(unit_defines, "enum HitInfo"),
        "",
        "// --- DBCEnums.h ---",
        enum_block(dbc_enums, "enum class ExpectedStatType : uint8"),
        "",
        "// --- SpellAuraDefines.h ---",
        enum_block(aura_defs, "enum AuraType : uint32"),
        "",
        enum_block(aura_defs, "enum ShapeshiftForm"),
        "",
        "// --- Util.h (percent helpers) ---",
        between(util_h, "template <class T, class U>\ninline T CalculatePct",
                "inline T RoundToInterval(T& num, T floor, T ceil)\n{\n    return num = std::min(std::max(num, floor), ceil);\n}"),
        "",
        "// --- g3dmath.h ---",
        "namespace G3D {",
        between(g3d, "#define fuzzyEpsilon64", "\n"),
        "using std::abs;  // stub: G3D has its own abs overloads; std::abs(double) is the same function",
        "inline double inf() { return std::numeric_limits<double>::infinity(); }  // stub (G3D::inf)",
        "inline double eps(double a, double b)\n" + body(g3d, "inline double eps(double a, double b)"),
        "inline bool fuzzyLe(double a, double b)\n" + body(g3d, "inline bool fuzzyLe(double a, double b)"),
        "}",
        "",
        "// --- Unit.h: CalcDamageInfo (ProcFlagsInit is stubbed as uint32 by probe.cpp) ---",
        enum_block(unit_h, "struct CalcDamageInfo\n{"),
        "",
    ]

    bodies = [
        "// GENERATED by extract.py -- do not edit.  Bodies are verbatim TrinityCore text;",
        "// the probe supplies the class scaffolding around them.",
        "",
        "// [extracted] Unit.cpp",
        "float Unit::GetAPMultiplier(WeaponAttackType attType, bool normalized) const",
        body(unit_cpp, "float Unit::GetAPMultiplier(WeaponAttackType attType, bool normalized) const"),
        "",
        "// [extracted] Unit.cpp",
        "float Unit::GetTotalAttackPowerValue(WeaponAttackType attType, bool includeWeapon) const",
        body(unit_cpp, "float Unit::GetTotalAttackPowerValue(WeaponAttackType attType, bool includeWeapon /*= true*/) const"),
        "",
        "// [extracted] Unit.cpp",
        "float Unit::GetWeaponDamageRange(WeaponAttackType attType, WeaponDamageRange type) const",
        body(unit_cpp, "float Unit::GetWeaponDamageRange(WeaponAttackType attType, WeaponDamageRange type) const"),
        "",
        "// [extracted] Unit.cpp",
        "uint32 Unit::CalculateDamage(WeaponAttackType attType, bool normalized, bool addTotalPct) const",
        body(unit_cpp, "uint32 Unit::CalculateDamage(WeaponAttackType attType, bool normalized, bool addTotalPct) const"),
        "",
        "// [extracted] StatSystem.cpp",
        "void Player::CalculateMinMaxDamage(WeaponAttackType attType, bool normalized, bool addTotalPct, float& minDamage, float& maxDamage) const",
        body(stat_cpp, "void Player::CalculateMinMaxDamage(WeaponAttackType attType, bool normalized, bool addTotalPct, float& minDamage, float& maxDamage) const"),
        "",
        "// [extracted] Player.cpp",
        "float Player::GetBlockPercent(uint8 attackerLevel) const",
        body(player_cpp, "float Player::GetBlockPercent(uint8 attackerLevel) const"),
        "",
        "// [cut] Unit.cpp Unit::CalcArmorReducedDamage, armor-penetration cap block",
        "static float CalcArmorReducedDamage_ArpCap(Unit const* attacker, Unit* victim, float armor, float arpPct)",
        "{",
        cut(unit_cpp,
            "/*static*/ uint32 Unit::CalcArmorReducedDamage(Unit const* attacker, Unit* victim, uint32 damage, SpellInfo const* spellInfo, WeaponAttackType attackType /*= MAX_ATTACK*/, uint8 attackerLevel /*= 0*/)",
            "            // no more than 100%", "            armor -= CalculatePct(maxArmorPen, arpPct);"),
        "    return armor;",
        "}",
        "",
        "// [cut] Unit.cpp Unit::CalcArmorReducedDamage, tail (armor constant, diminishing curve, mitigation)",
        "static uint32 CalcArmorReducedDamage_Tail(Unit const* attacker, Unit* victim, uint32 damage, uint8 attackerLevel, float armor)",
        "{",
        cut(unit_cpp,
            "/*static*/ uint32 Unit::CalcArmorReducedDamage(Unit const* attacker, Unit* victim, uint32 damage, SpellInfo const* spellInfo, WeaponAttackType attackType /*= MAX_ATTACK*/, uint8 attackerLevel /*= 0*/)",
            "    if (G3D::fuzzyLe(armor, 0.0f))", "    return uint32(std::max(damage * (1.0f - mitigation), 0.0f));"),
        "}",
        "",
        "// [cut] SpellEffects.cpp Spell::EffectWeaponDmg, both effect loops through the rounding",
        "SpellEffectValue Spell::WeaponDmgCut(Unit* unitCaster, uint32 effectMask, bool& outNormalized, bool& outAddPctMods)",
        "{",
        "    float totalDamagePercentMod  = 1.0f;   // [re-typed initialiser, SpellEffects.cpp above the cut]",
        "    SpellEffectValue fixed_bonus = 0;      // [re-typed initialiser]",
        "    SpellEffectValue spell_bonus = 0;      // [re-typed initialiser]",
        cut(spell_effects_cpp, "void Spell::EffectWeaponDmg()",
            "    bool normalized = false;", "    weaponDamage = std::max(std::round(weaponDamage), 0.0);"),
        "    outNormalized = normalized;",
        "    outAddPctMods = addPctMods;",
        "    return weaponDamage;",
        "}",
        "",
        "// [cut] Unit.cpp Unit::CalculateMeleeDamage, the outcome switch",
        "void Unit::ApplyMeleeOutcome(Unit* victim, CalcDamageInfo* damageInfo) const",
        "{",
        cut(unit_cpp,
            "void Unit::CalculateMeleeDamage(Unit* victim, CalcDamageInfo* damageInfo, WeaponAttackType attackType /*= BASE_ATTACK*/)",
            "    switch (damageInfo->HitOutCome)", "    }\n\n    // Always apply HITINFO_AFFECTS_VICTIM").removesuffix("\n\n    // Always apply HITINFO_AFFECTS_VICTIM"),
        "}",
        "",
    ]

    OUT_DECLS.write_text("\n".join(decls) + "\n", encoding="utf-8")
    OUT_BODIES.write_text("\n".join(bodies) + "\n", encoding="utf-8")
    print(f"wrote {OUT_DECLS} and {OUT_BODIES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
