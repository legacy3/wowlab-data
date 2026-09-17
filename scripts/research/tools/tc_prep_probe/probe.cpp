// Differential probe for character preparation (Track C, agent F).
//
// tc_extracted.inc holds bodies pulled verbatim out of the sibling
// TrinityCore checkout by extract.py.  This file supplies only the members,
// enums and stubs those bodies reference, plus a line protocol on stdin.
// Everything marked RE-TYPED is copied from a TrinityCore line whose text
// extract.py asserts still exists (see RETYPED there); the reason it is
// re-typed rather than extracted is given inline.
//
// Protocol (one request per line, one reply per line, floats printed %.17g):
//   totalstat  <create> <base_value> <base_pct_exclude_create> <base_pct> <total_value> <total_pct>
//       -> "<float raw> <int32>"                           Unit::GetTotalStatValue + SetStat(int32())
//   maxhealth  <create_health> <base_value> <base_pct> <total_value> <total_pct> <stamina_int32> <level> <hp_per_sta|-1>
//       -> "<uint32> <float raw>"                          Player::UpdateMaxHealth (+GetHealthBonusFromStamina)
//   maxpower   <create> <base_value> <base_pct> <total_value> <total_pct>
//       -> "<int32> <float raw>"                           Player::UpdateMaxPower (lroundf)
//   armor      <base_value> <base_pct> <total_value> <total_pct> <bonus_armor_pct_multiplier>
//       -> "<int32 armor> <int32 bonus> <float raw>"       Player::UpdateArmor
//   ap         <str_int32> <agi_int32> <ap_per_str> <ap_per_agi> <base_pct>
//       -> "<int32> <float raw>"                           RE-TYPED Player::UpdateAttackPowerAndDamage(false)
//   rap        <level> <agi_int32> <rap_per_agi>
//       -> "<int32> <float raw>"                           RE-TYPED Player::UpdateAttackPowerAndDamage(true)
//   ratingmult <column>
//       -> "<float>"                                       Player::GetRatingMultiplier
//   ratingbonus <cr> <rating_int32> <column> <mode> <n> <x0> <y0> ...
//       -> "<float linear> <float final>"                  Player::GetRatingBonusValue (+ApplyRatingDiminishing)
//   applyrating <int16 current> <int32 value>
//       -> "<int16>"                                       Player::ApplyRatingMod (int16 accumulation)
//   mastery    <aura_total> <rating_int32> <column> <mode> <n> <pts...>
//       -> "<float>"                                       RE-TYPED Player::UpdateMastery
//   masteryamount <base_points> <mastery_float> <coef_float>
//       -> "<double raw> <int32>"                          RE-TYPED SpellEffectInfo::CalcValue
//   crit       <flat> <pct> <rating_bonus>       -> "<float>"   RE-TYPED Player::UpdateCritPercentage
//   spellcrit  <aura_a> <aura_b> <rating_bonus>  -> "<float>"   RE-TYPED Player::UpdateSpellCritChance
//   calcpct    <base> <pct>                      -> "<float>"   CalculatePct<float,float>
//   basemp     <class> <15 columns>              -> "<uint32>"  GetGameTableColumnForClass + uint32()
//   fillgaps   <max_level> <query_level> <n> (<level> <s0> <s1> <s2> <s3> <s4>)*
//       -> "<filled_count> <s0> <s1> <s2> <s3> <s4>" or "ABORT"  LoadPlayerInfo gap fill
//   buildlevelinfo <class> <max_level> <level> <s0> <s1> <s2> <s3> <s4>
//       -> "<s0> <s1> <s2> <s3> <s4>"                      ObjectMgr::BuildPlayerLevelInfo

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <span>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

using uint8 = std::uint8_t;
using int16 = std::int16_t;
using int32 = std::int32_t;
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using SpellEffectValue = double;   // src/server/game/Spells/SpellDefines.h:490

template <typename E>
constexpr auto AsUnderlyingType(E e) { return static_cast<std::underlying_type_t<E>>(e); }

// -- enums (src/server/game/Miscellaneous/SharedDefines.h, Entities/Unit/Unit.h) --
enum Stats : uint8 { STAT_STRENGTH = 0, STAT_AGILITY = 1, STAT_STAMINA = 2, STAT_INTELLECT = 3, STAT_SPIRIT = 4 };
#define MAX_STATS 5
enum Powers : int8_t { POWER_MANA = 0, MAX_POWERS = 35 };
#define MAX_POWERS_PER_CLASS 10
enum Classes : uint8 {
    CLASS_NONE = 0, CLASS_WARRIOR = 1, CLASS_PALADIN = 2, CLASS_HUNTER = 3, CLASS_ROGUE = 4, CLASS_PRIEST = 5,
    CLASS_DEATH_KNIGHT = 6, CLASS_SHAMAN = 7, CLASS_MAGE = 8, CLASS_WARLOCK = 9, CLASS_MONK = 10, CLASS_DRUID = 11,
    CLASS_DEMON_HUNTER = 12, CLASS_EVOKER = 13, CLASS_ADVENTURER = 14, CLASS_TRAVELER = 15 };
enum Races : uint8 { RACE_NONE = 0 };
enum UnitModifierFlatType { BASE_VALUE = 0, BASE_PCT_EXCLUDE_CREATE = 1, TOTAL_VALUE = 2, MODIFIER_TYPE_FLAT_END = 3 };
enum UnitModifierPctType { BASE_PCT = 0, TOTAL_PCT = 1, MODIFIER_TYPE_PCT_END = 2 };
enum UnitMods { UNIT_MOD_STAT_STRENGTH, UNIT_MOD_STAT_AGILITY, UNIT_MOD_STAT_STAMINA, UNIT_MOD_STAT_INTELLECT,
    UNIT_MOD_STAT_SPIRIT, UNIT_MOD_HEALTH, UNIT_MOD_MANA, UNIT_MOD_POWER_LAST = UNIT_MOD_MANA + MAX_POWERS - 1,
    UNIT_MOD_ARMOR, UNIT_MOD_ATTACK_POWER, UNIT_MOD_ATTACK_POWER_RANGED, UNIT_MOD_END,
    UNIT_MOD_STAT_START = UNIT_MOD_STAT_STRENGTH, UNIT_MOD_POWER_START = UNIT_MOD_MANA };
enum CombatRating {
    CR_AMPLIFY = 0, CR_DEFENSE_SKILL = 1, CR_DODGE = 2, CR_PARRY = 3, CR_BLOCK = 4, CR_HIT_MELEE = 5, CR_HIT_RANGED = 6,
    CR_HIT_SPELL = 7, CR_CRIT_MELEE = 8, CR_CRIT_RANGED = 9, CR_CRIT_SPELL = 10, CR_CORRUPTION = 11,
    CR_CORRUPTION_RESISTANCE = 12, CR_SPEED = 13, CR_RESILIENCE_CRIT_TAKEN = 14, CR_RESILIENCE_PLAYER_DAMAGE = 15,
    CR_LIFESTEAL = 16, CR_HASTE_MELEE = 17, CR_HASTE_RANGED = 18, CR_HASTE_SPELL = 19, CR_AVOIDANCE = 20,
    CR_STURDINESS = 21, CR_UNUSED_7 = 22, CR_EXPERTISE = 23, CR_ARMOR_PENETRATION = 24, CR_MASTERY = 25,
    CR_PVP_POWER = 26, CR_CLEAVE = 27, CR_VERSATILITY_DAMAGE_DONE = 28, CR_VERSATILITY_HEALING_DONE = 29,
    CR_VERSATILITY_DAMAGE_TAKEN = 30, CR_UNUSED_12 = 31 };
#define MAX_COMBAT_RATING 32
enum AuraType { SPELL_AURA_MOD_ARMOR_PCT_FROM_STAT = 1, SPELL_AURA_MOD_BONUS_ARMOR_PCT = 2 };
enum class GlobalCurve : int32 { CritDiminishing = 0, MasteryDiminishing = 1, HasteDiminishing = 2, SpeedDiminishing = 3,
    AvoidanceDiminishing = 4, VersatilityDoneDiminishing = 5, LifestealDiminishing = 6, DodgeDiminishing = 7,
    BlockDiminishing = 8, ParryDiminishing = 9, VersatilityTakenDiminishing = 11 };
enum WorldIntConfigs { CONFIG_MAX_PLAYER_LEVEL };
enum class CurveInterpolationMode : uint8 { Linear = 0, Cosine = 1, CatmullRom = 2, Bezier3 = 3, Bezier4 = 4, Bezier = 5, Constant = 6 };

struct DBCPosition2D { float X; float Y; };
struct CurveEntry { uint32 ID; uint8 Type; int32 Flags; };

// -- game tables (src/server/game/DataStores/GameTables.h) --
struct GtCombatRatingsEntry {
    float Amplify = 0, DefenseSkill = 0, Dodge = 0, Parry = 0, Block = 0, HitMelee = 0, HitRanged = 0, HitSpell = 0,
          CritMelee = 0, CritRanged = 0, CritSpell = 0, Corruption = 0, CorruptionResistance = 0, Speed = 0,
          ResilienceCritTaken = 0, ResiliencePlayerDamage = 0, Lifesteal = 0, HasteMelee = 0, HasteRanged = 0,
          HasteSpell = 0, Avoidance = 0, Sturdiness = 0, Unused7 = 0, Expertise = 0, ArmorPenetration = 0,
          Mastery = 0, PvPPower = 0, Cleave = 0, VersatilityDamageDone = 0, VersatilityHealingDone = 0,
          VersatilityDamageTaken = 0, Unused12 = 0;
};
struct GtHpPerStaEntry { float Health = 0.0f; };
struct GtBaseMPEntry { float Rogue = 0, Druid = 0, Hunter = 0, Mage = 0, Paladin = 0, Priest = 0, Shaman = 0,
    Warlock = 0, Warrior = 0, DeathKnight = 0, Monk = 0, DemonHunter = 0, Evoker = 0, Adventurer = 0, Traveler = 0; };

template <class T>
class GameTable {                      // GameTables.h:178-195
public:
    T const* GetRow(uint32 row) const { if (row >= _data.size()) return nullptr; return &_data[row]; }
    void SetData(std::vector<T> data) { _data = std::move(data); }
private:
    std::vector<T> _data;
};
static GameTable<GtCombatRatingsEntry> sCombatRatingsGameTable;
static GameTable<GtHpPerStaEntry> sHpPerStaGameTable;

// -- world / DB2 / log stubs --
struct WorldStub { uint32 maxLevel = 90; uint32 getIntConfig(WorldIntConfigs) const { return maxLevel; } };
static WorldStub g_world;
static WorldStub* sWorld = &g_world;

static std::vector<DBCPosition2D> g_curve_points;
static CurveInterpolationMode g_curve_mode = CurveInterpolationMode::Constant;
static uint32 g_curve_id = 0;     // 0 => "no global curve" (ApplyRatingDiminishing returns bonusValue)

float DB2Manager_GetCurveValueAt(CurveInterpolationMode mode, std::span<DBCPosition2D const> points, float x);
struct DB2ManagerStub {
    uint32 GetGlobalCurveId(GlobalCurve) const { return g_curve_id; }
    float GetCurveValueAt(uint32, float x) const
    {
        // DB2Manager::GetCurveValueAt(uint32, float): unknown curve -> 0.0f
        if (g_curve_points.empty()) return 0.0f;
        return DB2Manager_GetCurveValueAt(g_curve_mode, std::span<DBCPosition2D const>(g_curve_points), x);
    }
};
static DB2ManagerStub sDB2Manager;

static int g_log_errors = 0;
static std::string g_last_log;
#define TC_LOG_ERROR(filter, ...) do { ++g_log_errors; g_last_log = filter; } while (0)
struct AbortSignal {};
#define ABORT() throw AbortSignal{}
template <class T> T* ASSERT_NOTNULL(T* p) { if (!p) throw std::runtime_error("ASSERT_NOTNULL"); return p; }

namespace Trinity::Containers {
    template <class M>
    auto MapGetValuePtr(M& map, typename M::key_type const& key) -> typename M::mapped_type::element_type*
    {
        auto it = map.find(key);
        return it != map.end() ? it->second.get() : nullptr;
    }
}

// -- ObjectMgr (src/server/game/Globals/ObjectMgr.h:628-665) --
struct PlayerLevelInfo { int32 stats[MAX_STATS] = { }; };
struct PlayerInfo { std::unique_ptr<PlayerLevelInfo[]> levelInfo; };
struct ObjectMgr {
    std::map<std::pair<Races, Classes>, std::unique_ptr<PlayerInfo>> _playerInfo;
    void BuildPlayerLevelInfo(uint8 race, uint8 _class, uint8 level, PlayerLevelInfo* info) const;
};

// -- Unit / Player scaffolding (Unit.h:771-774, 1409-1420, 1899-1939; Player.h:3252-3254) --
struct AuraEffect { int32 GetMiscValue() const { return 0; } SpellEffectValue GetAmount() const { return 0; } };
struct Pet { void UpdateArmor() {} };
struct ActivePlayerDataStub { std::array<int32, MAX_COMBAT_RATING> CombatRatings{}; };

struct Unit {
    std::array<float, MAX_STATS> m_createStats{};
    float m_auraFlatModifiersGroup[UNIT_MOD_END][MODIFIER_TYPE_FLAT_END];
    float m_auraPctModifiersGroup[UNIT_MOD_END][MODIFIER_TYPE_PCT_END];
    std::array<int32, MAX_STATS> m_stats{};
    uint32 m_baseHealth = 0;
    uint32 m_baseMana = 0;
    uint8 m_level = 80;
    uint64 m_maxHealth = 0;
    int32 m_maxPower = 0;
    int32 m_armor = 0, m_bonusArmor = 0;
    float m_bonusArmorPctMultiplier = 1.0f;

    Unit()
    {
        // Unit::Unit, src/server/game/Entities/Unit/Unit.cpp:343-349 (RE-TYPED defaults)
        for (uint8 i = 0; i < UNIT_MOD_END; ++i)
        {
            m_auraFlatModifiersGroup[i][BASE_VALUE] = 0.0f;
            m_auraFlatModifiersGroup[i][BASE_PCT_EXCLUDE_CREATE] = 100.0f;
            m_auraFlatModifiersGroup[i][TOTAL_VALUE] = 0.0f;
            m_auraPctModifiersGroup[i][BASE_PCT] = 1.0f;
            m_auraPctModifiersGroup[i][TOTAL_PCT] = 1.0f;
        }
    }
    float GetStat(Stats stat) const { return float(m_stats[stat]); }               // Unit.h:771
    void SetStat(Stats stat, int32 val) { m_stats[stat] = val; }                    // Unit.h:772
    float GetCreateStat(Stats stat) const { return m_createStats[stat]; }           // Unit.h:1420
    uint32 GetCreateHealth() const { return m_baseHealth; }                         // Unit.h:1414
    uint32 GetCreateMana() const { return m_baseMana; }                             // Unit.h:1416
    uint8 GetLevel() const { return m_level; }
    int32 GetCreatePowerValue(Powers power) const { return power == POWER_MANA ? int32(GetCreateMana()) : 0; } // StatSystem.cpp:90-100 (mana branch)
    uint32 GetPowerIndex(Powers) const { return 0; }
    float GetFlatModifierValue(UnitMods unitMod, UnitModifierFlatType t) const { return m_auraFlatModifiersGroup[unitMod][t]; } // Unit.cpp:9660
    float GetPctModifierValue(UnitMods unitMod, UnitModifierPctType t) const { return m_auraPctModifiersGroup[unitMod][t]; }   // Unit.cpp:9671
    float GetTotalStatValue(Stats stat) const;                                      // extracted
    void SetMaxHealth(uint64 val) { m_maxHealth = val ? val : 1; }                  // Unit.cpp:10016-10019 (floor at 1)
    void SetMaxPower(Powers, int32 val) { m_maxPower = val; }
    void SetArmor(int32 val, int32 bonusVal) { m_armor = val; m_bonusArmor = bonusVal; }
    template <class F> float GetTotalAuraModifier(AuraType, F const&) const { return 0.0f; }   // no auras in the probe
    float GetTotalAuraMultiplier(AuraType) const { return m_bonusArmorPctMultiplier; }
    Stats GetPrimaryStat() const { return STAT_STRENGTH; }
    Pet* GetPet() const { return nullptr; }
};

struct Player : Unit {
    ActivePlayerDataStub m_activePlayerDataStorage;
    ActivePlayerDataStub const* m_activePlayerData = &m_activePlayerDataStorage;
    std::array<int16, MAX_COMBAT_RATING> m_baseRatingValue{};                       // Player.h:3254 (int16!)

    float GetHealthBonusFromStamina() const;        // extracted
    void UpdateMaxHealth();                          // extracted
    void UpdateMaxPower(Powers power);               // extracted
    void UpdateArmor();                              // extracted
    float GetRatingMultiplier(CombatRating cr) const;               // extracted
    float GetRatingBonusValue(CombatRating cr) const;               // extracted
    float ApplyRatingDiminishing(CombatRating cr, float bonusValue) const;  // extracted
    void ApplyRatingMod(CombatRating combatRating, int32 value, bool apply); // extracted
    void UpdateRating(CombatRating) {}
    void UpdateAttackPowerAndDamage(bool = false) {}
};

#include "tc_extracted.inc"

static CurveInterpolationMode ParseMode(std::string const& name)
{
    if (name == "Linear") return CurveInterpolationMode::Linear;
    if (name == "Cosine") return CurveInterpolationMode::Cosine;
    if (name == "CatmullRom") return CurveInterpolationMode::CatmullRom;
    if (name == "Bezier3") return CurveInterpolationMode::Bezier3;
    if (name == "Bezier4") return CurveInterpolationMode::Bezier4;
    if (name == "Bezier") return CurveInterpolationMode::Bezier;
    return CurveInterpolationMode::Constant;
}

static void ReadCurve(std::istringstream& in)
{
    std::string mode; std::size_t n = 0;
    in >> mode >> n;
    g_curve_points.assign(n, DBCPosition2D{});
    for (std::size_t i = 0; i < n; ++i)
        in >> g_curve_points[i].X >> g_curve_points[i].Y;
    g_curve_mode = ParseMode(mode);
    g_curve_id = n ? 1 : 0;
}

static void SetRatingColumn(CombatRating cr, float column, uint8 level)
{
    std::vector<GtCombatRatingsEntry> rows(level + 1);
    float* fields[] = { &rows[level].Amplify, &rows[level].DefenseSkill, &rows[level].Dodge, &rows[level].Parry,
        &rows[level].Block, &rows[level].HitMelee, &rows[level].HitRanged, &rows[level].HitSpell, &rows[level].CritMelee,
        &rows[level].CritRanged, &rows[level].CritSpell, &rows[level].Corruption, &rows[level].CorruptionResistance,
        &rows[level].Speed, &rows[level].ResilienceCritTaken, &rows[level].ResiliencePlayerDamage, &rows[level].Lifesteal,
        &rows[level].HasteMelee, &rows[level].HasteRanged, &rows[level].HasteSpell, &rows[level].Avoidance,
        &rows[level].Sturdiness, &rows[level].Unused7, &rows[level].Expertise, &rows[level].ArmorPenetration,
        &rows[level].Mastery, &rows[level].PvPPower, &rows[level].Cleave, &rows[level].VersatilityDamageDone,
        &rows[level].VersatilityHealingDone, &rows[level].VersatilityDamageTaken, &rows[level].Unused12 };
    *fields[cr] = column;
    sCombatRatingsGameTable.SetData(std::move(rows));
}

static void PrintF(float v) { std::printf("%.17g", static_cast<double>(v)); }

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string command;
        if (!(in >> command))
            continue;
        try
        {
        if (command == "totalstat")
        {
            Player p; float create, bv, bpec, bp, tv, tp;
            in >> create >> bv >> bpec >> bp >> tv >> tp;
            p.m_createStats[STAT_STRENGTH] = create;
            p.m_auraFlatModifiersGroup[UNIT_MOD_STAT_STRENGTH][BASE_VALUE] = bv;
            p.m_auraFlatModifiersGroup[UNIT_MOD_STAT_STRENGTH][BASE_PCT_EXCLUDE_CREATE] = bpec;
            p.m_auraPctModifiersGroup[UNIT_MOD_STAT_STRENGTH][BASE_PCT] = bp;
            p.m_auraFlatModifiersGroup[UNIT_MOD_STAT_STRENGTH][TOTAL_VALUE] = tv;
            p.m_auraPctModifiersGroup[UNIT_MOD_STAT_STRENGTH][TOTAL_PCT] = tp;
            Stats stat = STAT_STRENGTH;
            float value  = p.GetTotalStatValue(stat);          // RE-TYPED StatSystem.cpp:110 (Player::UpdateStats)
            p.SetStat(stat, int32(value));                      // RE-TYPED StatSystem.cpp:112
            PrintF(value); std::printf(" %d\n", p.m_stats[stat]);
        }
        else if (command == "maxhealth")
        {
            Player p; float ch, bv, bp, tv, tp, hps; int32 stamina; uint32 level;
            in >> ch >> bv >> bp >> tv >> tp >> stamina >> level >> hps;
            p.m_baseHealth = uint32(ch); p.m_level = uint8(level);
            p.m_auraFlatModifiersGroup[UNIT_MOD_HEALTH][BASE_VALUE] = bv;
            p.m_auraPctModifiersGroup[UNIT_MOD_HEALTH][BASE_PCT] = bp;
            p.m_auraFlatModifiersGroup[UNIT_MOD_HEALTH][TOTAL_VALUE] = tv;
            p.m_auraPctModifiersGroup[UNIT_MOD_HEALTH][TOTAL_PCT] = tp;
            p.m_stats[STAT_STAMINA] = stamina;
            std::vector<GtHpPerStaEntry> rows;
            if (hps >= 0.0f) { rows.resize(level + 1); rows[level].Health = hps; }
            sHpPerStaGameTable.SetData(std::move(rows));
            p.UpdateMaxHealth();
            // raw: recompute the pre-cast value the same way the body does
            float value = p.GetFlatModifierValue(UNIT_MOD_HEALTH, BASE_VALUE) + p.GetCreateHealth();
            value *= p.GetPctModifierValue(UNIT_MOD_HEALTH, BASE_PCT);
            value += p.GetFlatModifierValue(UNIT_MOD_HEALTH, TOTAL_VALUE) + p.GetHealthBonusFromStamina();
            value *= p.GetPctModifierValue(UNIT_MOD_HEALTH, TOTAL_PCT);
            std::printf("%llu ", static_cast<unsigned long long>(p.m_maxHealth)); PrintF(value); std::printf("\n");
        }
        else if (command == "maxpower")
        {
            Player p; float create, bv, bp, tv, tp;
            in >> create >> bv >> bp >> tv >> tp;
            p.m_baseMana = uint32(create);
            p.m_auraFlatModifiersGroup[UNIT_MOD_MANA][BASE_VALUE] = bv;
            p.m_auraPctModifiersGroup[UNIT_MOD_MANA][BASE_PCT] = bp;
            p.m_auraFlatModifiersGroup[UNIT_MOD_MANA][TOTAL_VALUE] = tv;
            p.m_auraPctModifiersGroup[UNIT_MOD_MANA][TOTAL_PCT] = tp;
            p.UpdateMaxPower(POWER_MANA);
            float value = p.GetFlatModifierValue(UNIT_MOD_MANA, BASE_VALUE) + p.GetCreatePowerValue(POWER_MANA);
            value *= p.GetPctModifierValue(UNIT_MOD_MANA, BASE_PCT);
            value += p.GetFlatModifierValue(UNIT_MOD_MANA, TOTAL_VALUE);
            value *= p.GetPctModifierValue(UNIT_MOD_MANA, TOTAL_PCT);
            std::printf("%d ", p.m_maxPower); PrintF(value); std::printf("\n");
        }
        else if (command == "armor")
        {
            Player p; float bv, bp, tv, tp, mult;
            in >> bv >> bp >> tv >> tp >> mult;
            p.m_auraFlatModifiersGroup[UNIT_MOD_ARMOR][BASE_VALUE] = bv;
            p.m_auraPctModifiersGroup[UNIT_MOD_ARMOR][BASE_PCT] = bp;
            p.m_auraFlatModifiersGroup[UNIT_MOD_ARMOR][TOTAL_VALUE] = tv;
            p.m_auraPctModifiersGroup[UNIT_MOD_ARMOR][TOTAL_PCT] = tp;
            p.m_bonusArmorPctMultiplier = mult;
            p.UpdateArmor();
            float value = bv; value *= bp; value += tv; value *= tp; value *= mult;
            std::printf("%d %d ", p.m_armor, p.m_bonusArmor); PrintF(value); std::printf("\n");
        }
        else if (command == "ap")
        {
            // RE-TYPED from Player::UpdateAttackPowerAndDamage, StatSystem.cpp:347-386: needs
            // sChrClassesStore, shapeshift store, m_activePlayerData and pets; the arithmetic is copied.
            Player p; int32 str, agi; float apstr, apagi, bp;
            in >> str >> agi >> apstr >> apagi >> bp;
            p.m_stats[STAT_STRENGTH] = str; p.m_stats[STAT_AGILITY] = agi;
            struct { float AttackPowerPerStrength; float AttackPowerPerAgility; } entryStorage{ apstr, apagi };
            auto const* entry = &entryStorage;
            UnitMods unitMod = UNIT_MOD_ATTACK_POWER;
            float val2 = 0.0f;
            float strengthValue = std::max(p.GetStat(STAT_STRENGTH) * entry->AttackPowerPerStrength, 0.0f);
            float agilityValue = std::max(p.GetStat(STAT_AGILITY) * entry->AttackPowerPerAgility, 0.0f);
            val2 = strengthValue + agilityValue;
            p.m_auraFlatModifiersGroup[unitMod][BASE_VALUE] = val2;   // SetStatFlatModifier(unitMod, BASE_VALUE, val2)
            p.m_auraPctModifiersGroup[unitMod][BASE_PCT] = bp;
            float base_attPower = p.GetFlatModifierValue(unitMod, BASE_VALUE) * p.GetPctModifierValue(unitMod, BASE_PCT);
            std::printf("%d ", int32(base_attPower)); PrintF(base_attPower); std::printf("\n");
        }
        else if (command == "rap")
        {
            Player p; uint32 lvl; int32 agi; float rapagi;
            in >> lvl >> agi >> rapagi;
            p.m_level = uint8(lvl); p.m_stats[STAT_AGILITY] = agi;
            struct { float RangedAttackPowerPerAgility; } entryStorage{ rapagi };
            auto const* entry = &entryStorage;
            float level = float(p.GetLevel());
            float val2 = (level + std::max(p.GetStat(STAT_AGILITY), 0.0f)) * entry->RangedAttackPowerPerAgility;
            std::printf("%d ", int32(val2)); PrintF(val2); std::printf("\n");
        }
        else if (command == "ratingmult")
        {
            Player p; float column; in >> column;
            SetRatingColumn(CR_CRIT_MELEE, column, p.m_level);
            PrintF(p.GetRatingMultiplier(CR_CRIT_MELEE)); std::printf("\n");
        }
        else if (command == "ratingbonus" || command == "mastery")
        {
            Player p; float auraTotal = 0.0f; int cr = CR_MASTERY; int32 rating; float column;
            if (command == "mastery") in >> auraTotal; else in >> cr;
            in >> rating >> column;
            ReadCurve(in);
            SetRatingColumn(CombatRating(cr), column, p.m_level);
            p.m_activePlayerDataStorage.CombatRatings[cr] = rating;
            if (command == "mastery")
            {
                // RE-TYPED from Player::UpdateMastery, StatSystem.cpp:548-549 (needs CanUseMastery + update fields)
                float value = auraTotal;   // = GetTotalAuraModifier(SPELL_AURA_MASTERY), a float sum of double amounts
                value += p.GetRatingBonusValue(CR_MASTERY);
                PrintF(value); std::printf("\n");
            }
            else
            {
                float linear = float(p.m_activePlayerData->CombatRatings[cr]) * p.GetRatingMultiplier(CombatRating(cr));
                PrintF(linear); std::printf(" "); PrintF(p.GetRatingBonusValue(CombatRating(cr))); std::printf("\n");
            }
        }
        else if (command == "applyrating")
        {
            Player p; int current, value; in >> current >> value;
            p.m_baseRatingValue[CR_MASTERY] = int16(current);
            p.ApplyRatingMod(CR_MASTERY, int32(value), true);
            std::printf("%d\n", int(p.m_baseRatingValue[CR_MASTERY]));
        }
        else if (command == "masteryamount")
        {
            // RE-TYPED from SpellEffectInfo::CalcValue, SpellInfo.cpp:600-602: value is SpellEffectValue (double),
            // Mastery is the float update field, BonusCoefficient is float.
            double basePoints; float Mastery, BonusCoefficient;
            in >> basePoints >> Mastery >> BonusCoefficient;
            SpellEffectValue value = basePoints;
            value += Mastery * BonusCoefficient;
            std::printf("%.17g %d\n", value, int32(value));
        }
        else if (command == "crit")
        {
            // RE-TYPED from Player::UpdateAllCritPercentages/UpdateCritPercentage, StatSystem.cpp:501-539
            float flat, pct, ratingBonus; in >> flat >> pct >> ratingBonus;
            float value = flat + pct + ratingBonus;
            PrintF(value); std::printf("\n");
        }
        else if (command == "spellcrit")
        {
            // RE-TYPED from Player::UpdateSpellCritChance, StatSystem.cpp:709-721
            float a, b, ratingBonus; in >> a >> b >> ratingBonus;
            float crit = 5.0f;
            crit += a;
            crit += b;
            crit += ratingBonus;
            PrintF(crit); std::printf("\n");
        }
        else if (command == "curvetype")
        {
            int type = 0; std::size_t count = 0; in >> type >> count;
            CurveEntry curve{0, static_cast<uint8>(type), 0};
            std::vector<DBCPosition2D> points(count);
            std::printf("%d\n", int(DetermineCurveType(&curve, points)));
        }
        else if (command == "calcpct")
        {
            float base, pct; in >> base >> pct;
            PrintF(CalculatePct(base, pct)); std::printf("\n");
        }
        else if (command == "basemp")
        {
            int cls; GtBaseMPEntry row; in >> cls;
            float* cols[] = { &row.Rogue, &row.Druid, &row.Hunter, &row.Mage, &row.Paladin, &row.Priest, &row.Shaman,
                &row.Warlock, &row.Warrior, &row.DeathKnight, &row.Monk, &row.DemonHunter, &row.Evoker, &row.Adventurer, &row.Traveler };
            for (float* c : cols) in >> *c;
            std::printf("%u\n", uint32(GetGameTableColumnForClass(&row, cls)));   // ObjectMgr.cpp:4434
        }
        else if (command == "fillgaps")
        {
            uint32 maxLevel, query; std::size_t n; in >> maxLevel >> query >> n;
            g_world.maxLevel = maxLevel; g_log_errors = 0;
            ObjectMgr mgr;
            auto info = std::make_unique<PlayerInfo>();
            info->levelInfo = std::make_unique<PlayerLevelInfo[]>(maxLevel);   // ObjectMgr.cpp:4326
            for (std::size_t i = 0; i < n; ++i)
            {
                uint32 level; int32 s[MAX_STATS];
                in >> level >> s[0] >> s[1] >> s[2] >> s[3] >> s[4];
                if (level > maxLevel) continue;                                  // ObjectMgr.cpp:4310-4319
                for (int k = 0; k < MAX_STATS; ++k) info->levelInfo[level - 1].stats[k] = s[k];
            }
            mgr._playerInfo.emplace(std::make_pair(Races(1), Classes(1)), std::move(info));
            try { ObjectMgr_FillPlayerLevelGaps(mgr._playerInfo); }
            catch (AbortSignal const&) { std::printf("ABORT\n"); g_world.maxLevel = 90; continue; }
            PlayerLevelInfo const& out = mgr._playerInfo.begin()->second->levelInfo[query - 1];
            std::printf("%d %d %d %d %d %d\n", g_log_errors, out.stats[0], out.stats[1], out.stats[2], out.stats[3], out.stats[4]);
            g_world.maxLevel = 90;
        }
        else if (command == "buildlevelinfo")
        {
            int cls; uint32 maxLevel, level; in >> cls >> maxLevel >> level;
            g_world.maxLevel = maxLevel;
            ObjectMgr mgr;
            auto info = std::make_unique<PlayerInfo>();
            info->levelInfo = std::make_unique<PlayerLevelInfo[]>(maxLevel);
            for (int k = 0; k < MAX_STATS; ++k) in >> info->levelInfo[maxLevel - 1].stats[k];
            mgr._playerInfo.emplace(std::make_pair(Races(1), Classes(cls)), std::move(info));
            PlayerLevelInfo out;
            mgr.BuildPlayerLevelInfo(1, uint8(cls), uint8(level), &out);
            std::printf("%d %d %d %d %d\n", out.stats[0], out.stats[1], out.stats[2], out.stats[3], out.stats[4]);
            g_world.maxLevel = 90;
        }
        else
            std::printf("ERR unknown command\n");
        }
        catch (std::exception const& e) { std::printf("ERR %s\n", e.what()); }
        std::fflush(stdout);
    }
    return 0;
}
