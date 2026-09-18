#!/usr/bin/env python3
"""Extract TrinityCore aura scheduling code verbatim (aura-lifecycle Track I).

The C++ side of the ordering / generation differential (``aura_lifecycle/ordering.py``) is
Trinity's own text compiled against minimal stubs (see ``probe.cpp``).  ``EventProcessor.cpp`` is
included whole by ``probe.cpp`` (not extracted).

Extracted verbatim (TrinityCore @ 7f3d43b):
  Unit.cpp          Unit::_UpdateSpells, Unit::_DeleteRemovedAuras, Unit::RemoveOwnedAura (iterator, Aura*),
                    Unit::RemoveAllAurasOnDeath, Unit::_UnapplyAura(AuraApplication*, AuraRemoveMode)
  SpellAuras.cpp    Aura::UpdateOwner, Aura::Update, Aura::_Remove, Aura::SetDuration, Aura::RefreshDuration,
                    Aura::RefreshTimers, Aura::SetCharges, Aura::CalcMaxCharges, Aura::SetStackAmount,
                    Aura::CalcMaxStackAmount, Aura::ModStackAmount, Aura::_DeleteRemovedApplications,
                    UnitAura::Remove
  SpellAuras.h      Aura::IsExpired, Aura::IsPermanent (inline bodies)
  SpellAuraEffects  AuraEffect::Update, GetTotalTicks, ResetPeriodic, CalculatePeriodic, GetApplicationList
  Spell.cpp         SpellEvent::Execute; the SPELL_STATE_PREPARING case of Spell::update; the post-
                    TryRefreshStackOrCreate duration block of Spell::DoSpellEffectHit
  Util.h            CalculatePct
  enums             AuraRemoveMode, the AuraType values used (SpellAuraDefines.h), SPELL_ATTR* bits used (SharedDefines.h)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

WORKSPACE_PARENT = Path(__file__).resolve().parents[5]
TC_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else WORKSPACE_PARENT / "TrinityCore")
OUT_DIR = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent)
OUT_DECLS = OUT_DIR / "tc_order_decls.inc"
OUT_BODIES = OUT_DIR / "tc_order_bodies.inc"
G = TC_ROOT / "src/server/game"

ATTRS = ("SPELL_ATTR1_AURA_UNIQUE", "SPELL_ATTR2_NO_TARGET_PER_SECOND_COSTS", "SPELL_ATTR5_EXTRA_INITIAL_PERIOD",
         "SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC", "SPELL_ATTR5_AURA_UNIQUE_PER_CASTER",
         "SPELL_ATTR8_HASTE_AFFECTS_DURATION", "SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC",
         "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION")
PERIODIC_AURAS = ("SPELL_AURA_OBS_MOD_POWER", "SPELL_AURA_PERIODIC_DAMAGE", "SPELL_AURA_PERIODIC_HEAL",
                  "SPELL_AURA_OBS_MOD_HEALTH", "SPELL_AURA_PERIODIC_TRIGGER_SPELL",
                  "SPELL_AURA_PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "SPELL_AURA_PERIODIC_ENERGIZE",
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


def function(text: str, signature: str, source: str, prefix: str = "") -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"signature not found in {source}: {signature}")
    if text.find(signature, start + 1) >= 0:
        raise SystemExit(f"signature not unique in {source}: {signature}")
    line = text.count("\n", 0, start) + 1
    _, b = braced(text, start)
    return f"// {source}:{line}\n{prefix}{text[start:b]}\n"


def between(text: str, anchor: str, first: str, last: str, source: str) -> tuple[str, int]:
    """Verbatim text from ``first`` (inclusive) to ``last`` (exclusive), both searched after ``anchor``."""
    base = text.index(anchor)
    a = text.index(first, base)
    b = text.index(last, a)
    return text[a:b], text.count("\n", 0, a) + 1


def inline_member(text: str, decl: str, source: str) -> str:
    for n, line in enumerate(text.splitlines(), 1):
        if decl in line:
            body = line.strip()
            name = re.search(r"(\w+)\(\) const", body).group(1)
            return f"// {source}:{n}\n" + body.replace(f"bool {name}() const", f"bool Aura::{name}() const", 1)
    raise SystemExit(f"{decl} not found in {source}")


def enum_block(text: str, head: str, source: str) -> str:
    start = text.index(head)
    end = text.index("};", start) + 2
    line = text.count("\n", 0, start) + 1
    return f"// {source}:{line}\n{text[start:end]}"


def named_values(text: str, names, source: str, enum: str) -> str:
    rows = []
    for name in names:
        m = re.search(rf"^\s*({re.escape(name)})\s*=\s*([0-9A-Fa-fx]+)", text, re.MULTILINE)
        if not m:
            raise SystemExit(f"{name} not found in {source}")
        line = text.count("\n", 0, m.start()) + 1
        rows.append(f"    {name} = {m.group(2)}, // {source}:{line}")
    return f"enum {enum} : uint32\n{{\n" + "\n".join(rows) + "\n};"


def main() -> int:
    unit_cpp = (G / "Entities/Unit/Unit.cpp").read_text(encoding="utf-8")
    auras_cpp = (G / "Spells/Auras/SpellAuras.cpp").read_text(encoding="utf-8")
    auras_h = (G / "Spells/Auras/SpellAuras.h").read_text(encoding="utf-8")
    effects_cpp = (G / "Spells/Auras/SpellAuraEffects.cpp").read_text(encoding="utf-8")
    spell_cpp = (G / "Spells/Spell.cpp").read_text(encoding="utf-8")
    shared = (G / "Miscellaneous/SharedDefines.h").read_text(encoding="utf-8")
    aura_defs = (G / "Spells/Auras/SpellAuraDefines.h").read_text(encoding="utf-8")
    util_h = (TC_ROOT / "src/common/Utilities/Util.h").read_text(encoding="utf-8")

    decls = [
        "// GENERATED by tools/tc_aura_order_probe/extract.py from TrinityCore -- do not edit",
        enum_block(aura_defs, "enum AuraRemoveMode", "SpellAuraDefines.h"),
        named_values(aura_defs, PERIODIC_AURAS, "SpellAuraDefines.h", "AuraType"),
        *[named_values(shared, [a for a in ATTRS if a.startswith(f"SPELL_ATTR{w}_")], "SharedDefines.h", f"SpellAttr{w}")
          for w in sorted({a[10:a.index("_", 10)] for a in ATTRS}, key=int)],
        function(util_h, "inline T CalculatePct(T base, U pct)", "Util.h", prefix="template <class T, class U>\n"),
    ]

    bodies = ["// GENERATED by tools/tc_aura_order_probe/extract.py from TrinityCore -- do not edit"]
    bodies.append(inline_member(auras_h, "bool IsExpired() const", "SpellAuras.h"))
    bodies.append(inline_member(auras_h, "bool IsPermanent() const", "SpellAuras.h"))
    for sig in ("void Unit::_DeleteRemovedAuras()",
                "void Unit::_UpdateSpells(uint32 time)",
                "void Unit::RemoveOwnedAura(AuraMap::iterator& i, AuraRemoveMode removeMode)",
                "void Unit::RemoveOwnedAura(Aura* aura, AuraRemoveMode removeMode)",
                "void Unit::RemoveAllAurasOnDeath()",
                "void Unit::_UnapplyAura(AuraApplication* aurApp, AuraRemoveMode removeMode)"):
        bodies.append(function(unit_cpp, sig, "Unit.cpp"))
    for sig in ("void Aura::UpdateOwner(uint32 diff, WorldObject* owner)",
                "void Aura::Update(uint32 diff, Unit* caster)",
                "void Aura::_Remove(AuraRemoveMode removeMode)",
                "void Aura::SetDuration(int32 duration, bool withMods)",
                "void Aura::RefreshDuration(bool withMods)",
                "void Aura::RefreshTimers(bool resetPeriodicTimer)",
                "void Aura::SetCharges(uint8 charges)",
                "uint8 Aura::CalcMaxCharges(Unit* caster) const",
                "void Aura::SetStackAmount(uint8 stackAmount)",
                "uint32 Aura::CalcMaxStackAmount() const",
                "bool Aura::ModStackAmount(int32 num, AuraRemoveMode removeMode /*= AURA_REMOVE_BY_DEFAULT*/, bool resetPeriodicTimer /*= true*/)",
                "void Aura::_DeleteRemovedApplications()",
                "void UnitAura::Remove(AuraRemoveMode removeMode)"):
        bodies.append(function(auras_cpp, sig, "SpellAuras.cpp"))
    bodies.append(function(effects_cpp, "void AuraEffect::GetApplicationList(Container& applicationContainer) const",
                           "SpellAuraEffects.cpp", prefix="template <typename Container>\n"))
    for sig in ("void AuraEffect::Update(uint32 diff, Unit* caster)",
                "uint32 AuraEffect::GetTotalTicks() const",
                "void AuraEffect::ResetPeriodic(bool resetPeriodicTimer /*= false*/)",
                "void AuraEffect::CalculatePeriodic(Unit* caster, bool resetPeriodicTimer /*= true*/, bool load /*= false*/)"):
        bodies.append(function(effects_cpp, sig, "SpellAuraEffects.cpp"))

    bodies.append(function(spell_cpp, "bool SpellEvent::Execute(uint64 e_time, uint32 p_time)", "Spell.cpp"))
    case, line = between(spell_cpp, "void Spell::update(uint32 difftime)", "        case SPELL_STATE_PREPARING:",
                         "        case SPELL_STATE_CHANNELING:", "Spell.cpp")
    bodies.append(f"// Spell.cpp:{line} (SPELL_STATE_PREPARING case of Spell::update)\n"
                  "void Spell::UpdatePreparing(uint32 difftime)\n{\n    switch (m_spellState)\n    {\n"
                  f"{case}        default:\n            break;\n    }}\n}}\n")
    block, line = between(spell_cpp, "void Spell::DoSpellEffectHit(Unit* unit, SpellEffectInfo const& spellEffectInfo, TargetInfo& hitInfo)",
                          "                    if (!m_spellValue->Duration)",
                          "                    if (refresh)\n                        hitInfo.HitAura->AddStaticApplication", "Spell.cpp")
    bodies.append(f"// Spell.cpp:{line} (DoSpellEffectHit, after TryRefreshStackOrCreate)\n"
                  "void Spell::CommitHitDuration(Unit* caster, Unit* unit, TargetInfo& hitInfo, bool refresh)\n{\n"
                  f"{block}}}\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DECLS.write_text("\n\n".join(decls) + "\n", encoding="utf-8")
    OUT_BODIES.write_text("\n\n".join(bodies) + "\n", encoding="utf-8")
    print(f"wrote {OUT_DECLS.name} ({len(decls)} blocks), {OUT_BODIES.name} ({len(bodies)} blocks) from {TC_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
