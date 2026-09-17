#!/usr/bin/env python3
"""Extract the character-preparation arithmetic verbatim from TrinityCore.

Same contract as ``tools/tc_probe/extract.py``: the bodies below are pulled
out of the sibling checkout at build time, so the probe runs the real C++
``float`` arithmetic rather than a hand-typed copy, and a change in
TrinityCore breaks the build instead of silently diverging.

Extracted verbatim (body text unchanged; the probe supplies the members and
globals the bodies reference):

  Unit::GetTotalStatValue                     Entities/Unit/Unit.cpp
  Player::GetHealthBonusFromStamina           Entities/Unit/StatSystem.cpp
  Player::UpdateMaxHealth                     Entities/Unit/StatSystem.cpp
  Player::UpdateMaxPower                      Entities/Unit/StatSystem.cpp
  Player::UpdateArmor                         Entities/Unit/StatSystem.cpp
  Player::GetRatingMultiplier                 Entities/Player/Player.cpp
  Player::GetRatingBonusValue                 Entities/Player/Player.cpp
  Player::ApplyRatingDiminishing              Entities/Player/Player.cpp
  Player::ApplyRatingMod                      Entities/Player/Player.cpp
  GetGameTableColumnForCombatRating           Entities/Player/Player.cpp (inline)
  GetGameTableColumnForClass                  DataStores/GameTables.h (template)
  CalculatePct / AddPct                       common/Utilities/Util.h
  DB2Manager::GetCurveValueAt / DetermineCurveType   DataStores/DB2Stores.cpp
  ObjectMgr::BuildPlayerLevelInfo             Globals/ObjectMgr.cpp
  the "fill level gaps" loop of ObjectMgr::LoadPlayerInfo   Globals/ObjectMgr.cpp

Pinned but re-typed in probe.cpp (they need Player/update-field state that
cannot be stubbed honestly): the attack-power, crit, spell-crit, mastery and
mastery-amount lines.  Each re-typed line is listed in ``RETYPED`` below and
asserted to exist verbatim in the checkout, so the build fails when the
source moves.
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE_PARENT = Path(__file__).resolve().parents[4].parent
TC_ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1]
               else WORKSPACE_PARENT / "TrinityCore")
OUT = Path(sys.argv[2] if len(sys.argv) > 2
           else Path(__file__).with_name("tc_extracted.inc"))

GAME = TC_ROOT / "src/server/game"
UNIT = GAME / "Entities/Unit/Unit.cpp"
STAT = GAME / "Entities/Unit/StatSystem.cpp"
PLAYER = GAME / "Entities/Player/Player.cpp"
OBJMGR = GAME / "Globals/ObjectMgr.cpp"
DB2 = GAME / "DataStores/DB2Stores.cpp"
GAMETABLES = GAME / "DataStores/GameTables.h"
UTIL = TC_ROOT / "src/common/Utilities/Util.h"
SPELLINFO = GAME / "Spells/SpellInfo.cpp"

#: (file, exact text) -- lines the probe re-types; must exist verbatim.
RETYPED = [
    (STAT, "float strengthValue = std::max(GetStat(STAT_STRENGTH) * entry->AttackPowerPerStrength, 0.0f);"),
    (STAT, "float agilityValue = std::max(GetStat(STAT_AGILITY) * entry->AttackPowerPerAgility, 0.0f);"),
    (STAT, "val2 = strengthValue + agilityValue;"),
    (STAT, "val2 = (level + std::max(GetStat(STAT_AGILITY), 0.0f)) * entry->RangedAttackPowerPerAgility;"),
    (STAT, "float base_attPower = GetFlatModifierValue(unitMod, BASE_VALUE) * GetPctModifierValue(unitMod, BASE_PCT);"),
    (STAT, "SetAttackPower(int32(base_attPower));"),
    (STAT, "float value = 5.0f;"),
    (STAT, "applyCritLimit(GetBaseModValue(CRIT_PERCENTAGE, FLAT_MOD) + GetBaseModValue(CRIT_PERCENTAGE, PCT_MOD) + GetRatingBonusValue(CR_CRIT_MELEE))"),
    (STAT, "float crit = 5.0f;"),
    (STAT, "crit += GetTotalAuraModifier(SPELL_AURA_MOD_SPELL_CRIT_CHANCE);"),
    (STAT, "crit += GetTotalAuraModifier(SPELL_AURA_MOD_CRIT_PCT);"),
    (STAT, "crit += GetRatingBonusValue(CR_CRIT_SPELL);"),
    (STAT, "float value = GetTotalAuraModifier(SPELL_AURA_MASTERY);"),
    (STAT, "value += GetRatingBonusValue(CR_MASTERY);"),
    (STAT, "float value  = GetTotalStatValue(stat);"),
    (STAT, "SetStat(stat, int32(value));"),
    (SPELLINFO, "value += *playerCaster->m_activePlayerData->Mastery * BonusCoefficient;"),
    (PLAYER, "SetArmor(int32(m_createStats[STAT_AGILITY]*2), 0);"),
    (UNIT, "m_auraFlatModifiersGroup[i][BASE_PCT_EXCLUDE_CREATE] = 100.0f;"),
]


def extract_body(text: str, signature: str) -> str:
    index = text.find(signature)
    if index < 0:
        raise SystemExit(f"signature not found: {signature}")
    open_brace = text.index("{", index)
    depth = 0
    for position in range(open_brace, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace:position + 1]
    raise SystemExit(f"unbalanced braces after {signature}")


def extract_between(text: str, start_anchor: str, end_anchor: str) -> str:
    start = text.find(start_anchor)
    if start < 0:
        raise SystemExit(f"anchor not found: {start_anchor}")
    end = text.find(end_anchor, start)
    if end < 0:
        raise SystemExit(f"end anchor not found after {start_anchor}: {end_anchor}")
    return text[start:end]


def line_of(text: str, needle: str) -> int:
    return text[:text.index(needle)].count("\n") + 1


def main() -> int:
    unit = UNIT.read_text(encoding="utf-8")
    stat = STAT.read_text(encoding="utf-8")
    player = PLAYER.read_text(encoding="utf-8")
    objmgr = OBJMGR.read_text(encoding="utf-8")
    db2 = DB2.read_text(encoding="utf-8")
    gametables = GAMETABLES.read_text(encoding="utf-8")
    util = UTIL.read_text(encoding="utf-8")
    spellinfo = SPELLINFO.read_text(encoding="utf-8")
    texts = {UNIT: unit, STAT: stat, PLAYER: player, SPELLINFO: spellinfo}

    for path, needle in RETYPED:
        if needle not in texts[path]:
            raise SystemExit(f"re-typed line no longer present in {path}: {needle}")

    # Order matters for compilation: helpers (Util.h templates, inline
    # GameTable accessors, curve code) before the bodies that call them.
    parts = [
        ("template <class T, class U>\ninline T CalculatePct(T base, U pct)",
         extract_body(util, "inline T CalculatePct(T base, U pct)"), UTIL, util),
        ("template <class T, class U>\ninline T AddPct(T &base, U pct)",
         extract_body(util, "inline T AddPct(T &base, U pct)"), UTIL, util),
        ("template<class T>\ninline float GetGameTableColumnForClass(T const* row, int32 class_)",
         extract_body(gametables, "inline float GetGameTableColumnForClass(T const* row, int32 class_)"), GAMETABLES, gametables),
        ("inline float GetGameTableColumnForCombatRating(GtCombatRatingsEntry const* row, uint32 rating)",
         extract_body(player, "inline float GetGameTableColumnForCombatRating(GtCombatRatingsEntry const* row, uint32 rating)"), PLAYER, player),
        ("static CurveInterpolationMode DetermineCurveType(CurveEntry const* curve, std::vector<DBCPosition2D> const& points)",
         extract_body(db2, "static CurveInterpolationMode DetermineCurveType("), DB2, db2),
        ("float DB2Manager_GetCurveValueAt(CurveInterpolationMode mode, std::span<DBCPosition2D const> points, float x)",
         extract_body(db2, "float DB2Manager::GetCurveValueAt(CurveInterpolationMode mode,"), DB2, db2),
        ("float Unit::GetTotalStatValue(Stats stat) const",
         extract_body(unit, "float Unit::GetTotalStatValue(Stats stat) const"), UNIT, unit),
        ("float Player::GetHealthBonusFromStamina() const",
         extract_body(stat, "float Player::GetHealthBonusFromStamina() const"), STAT, stat),
        ("void Player::UpdateMaxHealth()",
         extract_body(stat, "void Player::UpdateMaxHealth()"), STAT, stat),
        ("void Player::UpdateMaxPower(Powers power)",
         extract_body(stat, "void Player::UpdateMaxPower(Powers power)"), STAT, stat),
        ("void Player::UpdateArmor()",
         extract_body(stat, "void Player::UpdateArmor()"), STAT, stat),
        ("float Player::GetRatingMultiplier(CombatRating cr) const",
         extract_body(player, "float Player::GetRatingMultiplier(CombatRating cr) const"), PLAYER, player),
        ("float Player::GetRatingBonusValue(CombatRating cr) const",
         extract_body(player, "float Player::GetRatingBonusValue(CombatRating cr) const"), PLAYER, player),
        ("float Player::ApplyRatingDiminishing(CombatRating cr, float bonusValue) const",
         extract_body(player, "float Player::ApplyRatingDiminishing(CombatRating cr, float bonusValue) const"), PLAYER, player),
        ("void Player::ApplyRatingMod(CombatRating combatRating, int32 value, bool apply)",
         extract_body(player, "void Player::ApplyRatingMod(CombatRating combatRating, int32 value, bool apply)"), PLAYER, player),
        ("void ObjectMgr::BuildPlayerLevelInfo(uint8 race, uint8 _class, uint8 level, PlayerLevelInfo* info) const",
         extract_body(objmgr, "void ObjectMgr::BuildPlayerLevelInfo(uint8 race, uint8 _class, uint8 level, PlayerLevelInfo* info) const"), OBJMGR, objmgr),
    ]

    # The gap fill is a loop inside LoadPlayerInfo, not a function: cut it out
    # by its anchors and wrap it so the probe can call it per (race, class).
    fill_start = "        // Fill gaps and check integrity\n"
    fill_end = "        TC_LOG_INFO(\"server.loading\", \">> Loaded {} level stats definitions"
    # There are two "Fill gaps" blocks in ObjectMgr.cpp (pet stats first); take the player one.
    player_block_start = objmgr.index("FROM player_classlevelstats")
    fill = extract_between(objmgr[player_block_start:], fill_start, fill_end)
    fill_line = objmgr[:player_block_start + objmgr[player_block_start:].index(fill_start)].count("\n") + 1

    lines = [
        "// GENERATED by extract.py -- do not edit.",
        f"// TrinityCore: {TC_ROOT}",
        "",
    ]
    for signature, body, path, text in parts:
        rel = path.relative_to(TC_ROOT)
        # Function signatures with a Class:: are emitted as-is; the probe
        # declares matching structs so the bodies compile unchanged.
        key = signature.split("\n")[-1]
        needle = key.replace("DB2Manager_GetCurveValueAt", "DB2Manager::GetCurveValueAt")
        needle = needle.replace("static CurveInterpolationMode DetermineCurveType(CurveEntry const* curve, std::vector<DBCPosition2D> const& points)",
                                "static CurveInterpolationMode DetermineCurveType(")
        needle = needle.replace("float DB2Manager::GetCurveValueAt(CurveInterpolationMode mode, std::span<DBCPosition2D const> points, float x)",
                                "float DB2Manager::GetCurveValueAt(CurveInterpolationMode mode,")
        lines.append(f"// {rel}:{line_of(text, needle)}")
        lines.append(signature)
        lines.append(body)
        lines.append("")
    lines.append(f"// {OBJMGR.relative_to(TC_ROOT)}:{fill_line} -- the player 'Fill gaps and check integrity' block of ObjectMgr::LoadPlayerInfo, wrapped")
    lines.append("void ObjectMgr_FillPlayerLevelGaps(std::map<std::pair<Races, Classes>, std::unique_ptr<PlayerInfo>> const& _playerInfo)")
    lines.append("{")
    lines.append(fill.rstrip())
    lines.append("}")
    lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({sum(len(x.splitlines()) for x in lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
