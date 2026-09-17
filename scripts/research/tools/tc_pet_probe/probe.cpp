// Differential probe for the controlled-unit stat oracle (controlled_units/stats.py).
//
// tc_pet_decls.inc / tc_pet_bodies.inc are pulled verbatim out of the sibling
// TrinityCore checkout by extract.py.  This file supplies only the scaffolding
// the bodies reference, with Trinity's member names.  BOUNDED probe: the cut
// points below replace engine lookups with driver-supplied values.
//
// Cut points (semantics copied from the cited lines, not extracted):
//   Unit.h:771-777       GetStat / SetStat / GetArmor / SetArmor / GetResistance / SetResistance
//   Unit.h:1412-1420     SetCreateStat / SetCreateHealth(uint32) / SetCreateMana(uint32) / GetCreate*
//   Unit.h:1573          SetBaseWeaponDamage
//   Unit.cpp:10013-10020 SetMaxHealth(uint64): 0 -> 1 (group-update side effects dropped)
//   Unit.cpp:10095-10102 SetMaxPower: index gate, stores the int32 value
//   Unit.cpp:5696-5711   SetPowerType: IsUsedByNPCs gate (driver flag), DisplayPower, UpdateMaxPower
//   Unit.cpp:5218-5281   UpdateStatBuffMod: writes only m_floatStatPos/NegBuff (not read here) -> no-op
//   Unit.cpp:7083-7118   SpellBaseDamageBonusDone: driver-supplied per school mask
//   BaseEntity.h:299-303 SetUpdateFieldStatValue: std::max(value, T(0))
//   Creature.cpp:1687    GetHealthMod: Rate.Creature.HP.* default 1.0f
//   Creature.cpp:3059    ApplyLevelScaling: no-op (the driver supplies the resolved levels / ES values)
//   DB2Stores.cpp:2476   EvaluateExpectedStat: driver-supplied (stat, level) -> float table
//   ObjectMgr.cpp:3769   GetPetLevelInfo: driver-supplied resolved row or nullptr
//   ObjectMgr.cpp:9990   GetCreatureBaseStats: driver-supplied BaseMana
//
// Protocol: one request per line, one reply per line.
//   init <is_pet> <hunter_mask> <owner_class> <petlevel> <entry> <unit_class> <base_attack_time>
//        <calc_power> <power_npc_ok> <max_base_power>
//        <has_pinfo> <hp> <mana> <armor> <s0> <s1> <s2> <s3> <s4>
//        <owner_s0..s4> <owner_armor> <owner_ap> <owner_rap>
//        <pos0..6> <neg0..6> <sbdb_frost> <sbdb_nature> <sbdb_fire> <sbdb_shadow>
//        <health_modifier> <mana_modifier> <base_mana>
//        <es_health_pet> <es_dps_pet>
//        <guardian_pre> <select_level> <es_health_sel> <es_dps_sel>
//   -> stats0..4 maxhealth maxpower armor_base armor_bonus ap %a(apmult) bonus %a(min) %a(max)
//      %a(wmin) %a(wmax) create_health create_mana pet_type attack_time display_power
//   ucast <%a float>   -> (uint32) of the float as compiled here (UB probe for negative values)

#include "Define.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#define TC_LOG_ERROR(...) ((void)0)
#define ASSERT(x) ((void)(x))
#define ABORT_MSG(...) std::abort()

template<class T> constexpr std::underlying_type_t<T> AsUnderlyingType(T v) { return static_cast<std::underlying_type_t<T>>(v); }

#include "tc_pet_decls.inc"

enum Classes : uint8 { CLASS_NONE = 0, CLASS_WARRIOR = 1, CLASS_PALADIN = 2, CLASS_HUNTER = 3, CLASS_ROGUE = 4, CLASS_PRIEST = 5,
                       CLASS_DEATH_KNIGHT = 6, CLASS_SHAMAN = 7, CLASS_MAGE = 8, CLASS_WARLOCK = 9 };
enum SpellSchoolMask : uint32 { SPELL_SCHOOL_MASK_NORMAL = 1, SPELL_SCHOOL_MASK_FIRE = 4, SPELL_SCHOOL_MASK_NATURE = 8,
                                SPELL_SCHOOL_MASK_FROST = 16, SPELL_SCHOOL_MASK_SHADOW = 32 };
enum TypeID { TYPEID_UNIT = 5, TYPEID_PLAYER = 6 };
enum class CreatureClassifications : uint32 { Normal = 0 };
constexpr uint32 UNIT_MASK_GUARDIAN = 0x2, UNIT_MASK_PET = 0x8, UNIT_MASK_HUNTER_PET = 0x20, UNIT_MASK_CONTROLABLE_GUARDIAN = 0x100;  // UnitDefines.h values
constexpr uint32 BASE_ATTACK_TIME = 2000;
constexpr float BASE_MINDAMAGE = 1.0f, BASE_MAXDAMAGE = 2.0f;
constexpr uint32 PET_GHOUL = 26125, PET_SPIRIT_WOLF = 29264;  // TemporarySummon.h:24-39

namespace UF
{
    struct UnitData
    {
        std::array<int32, MAX_STATS> Stats{};
        std::array<int32, MAX_SPELL_SCHOOL> Resistances{};
        std::array<int32, MAX_SPELL_SCHOOL> BonusResistanceMods{};
        int32 AttackPower = 0, AttackPowerModPos = 0, AttackPowerModNeg = 0;
        float AttackPowerMultiplier = 0.0f;
        int32 RangedAttackPower = 0, RangedAttackPowerModPos = 0, RangedAttackPowerModNeg = 0;
        float RangedAttackPowerMultiplier = 0.0f;
        float MainHandWeaponAttackPower = 0.0f, OffHandWeaponAttackPower = 0.0f, RangedWeaponAttackPower = 0.0f;
        float MinDamage = 0.0f, MaxDamage = 0.0f;
        uint32 BaseHealth = 0, BaseMana = 0;
        int32 ContentTuningID = 0;
        uint64 MaxHealth = 0;
        std::array<int32, MAX_POWERS_PER_CLASS> MaxPower{};
        int8 DisplayPower = 0;
        uint8 Level = 0;
    };
    struct ActivePlayerData
    {
        std::array<int32, MAX_SPELL_SCHOOL> ModDamageDonePos{};
        std::array<int32, MAX_SPELL_SCHOOL> ModDamageDoneNeg{};
        int32 PetSpellPower = 0;
    };
}

template<class T> struct FieldSetter { T* p; };
struct UnitDataProxy
{
    UF::UnitData* d;
    template<class T> FieldSetter<T> ModifyValue(T UF::UnitData::* m) { return { &(d->*m) }; }
};
class Unit;
struct ValuesStub
{
    UF::UnitData* d;
    UnitDataProxy ModifyValue(UF::UnitData* Unit::*) { return { d }; }
};

struct CreatureTemplate { uint32 Entry = 0; uint32 dmgschool = 0; std::array<int32, MAX_SPELL_SCHOOL> resistance{}; uint32 unit_class = 0;
                          uint32 BaseAttackTime = 0; CreatureClassifications Classification = CreatureClassifications::Normal; };
struct CreatureDifficulty { float HealthModifier = 1.0f; float ManaModifier = 1.0f; float ArmorModifier = 1.0f; int32 ContentTuningID = 0;
                            int32 GetHealthScalingExpansion() const { return 11; } };
struct CreatureBaseStats { uint32 BaseMana = 0; uint32 AttackPower = 0; uint32 RangedAttackPower = 0; };

// ---- driver-supplied world ---------------------------------------------------
struct World
{
    bool has_pinfo = false; PetLevelInfo pinfo;
    CreatureBaseStats base;
    std::map<std::pair<int, uint32>, float> es;  // (ExpectedStatType, level) -> value
    bool power_npc_ok = true; int32 max_base_power = 0;
    std::array<int32, 4> sbdb{};  // frost nature fire shadow
} W;

struct ObjectMgrStub
{
    PetLevelInfo const* GetPetLevelInfo(uint32, uint8) const { return W.has_pinfo ? &W.pinfo : nullptr; }
    CreatureBaseStats const* GetCreatureBaseStats(uint8, uint8) const { return &W.base; }
    uint32 GetXPForLevel(uint8) const { return 0; }
} objmgr;
ObjectMgrStub* sObjectMgr = &objmgr;

struct PowerTypeFlagsHolder { bool npc; bool HasFlag(int) const { return npc; } };
namespace PowerTypeFlags { constexpr int IsUsedByNPCs = 0x80; }
struct PowerTypeEntry { int32 MaxBasePower; bool npc; PowerTypeFlagsHolder GetFlags() const { return { npc }; } };
struct DB2ManagerStub
{
    PowerTypeEntry pt;
    float EvaluateExpectedStat(ExpectedStatType stat, uint32 level, int32, uint32, Classes, int32) const
    {
        auto it = W.es.find({ int(stat), level });
        return it == W.es.end() ? 1.0f : it->second;  // DB2Stores.cpp:2482-2483 (no row -> 1.0f)
    }
    PowerTypeEntry const* GetPowerTypeEntry(Powers power)
    {
        pt = { power == POWER_MANA ? 0 : W.max_base_power, power == POWER_MANA ? true : W.power_npc_ok };
        return &pt;
    }
} sDB2Manager;

class Player;
class Pet;

class Unit
{
public:
    virtual ~Unit() = default;
    UF::UnitData data;
    UF::UnitData* m_unitData = &data;
    ValuesStub m_values{ &data };
    std::array<std::array<float, MODIFIER_TYPE_FLAT_END>, UNIT_MOD_END> m_auraFlatModifiersGroup{};
    std::array<std::array<float, MODIFIER_TYPE_PCT_END>, UNIT_MOD_END> m_auraPctModifiersGroup{};
    std::array<float, MAX_STATS> m_createStats{};
    std::array<std::array<float, 2>, MAX_ATTACK> m_weaponDamage{};
    std::array<uint32, MAX_ATTACK> m_baseAttackSpeed{};
    uint32 m_unitTypeMask = 0;
    bool m_canModifyStats = false;
    uint8 m_class = 0;
    uint32 m_entry = 0;
    bool m_isPlayer = false;

    Unit()
    {
        for (uint8 i = 0; i < UNIT_MOD_END; ++i)  // Unit.cpp:342-349
        {
            m_auraFlatModifiersGroup[i][BASE_VALUE] = 0.0f;
            m_auraFlatModifiersGroup[i][BASE_PCT_EXCLUDE_CREATE] = 100.0f;
            m_auraFlatModifiersGroup[i][TOTAL_VALUE] = 0.0f;
            m_auraPctModifiersGroup[i][BASE_PCT] = 1.0f;
            m_auraPctModifiersGroup[i][TOTAL_PCT] = 1.0f;
        }
        m_auraPctModifiersGroup[UNIT_MOD_DAMAGE_OFFHAND][TOTAL_PCT] = 0.5f;
        for (uint8 i = 0; i < MAX_ATTACK; ++i) { m_weaponDamage[i][MINDAMAGE] = BASE_MINDAMAGE; m_weaponDamage[i][MAXDAMAGE] = BASE_MAXDAMAGE; }
    }

    // --- extracted (Unit.cpp / StatSystem.cpp) ---
    float GetTotalStatValue(Stats stat) const;
    float GetTotalAuraModValue(UnitMods unitMod) const;
    void SetStatFlatModifier(UnitMods unitMod, UnitModifierFlatType modifierType, float val);
    void SetStatPctModifier(UnitMods unitMod, UnitModifierPctType modifierType, float val);
    float GetFlatModifierValue(UnitMods unitMod, UnitModifierFlatType modifierType) const;
    float GetPctModifierValue(UnitMods unitMod, UnitModifierPctType modifierType) const;
    void UpdateUnitMod(UnitMods unitMod);
    SpellSchools GetSpellSchoolByAuraGroup(UnitMods unitMod) const;
    Stats GetStatByAuraGroup(UnitMods unitMod) const;
    float GetTotalAttackPowerValue(WeaponAttackType attType, bool includeWeapon = true) const;
    float GetWeaponDamageRange(WeaponAttackType attType, WeaponDamageRange type) const;
    uint32 GetBaseAttackTime(WeaponAttackType att) const;
    void UpdateAllResistances();
    virtual int32 GetCreatePowerValue(Powers power) const;

    // --- virtual stat interface (Unit.h) ---
    virtual bool UpdateStats(Stats) { return false; }
    virtual bool UpdateAllStats() { return false; }
    virtual void UpdateResistances(uint32) {}
    virtual void UpdateArmor() {}
    virtual void UpdateMaxHealth() {}
    virtual void UpdateMaxPower(Powers) {}
    virtual void UpdateAttackPowerAndDamage(bool = false) {}
    virtual void UpdateDamagePhysical(WeaponAttackType) {}
    virtual uint32 GetPowerIndex(Powers power) const { return power == GetPowerType() ? 0 : MAX_POWERS; }

    // --- cut points (see header) ---
    bool CanModifyStats() const { return m_canModifyStats; }
    bool haveOffhandWeapon() const { return false; }
    float GetStat(Stats stat) const { return float(m_unitData->Stats[stat]); }
    void SetStat(Stats stat, int32 val) { data.Stats[stat] = val; }
    int32 GetResistance(SpellSchools school) const { return m_unitData->Resistances[school]; }
    int32 GetBonusResistanceMod(SpellSchools school) const { return m_unitData->BonusResistanceMods[school]; }
    void SetResistance(SpellSchools school, int32 val) { data.Resistances[school] = val; }
    void SetBonusResistanceMod(SpellSchools school, int32 val) { data.BonusResistanceMods[school] = val; }
    uint32 GetArmor() const { return GetResistance(SPELL_SCHOOL_NORMAL); }
    void SetArmor(int32 val, int32 bonusVal) { SetResistance(SPELL_SCHOOL_NORMAL, val); SetBonusResistanceMod(SPELL_SCHOOL_NORMAL, bonusVal); }
    void SetCreateStat(Stats stat, float val) { m_createStats[stat] = val; }
    float GetCreateStat(Stats stat) const { return m_createStats[stat]; }
    void SetCreateHealth(uint32 val) { data.BaseHealth = val; }
    uint32 GetCreateHealth() const { return m_unitData->BaseHealth; }
    void SetCreateMana(uint32 val) { data.BaseMana = val; }
    uint32 GetCreateMana() const { return m_unitData->BaseMana; }
    void SetMaxHealth(uint64 val) { if (!val) val = 1; data.MaxHealth = val; }
    void SetHealth(uint64) {}
    void SetFullHealth() {}
    void SetFullPower(Powers) {}
    void SetMaxPower(Powers power, int32 val) { uint32 idx = GetPowerIndex(power); if (idx == MAX_POWERS || idx >= MAX_POWERS_PER_CLASS) return; data.MaxPower[idx] = val; }
    Powers GetPowerType() const { return Powers(m_unitData->DisplayPower); }
    void SetPowerType(Powers power, bool /*sendUpdate*/ = true, bool onInit = false)
    {
        if (!onInit && GetPowerType() == power) return;
        PowerTypeEntry const* e = sDB2Manager.GetPowerTypeEntry(power);
        if (!e->GetFlags().HasFlag(PowerTypeFlags::IsUsedByNPCs)) return;  // IsCreature() always true here
        data.DisplayPower = int8(power);
        UpdateMaxPower(power);
    }
    void SetBaseWeaponDamage(WeaponAttackType attType, WeaponDamageRange damageRange, float value) { m_weaponDamage[attType][damageRange] = value; }
    void SetBaseAttackTime(WeaponAttackType att, uint32 val) { m_baseAttackSpeed[att] = val; }
    void SetAttackPower(int32 v) { data.AttackPower = v; }
    void SetAttackPowerMultiplier(float v) { data.AttackPowerMultiplier = v; }
    template<class T> void SetUpdateFieldStatValue(FieldSetter<T> s, T value) { *s.p = std::max(value, T(0)); }
    void UpdateStatBuffMod(Stats) {}
    void SetLevel(uint8 lvl, bool = true) { data.Level = lvl; }
    uint8 GetLevel() const { return m_unitData->Level; }
    uint8 GetClass() const { return m_class; }
    uint32 GetEntry() const { return m_entry; }
    TypeID GetTypeId() const { return m_isPlayer ? TYPEID_PLAYER : TYPEID_UNIT; }
    bool IsPet() const { return (m_unitTypeMask & UNIT_MASK_PET) != 0; }
    bool IsHunterPet() const { return (m_unitTypeMask & UNIT_MASK_HUNTER_PET) != 0; }
    bool IsGuardian() const { return (m_unitTypeMask & UNIT_MASK_GUARDIAN) != 0; }
    Player* ToPlayer() { return m_isPlayer ? reinterpret_cast<Player*>(this) : nullptr; }
    int32 SpellBaseDamageBonusDone(SpellSchoolMask mask) const
    {
        switch (mask) { case SPELL_SCHOOL_MASK_FROST: return W.sbdb[0]; case SPELL_SCHOOL_MASK_NATURE: return W.sbdb[1];
                        case SPELL_SCHOOL_MASK_FIRE: return W.sbdb[2]; case SPELL_SCHOOL_MASK_SHADOW: return W.sbdb[3]; default: return 0; }
    }
    void SetMeleeDamageSchool(SpellSchools) {}
    void SetObjectScale(float) {}
    float GetNativeObjectScale() const { return 1.0f; }
    uint32 GetDisplayId() const { return 0; }
    void SetDisplayId(uint32) {}
    bool HasAura(uint32) const { return false; }
    void AddAura(uint32, Unit*) {}
    Powers CalculateDisplayPowerType() const;
    int32 calcPower = 0;
};

class Player : public Unit
{
public:
    UF::ActivePlayerData apd;
    UF::ActivePlayerData* m_activePlayerData = &apd;
    Player() { m_isPlayer = true; }
    void SetPetSpellPower(int32 v) { apd.PetSpellPower = v; }
};

Powers Unit::CalculateDisplayPowerType() const { return Powers(calcPower); }

class Creature : public Unit
{
public:
    CreatureTemplate tmpl;
    CreatureDifficulty diff;
    CreatureTemplate const* GetCreatureTemplate() const { return &tmpl; }
    CreatureDifficulty const* GetCreatureDifficulty() const { return &diff; }
    static float GetHealthMod(CreatureClassifications) { return 1.0f; }
    void ApplyLevelScaling() {}
    void ResetPlayerDamageReq() {}
    uint32 GetPowerIndex(Powers power) const override;
    int32 GetCreatePowerValue(Powers power) const override;
    void UpdateLevelDependantStats();
    uint64 GetMaxHealthByLevel(uint8 level) const;
    float GetBaseDamageForLevel(uint8 level) const;
    float GetBaseArmorForLevel(uint8 level) const;
};

class Guardian : public Creature
{
public:
    Unit* m_owner = nullptr;
    int32 m_bonusSpellDamage = 0;
    float m_statFromOwner[MAX_STATS] = {};
    Unit* GetOwner() const { return m_owner; }
    bool IsPetGhoul() const { return GetEntry() == PET_GHOUL; }
    bool IsSpiritWolf() const { return GetEntry() == PET_SPIRIT_WOLF; }
    Pet* ToPet() { return reinterpret_cast<Pet*>(this); }
    bool InitStatsForLevel(uint8 petlevel);
    bool UpdateStats(Stats stat) override;
    bool UpdateAllStats() override;
    void UpdateResistances(uint32 school) override;
    void UpdateArmor() override;
    void UpdateMaxHealth() override;
    void UpdateMaxPower(Powers power) override;
    void UpdateAttackPowerAndDamage(bool ranged = false) override;
    void UpdateDamagePhysical(WeaponAttackType attType) override;
    void SetBonusDamage(int32 damage);
};

class Pet : public Guardian
{
public:
    void SetPetNextLevelExperience(uint32) {}
};

#include "tc_pet_bodies.inc"

static bool rd(std::istringstream& in, double& v) { std::string t; if (!(in >> t)) return false; v = std::strtod(t.c_str(), nullptr); return true; }

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd == "ucast")
        {
            double v; rd(in, v);
            volatile float f = float(v);
            uint32 r = (uint32)f;
            std::printf("%u\n", r);
            std::fflush(stdout);
            continue;
        }
        if (cmd != "init") { std::printf("error\n"); std::fflush(stdout); continue; }
        std::vector<double> a; double v;
        while (rd(in, v)) a.push_back(v);
        if (a.size() != 54) { std::printf("error args %zu\n", a.size()); std::fflush(stdout); continue; }
        size_t i = 0;
        auto I = [&]() { return int64(a[i++]); };
        auto F = [&]() { return float(a[i++]); };
        W = World();
        Player owner;
        Pet pet;
        bool isPet = I(); bool hunterMask = I();
        owner.m_class = uint8(I());
        uint8 petlevel = uint8(I());
        pet.m_entry = uint32(I()); pet.tmpl.Entry = pet.m_entry;
        pet.tmpl.unit_class = uint32(I()); pet.m_class = uint8(pet.tmpl.unit_class);
        pet.tmpl.BaseAttackTime = uint32(I());
        pet.calcPower = int32(I()); W.power_npc_ok = I(); W.max_base_power = int32(I());
        W.has_pinfo = I();
        W.pinfo.health = uint16(I()); W.pinfo.mana = uint16(I()); W.pinfo.armor = uint16(I());
        for (int s = 0; s < MAX_STATS; ++s) W.pinfo.stats[s] = uint16(I());
        for (int s = 0; s < MAX_STATS; ++s) owner.data.Stats[s] = int32(I());
        owner.data.Resistances[0] = int32(I());
        owner.data.AttackPower = int32(I());
        owner.data.RangedAttackPower = int32(I());
        for (int s = 0; s < MAX_SPELL_SCHOOL; ++s) owner.apd.ModDamageDonePos[s] = int32(I());
        for (int s = 0; s < MAX_SPELL_SCHOOL; ++s) owner.apd.ModDamageDoneNeg[s] = int32(I());
        for (int s = 0; s < 4; ++s) W.sbdb[s] = int32(I());
        pet.diff.HealthModifier = F(); pet.diff.ManaModifier = F();
        W.base.BaseMana = uint32(I());
        float esHealthPet = F(), esDpsPet = F();
        bool guardianPre = I(); uint8 selectLevel = uint8(I());
        float esHealthSel = F(), esDpsSel = F();
        pet.m_owner = &owner;
        pet.m_unitTypeMask = UNIT_MASK_GUARDIAN | (isPet ? (UNIT_MASK_PET | UNIT_MASK_CONTROLABLE_GUARDIAN) : 0) | (hunterMask ? UNIT_MASK_HUNTER_PET : 0);
        if (guardianPre)
        {
            W.es[{ int(ExpectedStatType::CreatureHealth), selectLevel }] = esHealthSel;
            W.es[{ int(ExpectedStatType::CreatureAutoAttackDps), selectLevel }] = esDpsSel;
            W.es[{ int(ExpectedStatType::CreatureArmor), selectLevel }] = 0.0f;
            pet.SetLevel(selectLevel);
            pet.UpdateLevelDependantStats();   // Creature.cpp:1625 (verbatim), level = SelectLevel level
        }
        W.es[{ int(ExpectedStatType::CreatureHealth), petlevel }] = esHealthPet;
        W.es[{ int(ExpectedStatType::CreatureAutoAttackDps), petlevel }] = esDpsPet;
        pet.InitStatsForLevel(petlevel);        // Pet.cpp:839 (verbatim)
        UF::UnitData const& d = pet.data;
        uint32 pidx = pet.GetPowerIndex(pet.GetPowerType());
        std::printf("%d %d %d %d %d %llu %d %d %d %d %a %d %a %a %a %a %u %u %d %u %d\n",
            d.Stats[0], d.Stats[1], d.Stats[2], d.Stats[3], d.Stats[4], (unsigned long long)d.MaxHealth,
            pidx < MAX_POWERS_PER_CLASS ? d.MaxPower[pidx] : -1, d.Resistances[0], d.BonusResistanceMods[0], d.AttackPower,
            d.AttackPowerMultiplier, pet.m_bonusSpellDamage, d.MinDamage, d.MaxDamage,
            pet.m_weaponDamage[BASE_ATTACK][MINDAMAGE], pet.m_weaponDamage[BASE_ATTACK][MAXDAMAGE],
            d.BaseHealth, d.BaseMana, 0, pet.GetBaseAttackTime(BASE_ATTACK), int(d.DisplayPower));
        std::fflush(stdout);
    }
    return 0;
}
