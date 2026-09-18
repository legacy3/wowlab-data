#!/usr/bin/env python3
"""Extract TrinityCore aura duration / refresh / pandemic code verbatim (aura-lifecycle track B).

Same idea as ``tools/tc_target_auramap_probe``: the C++ side of the differential for
``aura_lifecycle/duration.py`` + ``aura_lifecycle/refresh.py`` is Trinity's own text,
compiled against minimal stubs (``probe.cpp``).

Extracted verbatim (TrinityCore @ 7f3d43b):
  Util.h          CalculatePct, AddPct
  SpellInfo.cpp   SpellInfo::IsPassive, IsChanneled, GetDuration, GetMaxDuration
  Object.cpp      WorldObject::CalcSpellDuration, WorldObject::ModSpellDurationTime
  SpellAuras.cpp  Aura::Update, Aura::CalcMaxDuration (both), Aura::SetDuration, Aura::RefreshDuration,
                  Aura::RefreshTimers, Aura::CalcMaxStackAmount, Aura::ModStackAmount
  SpellAuraEffects.cpp  AuraEffect::GetTotalTicks, ResetPeriodic, CalculatePeriodic, Update
  Spell.cpp       the resetPeriodicTimer statement (3240) and the post-TryRefreshStackOrCreate
                  duration commit of DoSpellEffectHit (3259-3298: ModSpellDuration .. SetDuration),
                  wrapped as Spell::CommitAuraDuration
  enums           SpellModOp (SpellDefines.h), AuraRemoveMode (SpellAuraDefines.h), the SPELL_ATTR*
                  bits and SPELL_AURA_* values the bodies use (SharedDefines.h / SpellAuraDefines.h),
                  SPELL_EMPOWER_HOLD_TIME_AT_MAX (Spell.h), TRIGGERED_DONT_RESET_PERIODIC_TIMER (SpellDefines.h)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE_PARENT = Path(__file__).resolve().parents[5]
TC_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else WORKSPACE_PARENT / "TrinityCore")
OUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent)
OUT_DECLS = OUT_DIR / "tc_duration_decls.inc"
OUT_BODIES = OUT_DIR / "tc_duration_bodies.inc"
G = TC_ROOT / "src/server/game"

ATTRS = ("SPELL_ATTR0_USES_RANGED_SLOT", "SPELL_ATTR0_IS_ABILITY", "SPELL_ATTR0_IS_TRADESKILL", "SPELL_ATTR0_PASSIVE",
         "SPELL_ATTR1_IS_CHANNELLED", "SPELL_ATTR1_IS_SELF_CHANNELLED", "SPELL_ATTR1_AURA_UNIQUE",
         "SPELL_ATTR2_AUTO_REPEAT", "SPELL_ATTR2_NO_TARGET_PER_SECOND_COSTS",
         "SPELL_ATTR3_IGNORE_CASTER_MODIFIERS",
         "SPELL_ATTR5_EXTRA_INITIAL_PERIOD", "SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC", "SPELL_ATTR5_AURA_UNIQUE_PER_CASTER",
         "SPELL_ATTR8_HASTE_AFFECTS_DURATION", "SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC",
         "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION")
AURAS = ("SPELL_AURA_OBS_MOD_POWER", "SPELL_AURA_PERIODIC_DAMAGE", "SPELL_AURA_PERIODIC_HEAL", "SPELL_AURA_OBS_MOD_HEALTH",
         "SPELL_AURA_PERIODIC_TRIGGER_SPELL", "SPELL_AURA_PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "SPELL_AURA_PERIODIC_ENERGIZE",
         "SPELL_AURA_PERIODIC_LEECH", "SPELL_AURA_PERIODIC_HEALTH_FUNNEL", "SPELL_AURA_PERIODIC_MANA_LEECH",
         "SPELL_AURA_PERIODIC_DAMAGE_PERCENT", "SPELL_AURA_POWER_BURN", "SPELL_AURA_PERIODIC_DUMMY",
         "SPELL_AURA_PERIODIC_TRIGGER_SPELL_WITH_VALUE", "SPELL_AURA_MOD_STAT")


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


def statement(text: str, first: str, last: str, source: str) -> tuple[str, int]:
    a = text.index(first)
    if text.find(first, a + 1) >= 0:
        raise SystemExit(f"statement start not unique in {source}: {first!r}")
    b = text.index(last, a)
    line = text.count("\n", 0, a) + 1
    return text[a:b], line


def enum_block(text: str, head: str, source: str) -> str:
    start = text.index(head)
    end = text.index("};", start) + 2
    line = text.count("\n", 0, start) + 1
    return f"// {source}:{line}\n{text[start:end]}"


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


def template(text: str, name: str, source: str) -> str:
    m = re.search(rf"template <class T, class U>\ninline T {name}\(", text)
    if not m:
        raise SystemExit(f"template {name} not found in {source}")
    line = text.count("\n", 0, m.start()) + 1
    _, b = braced(text, m.start())
    return f"// {source}:{line}\n{text[m.start():b]}\n"


def main() -> int:
    util_h = (TC_ROOT / "src/common/Utilities/Util.h").read_text(encoding="utf-8")
    auras_cpp = (G / "Spells/Auras/SpellAuras.cpp").read_text(encoding="utf-8")
    effects_cpp = (G / "Spells/Auras/SpellAuraEffects.cpp").read_text(encoding="utf-8")
    aura_defs = (G / "Spells/Auras/SpellAuraDefines.h").read_text(encoding="utf-8")
    info_cpp = (G / "Spells/SpellInfo.cpp").read_text(encoding="utf-8")
    spell_cpp = (G / "Spells/Spell.cpp").read_text(encoding="utf-8")
    spell_h = (G / "Spells/Spell.h").read_text(encoding="utf-8")
    spell_defs = (G / "Spells/SpellDefines.h").read_text(encoding="utf-8")
    obj_cpp = (G / "Entities/Object/Object.cpp").read_text(encoding="utf-8")
    shared = (G / "Miscellaneous/SharedDefines.h").read_text(encoding="utf-8")

    decls = [
        "// GENERATED by tools/tc_aura_duration_probe/extract.py from TrinityCore -- do not edit",
        define(spell_h, "SPELL_EMPOWER_HOLD_TIME_AT_MAX", "Spell.h"),
        enum_block(spell_defs, "enum class SpellModOp : uint8", "SpellDefines.h"),
        enum_block(aura_defs, "enum AuraRemoveMode", "SpellAuraDefines.h"),
        named_values(spell_defs, ("TRIGGERED_DONT_RESET_PERIODIC_TIMER",), "SpellDefines.h", "TriggerCastFlags"),
        *[named_values(shared, [a for a in ATTRS if a.startswith(f"SPELL_ATTR{w}_")], "SharedDefines.h", f"SpellAttr{w}")
          for w in sorted({int(re.match(r"SPELL_ATTR(\d+)_", a).group(1)) for a in ATTRS})],
        named_values(aura_defs, AURAS, "SpellAuraDefines.h", "AuraType"),
    ]
    bodies = ["// GENERATED by tools/tc_aura_duration_probe/extract.py from TrinityCore -- do not edit"]
    bodies.append(template(util_h, "CalculatePct", "Util.h"))
    bodies.append(template(util_h, "AddPct", "Util.h"))
    for sig in ("bool SpellInfo::IsPassive() const", "bool SpellInfo::IsChanneled() const",
                "int32 SpellInfo::GetDuration() const", "int32 SpellInfo::GetMaxDuration() const"):
        bodies.append(function(info_cpp, sig, "SpellInfo.cpp"))
    for sig in ("int32 WorldObject::CalcSpellDuration(SpellInfo const* spellInfo, std::vector<SpellPowerCost> const* powerCosts) const",
                "void WorldObject::ModSpellDurationTime(SpellInfo const* spellInfo, int32& duration, Spell* spell /*= nullptr*/) const"):
        bodies.append(function(obj_cpp, sig, "Object.cpp"))
    for sig in ("void Aura::Update(uint32 diff, Unit* caster)",
                "int32 Aura::CalcMaxDuration(Unit* caster) const",
                "/*static*/ int32 Aura::CalcMaxDuration(SpellInfo const* spellInfo, WorldObject const* caster, std::vector<SpellPowerCost> const* powerCosts)",
                "void Aura::SetDuration(int32 duration, bool withMods)",
                "void Aura::RefreshDuration(bool withMods)",
                "void Aura::RefreshTimers(bool resetPeriodicTimer)",
                "uint32 Aura::CalcMaxStackAmount() const",
                "bool Aura::ModStackAmount(int32 num, AuraRemoveMode removeMode /*= AURA_REMOVE_BY_DEFAULT*/, bool resetPeriodicTimer /*= true*/)"):
        bodies.append(function(auras_cpp, sig, "SpellAuras.cpp").replace("/*static*/ int32 Aura::", "int32 Aura::"))
    for sig in ("uint32 AuraEffect::GetTotalTicks() const",
                "void AuraEffect::ResetPeriodic(bool resetPeriodicTimer /*= false*/)",
                "void AuraEffect::CalculatePeriodic(Unit* caster, bool resetPeriodicTimer /*= true*/, bool load /*= false*/)",
                "void AuraEffect::Update(uint32 diff, Unit* caster)"):
        bodies.append(function(effects_cpp, sig, "SpellAuraEffects.cpp"))
    reset, rl = statement(spell_cpp, "bool const resetPeriodicTimer = (m_spellInfo->StackAmount < 2)",
                          "\n", "Spell.cpp")
    bodies.append(f"// Spell.cpp:{rl}\nbool Spell::ResetPeriodicTimerForHit() const\n{{\n    {reset}\n    return resetPeriodicTimer;\n}}\n")
    commit, cl = statement(spell_cpp, "                    if (!m_spellValue->Duration)\n                    {\n                        hitInfo.AuraDuration = caster->ModSpellDuration(",
                           "                    if (refresh)\n                        hitInfo.HitAura->AddStaticApplication", "Spell.cpp")
    bodies.append(f"// Spell.cpp:{cl}\nvoid Spell::CommitAuraDuration(Unit* unit, TargetInfo& hitInfo, WorldObject* caster, bool refresh)\n{{\n{commit}}}\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DECLS.write_text("\n\n".join(decls) + "\n", encoding="utf-8")
    OUT_BODIES.write_text("\n\n".join(bodies) + "\n", encoding="utf-8")
    print(f"wrote {OUT_DECLS.name} ({len(decls)} blocks), {OUT_BODIES.name} ({len(bodies)} blocks) from {TC_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
