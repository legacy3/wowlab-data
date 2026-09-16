// Differential probe for the proc research package.
//
// tc_proc_decls.inc / tc_proc_bodies.inc are pulled verbatim out of the
// sibling TrinityCore checkout by extract.py.  This file supplies only the
// minimum scaffolding those bodies reference (Unit, Player, SpellInfo, Aura,
// ProcEventInfo, ... with the member names Trinity uses) and a line protocol.
// Floats are printed with %a so the Python side can compare bit for bit.
//
// Protocol (one request per line, one reply per line):
//   gen <id> <pf0> <pf1> <chance> <cooldown> <charges> <baseppm> <family> <attr3> <neff>
//       { <index> <effect> <aura> <trigger> <bp> <cm0> <cm1> <cm2> <cm3> } * neff
//     -> "none" | school family fm0 fm1 fm2 fm3 pf0 pf1 type phase hit attrs disable ppm chance cooldown charges
//   match <school> <family> <fm0> <fm1> <fm2> <fm3> <pf0> <pf1> <type> <phase> <hit> <attrs>
//         <tm0> <tm1> <etype> <ephase> <ehit> <eschool> <has_spell> <cost_positive>
//         <has_info> <efamily> <ef0> <ef1> <ef2> <ef3> <actor_player> <has_target> <honor>
//     -> 0 | 1
//   ppm <weapon_speed> <ppm>                                        -> %a
//   weapon <main_ready> <main_speed> <has_off> <off_ready> <off_speed> -> %a
//   rppm <base> <nmods> {type param coeff}* <haste> <rhaste> <shaste> <reghaste>
//        <is_player> <crit> <rcrit> <scrit> <class> <spec> <race> <ilvl> <bg>
//        <naura> {id}* <nrpp> {level points}*                      -> %a
//   chance <entry_chance> <entry_ppm> <base_ppm> <has_caster> <has_damage> <speed>
//          <attrs> <actor_level> <attempt_ms_ago> <success_ms_ago>  -> %a

#include "Define.h"
#include "EnumFlag.h"
#include "FlagsArray.h"
#include "Duration.h"
#include "RaceMask.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace std::chrono;

#define TC_LOG_INFO(...) ((void)0)
#define TC_LOG_ERROR(...) ((void)0)
static uint32 getMSTime() { return 0; }
static uint32 GetMSTimeDiffToNow(uint32) { return 0; }

#include "tc_proc_decls.inc"

enum WeaponAttackType : uint8 { BASE_ATTACK = 0, OFF_ATTACK = 1, RANGED_ATTACK = 2, MAX_ATTACK };
enum Difficulty : uint8 { DIFFICULTY_NONE = 0 };
enum ItemQualities { ITEM_QUALITY_RARE = 3 };
enum InventoryType { INVTYPE_CHEST = 5 };
enum class ChrSpecialization : uint32 { None = 0 };
enum class SpellModOp : uint8 { ProcCharges = 4, ProcChance = 18, ProcFrequency = 26, ProcCooldown = 38 };

template<> struct std::hash<std::pair<uint32, Difficulty>>
{
    size_t operator()(std::pair<uint32, Difficulty> const& p) const noexcept { return (size_t(p.first) << 8) ^ p.second; }
};

struct SpellProcsPerMinuteModEntry { uint32 ID; int32 Type; int32 Param; float Coeff; uint32 SpellProcsPerMinuteID; };
struct SpellPowerCost { int32 Power; int32 Amount; };

class Unit;
class Player;

static std::map<uint32, float> g_randPropPoints;
float GetRandomPropertyPoints(uint32 itemLevel, uint32 /*quality*/, uint32 /*inventoryType*/, uint32 /*subClass*/)
{
    auto itr = g_randPropPoints.find(itemLevel);
    return itr == g_randPropPoints.end() ? 0.0f : itr->second;
}

struct SpellEffectInfo
{
    uint32 Effect = 0;
    uint32 ApplyAuraName = 0;
    uint32 EffectIndex = 0;
    flag128 SpellClassMask;
    uint32 TriggerSpell = 0;
    int32 BasePointsInt = 0;
    bool IsEffect() const;
    bool IsAura() const;
    bool IsAreaAuraEffect() const;
    bool IsUnitOwnedAuraEffect() const;
    int32 CalcValueAsInt() const { return BasePointsInt; }
};

class SpellInfo
{
public:
    uint32 Id = 0;
    ::Difficulty Difficulty = DIFFICULTY_NONE;
    ProcFlagsInit ProcFlags;
    uint32 ProcChance = 0;
    uint32 ProcCharges = 0;
    uint32 ProcCooldown = 0;
    float ProcBasePPM = 0.0f;
    std::vector<SpellProcsPerMinuteModEntry const*> ProcPPMMods;
    uint32 SpellFamilyName = 0;
    flag128 SpellFamilyFlags;
    uint32 Attr[17] = {};
    std::vector<SpellEffectInfo> Effects;

    std::vector<SpellEffectInfo> const& GetEffects() const { return Effects; }
#define HAS_ATTR(n) bool HasAttribute(SpellAttr##n a) const { return (Attr[n] & a) != 0; }
    HAS_ATTR(0) HAS_ATTR(1) HAS_ATTR(2) HAS_ATTR(3) HAS_ATTR(4) HAS_ATTR(5) HAS_ATTR(6) HAS_ATTR(7)
    HAS_ATTR(8) HAS_ATTR(9) HAS_ATTR(10) HAS_ATTR(11) HAS_ATTR(12) HAS_ATTR(13) HAS_ATTR(14) HAS_ATTR(15) HAS_ATTR(16)
#undef HAS_ATTR
    bool IsAffected(uint32 familyName, flag128 const& familyFlags) const;
    float CalcProcPPM(Unit* caster, int32 itemLevel) const;
};

struct UnitDataStub { float ModHaste = 1.0f, ModRangedHaste = 1.0f, ModSpellHaste = 1.0f, ModHasteRegen = 1.0f; };
struct ActivePlayerDataStub { float CritPercentage = 0.0f, RangedCritPercentage = 0.0f, SpellCritPercentage = 0.0f; };
struct MapStub { bool Bg = false; bool IsBattlegroundOrArena() const { return Bg; } };

class Unit
{
public:
    virtual ~Unit() = default;
    UnitDataStub UnitData;
    UnitDataStub* m_unitData = &UnitData;
    bool IsPlayerUnit = false;
    uint8 Class = 1;
    uint8 Race = 1;
    uint8 Level = 80;
    std::set<uint32> Auras;
    MapStub Map;
    uint32 AttackTime[MAX_ATTACK] = { 2000, 2000, 2000 };
    bool AttackReady[MAX_ATTACK] = { true, true, true };
    bool HasOffhand = false;

    Player* ToPlayer();
    uint8 GetClass() const { return Class; }
    uint32 GetClassMask() const;
    uint8 GetRace() const { return Race; }
    uint8 GetLevel() const { return Level; }
    MapStub* GetMap() { return &Map; }
    bool HasAura(uint32 id) const { return Auras.count(id) != 0; }
    uint32 GetBaseAttackTime(WeaponAttackType att) const { return AttackTime[att]; }
    Player* GetSpellModOwner() const { return nullptr; }
    bool isAttackReady(WeaponAttackType type = BASE_ATTACK) const { return AttackReady[type]; }
    bool haveOffhandWeapon() const { return HasOffhand; }
    float GetPPMProcChance(uint32 WeaponSpeed, float PPM, SpellInfo const* spellProto) const;
    float GetWeaponProcChance() const;
};

class Player : public Unit
{
public:
    ActivePlayerDataStub ActiveData;
    ActivePlayerDataStub* m_activePlayerData = &ActiveData;
    ChrSpecialization Spec = ChrSpecialization::None;
    bool HonorTarget = true;
    ChrSpecialization GetPrimarySpecialization() const { return Spec; }
    template<class T> void ApplySpellMod(SpellInfo const*, SpellModOp, T&) const { }
    bool isHonorOrXPTarget(Unit const*) const { return HonorTarget; }
};

Player* Unit::ToPlayer() { return IsPlayerUnit ? static_cast<Player*>(this) : nullptr; }

class Spell
{
public:
    std::vector<SpellPowerCost> Costs;
    std::vector<SpellPowerCost> const& GetPowerCost() const { return Costs; }
};

class DamageInfo
{
public:
    WeaponAttackType AttackType = BASE_ATTACK;
    WeaponAttackType GetAttackType() const { return AttackType; }
};

class ProcEventInfo
{
public:
    Unit* _actor = nullptr;
    Unit* _actionTarget = nullptr;
    ProcFlagsInit _typeMask;
    ProcFlagsSpellType _spellTypeMask = PROC_SPELL_TYPE_NONE;
    ProcFlagsSpellPhase _spellPhaseMask = PROC_SPELL_PHASE_NONE;
    ProcFlagsHit _hitMask = PROC_HIT_NONE;
    Spell* _spell = nullptr;
    DamageInfo* _damageInfo = nullptr;
    SpellInfo const* _spellInfo = nullptr;
    SpellSchoolMask _schoolMask = SPELL_SCHOOL_MASK_NONE;

    Unit* GetActor() const { return _actor; }
    Unit* GetActionTarget() const { return _actionTarget; }
    ProcFlagsInit GetTypeMask() const { return _typeMask; }
    ProcFlagsSpellType GetSpellTypeMask() const { return _spellTypeMask; }
    ProcFlagsSpellPhase GetSpellPhaseMask() const { return _spellPhaseMask; }
    ProcFlagsHit GetHitMask() const { return _hitMask; }
    SpellInfo const* GetSpellInfo() const { return _spellInfo; }
    SpellSchoolMask GetSchoolMask() const { return _schoolMask; }
    DamageInfo* GetDamageInfo() const { return _damageInfo; }
    Spell const* GetProcSpell() const { return _spell; }
};

class SpellMgr
{
public:
    std::vector<SpellInfo> mSpellInfoMap;
    std::unordered_map<std::pair<uint32, Difficulty>, SpellProcEntry> mSpellProcMap;
    SpellProcEntry const* GetSpellProcEntry(SpellInfo const* spellInfo) const
    {
        auto itr = mSpellProcMap.find({ spellInfo->Id, spellInfo->Difficulty });
        return itr == mSpellProcMap.end() ? nullptr : &itr->second;
    }
    static bool CanSpellTriggerProcOnEvent(SpellProcEntry const& procEntry, ProcEventInfo& eventInfo);
    void GenerateDefaultProcs();
};

static TimePoint g_now = TimePoint(std::chrono::hours(1000));
namespace GameTime { TimePoint Now() { return g_now; } }

class Aura
{
public:
    SpellInfo const* m_spellInfo = nullptr;
    Unit* Caster = nullptr;
    int32 CastItemLevel = -1;
    TimePoint m_lastProcAttemptTime;
    TimePoint m_lastProcSuccessTime;
    Unit* GetCaster() const { return Caster; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    int32 GetCastItemLevel() const { return CastItemLevel; }
    float CalcProcChance(SpellProcEntry const& procEntry, ProcEventInfo& eventInfo) const;
    float CalcPPMProcChance(Unit* actor) const;
};

#include "tc_proc_bodies.inc"

static flag128 read128(std::istream& in)
{
    uint32 a, b, c, d;
    in >> a >> b >> c >> d;
    return flag128(a, b, c, d);
}

static void printf_float(float v) { std::printf("%a\n", static_cast<double>(v)); }

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd == "gen")
        {
            SpellInfo info;
            int32 pf0, pf1;
            uint32 attr3, neff;
            in >> info.Id >> pf0 >> pf1 >> info.ProcChance >> info.ProcCooldown >> info.ProcCharges
               >> info.ProcBasePPM >> info.SpellFamilyName >> attr3 >> neff;
            info.ProcFlags = ProcFlagsInit(ProcFlags(pf0), ProcFlags2(pf1));
            info.Attr[3] = attr3;
            for (uint32 i = 0; i < neff; ++i)
            {
                SpellEffectInfo e;
                in >> e.EffectIndex >> e.Effect >> e.ApplyAuraName >> e.TriggerSpell >> e.BasePointsInt;
                e.SpellClassMask = read128(in);
                info.Effects.push_back(e);
            }
            SpellMgr mgr;
            mgr.mSpellInfoMap.push_back(info);
            mgr.GenerateDefaultProcs();
            SpellProcEntry const* entry = mgr.GetSpellProcEntry(&mgr.mSpellInfoMap[0]);
            if (!entry)
            {
                std::printf("none\n");
                continue;
            }
            std::printf("%u %u %u %u %u %u %u %u %u %u %u %u %u %a %a %lld %u\n",
                entry->SchoolMask, entry->SpellFamilyName,
                entry->SpellFamilyMask[0], entry->SpellFamilyMask[1], entry->SpellFamilyMask[2], entry->SpellFamilyMask[3],
                uint32(entry->ProcFlags[0]), uint32(entry->ProcFlags[1]),
                uint32(entry->SpellTypeMask), uint32(entry->SpellPhaseMask), uint32(entry->HitMask),
                uint32(entry->AttributesMask), entry->DisableEffectsMask,
                static_cast<double>(entry->ProcsPerMinute), static_cast<double>(entry->Chance),
                static_cast<long long>(entry->Cooldown.count()), entry->Charges);
        }
        else if (cmd == "match")
        {
            SpellProcEntry entry;
            uint32 type, phase, hit, attrs;
            int32 pf0, pf1;
            in >> entry.SchoolMask >> entry.SpellFamilyName;
            entry.SpellFamilyMask = read128(in);
            in >> pf0 >> pf1 >> type >> phase >> hit >> attrs;
            entry.ProcFlags = ProcFlagsInit(ProcFlags(pf0), ProcFlags2(pf1));
            entry.SpellTypeMask = ProcFlagsSpellType(type);
            entry.SpellPhaseMask = ProcFlagsSpellPhase(phase);
            entry.HitMask = ProcFlagsHit(hit);
            entry.AttributesMask = ProcAttributes(attrs);

            int32 tm0, tm1;
            uint32 etype, ephase, ehit, eschool, hasSpell, costPositive, hasInfo, efamily, actorPlayer, hasTarget, honor;
            in >> tm0 >> tm1 >> etype >> ephase >> ehit >> eschool >> hasSpell >> costPositive >> hasInfo >> efamily;
            flag128 eflags = read128(in);
            in >> actorPlayer >> hasTarget >> honor;

            Player actorPlayerObj;
            Unit actorUnit;
            Unit target;
            actorPlayerObj.IsPlayerUnit = true;
            actorPlayerObj.HonorTarget = honor != 0;
            Spell spell;
            spell.Costs.push_back({ 0, costPositive ? 1 : 0 });
            SpellInfo eventInfoSpell;
            eventInfoSpell.SpellFamilyName = efamily;
            eventInfoSpell.SpellFamilyFlags = eflags;

            ProcEventInfo ev;
            ev._actor = actorPlayer ? static_cast<Unit*>(&actorPlayerObj) : &actorUnit;
            ev._actionTarget = hasTarget ? &target : nullptr;
            ev._typeMask = ProcFlagsInit(ProcFlags(tm0), ProcFlags2(tm1));
            ev._spellTypeMask = ProcFlagsSpellType(etype);
            ev._spellPhaseMask = ProcFlagsSpellPhase(ephase);
            ev._hitMask = ProcFlagsHit(ehit);
            ev._schoolMask = SpellSchoolMask(eschool);
            ev._spell = hasSpell ? &spell : nullptr;
            ev._spellInfo = hasInfo ? &eventInfoSpell : nullptr;
            std::printf("%d\n", SpellMgr::CanSpellTriggerProcOnEvent(entry, ev) ? 1 : 0);
        }
        else if (cmd == "ppm")
        {
            uint32 speed;
            float ppm;
            in >> speed >> ppm;
            Unit u;
            printf_float(u.GetPPMProcChance(speed, ppm, nullptr));
        }
        else if (cmd == "weapon")
        {
            Unit u;
            uint32 mainReady, mainSpeed, hasOff, offReady, offSpeed;
            in >> mainReady >> mainSpeed >> hasOff >> offReady >> offSpeed;
            u.AttackReady[BASE_ATTACK] = mainReady != 0;
            u.AttackTime[BASE_ATTACK] = mainSpeed;
            u.HasOffhand = hasOff != 0;
            u.AttackReady[OFF_ATTACK] = offReady != 0;
            u.AttackTime[OFF_ATTACK] = offSpeed;
            printf_float(u.GetWeaponProcChance());
        }
        else if (cmd == "rppm")
        {
            SpellInfo info;
            uint32 nmods;
            in >> info.ProcBasePPM >> nmods;
            std::vector<SpellProcsPerMinuteModEntry> mods(nmods);
            for (auto& m : mods)
                in >> m.Type >> m.Param >> m.Coeff;
            for (auto& m : mods)
                info.ProcPPMMods.push_back(&m);
            Player p;
            p.IsPlayerUnit = true;
            uint32 isPlayer, cls, spec, race, bg, naura, nrpp;
            int32 ilvl;
            in >> p.UnitData.ModHaste >> p.UnitData.ModRangedHaste >> p.UnitData.ModSpellHaste >> p.UnitData.ModHasteRegen
               >> isPlayer >> p.ActiveData.CritPercentage >> p.ActiveData.RangedCritPercentage >> p.ActiveData.SpellCritPercentage
               >> cls >> spec >> race >> ilvl >> bg >> naura;
            p.IsPlayerUnit = isPlayer != 0;
            p.Class = uint8(cls);
            p.Spec = ChrSpecialization(spec);
            p.Race = uint8(race);
            p.Map.Bg = bg != 0;
            for (uint32 i = 0; i < naura; ++i)
            {
                uint32 id;
                in >> id;
                p.Auras.insert(id);
            }
            in >> nrpp;
            g_randPropPoints.clear();
            for (uint32 i = 0; i < nrpp; ++i)
            {
                uint32 level;
                float points;
                in >> level >> points;
                g_randPropPoints[level] = points;
            }
            printf_float(info.CalcProcPPM(&p, ilvl));
        }
        else if (cmd == "chance")
        {
            SpellProcEntry entry;
            float entryChance, entryPpm;
            SpellInfo info;
            uint32 hasCaster, hasDamage, speed, attrs, level;
            long long attemptAgo, successAgo;
            in >> entryChance >> entryPpm >> info.ProcBasePPM >> hasCaster >> hasDamage >> speed >> attrs >> level
               >> attemptAgo >> successAgo;
            entry.Chance = entryChance;
            entry.ProcsPerMinute = entryPpm;
            entry.AttributesMask = ProcAttributes(attrs);
            Unit caster;
            caster.AttackTime[BASE_ATTACK] = speed;
            Unit actor;
            actor.Level = uint8(level);
            DamageInfo damage;
            ProcEventInfo ev;
            ev._actor = &actor;
            ev._damageInfo = hasDamage ? &damage : nullptr;
            Aura aura;
            aura.m_spellInfo = &info;
            aura.Caster = hasCaster ? &caster : nullptr;
            aura.m_lastProcAttemptTime = g_now - std::chrono::milliseconds(attemptAgo);
            aura.m_lastProcSuccessTime = g_now - std::chrono::milliseconds(successAgo);
            printf_float(aura.CalcProcChance(entry, ev));
        }
        else
        {
            std::printf("error unknown command %s\n", cmd.c_str());
        }
        std::fflush(stdout);
    }
    return 0;
}
