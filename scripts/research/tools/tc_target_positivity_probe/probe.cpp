// Load-time spell positivity differential probe (track G, targeting/positivity.py).
//
// tc_positivity_{decls,bodies}.inc are Trinity's own text (extract.py).  This file supplies
// the minimal SpellInfo / SpellEffectInfo / SpellMgr stubs those bodies read.
//
// Cut points:
//   SpellImplicitTargetInfo      -> driver target id + check type (SpellInfo.cpp:83-120 not needed)
//   SpellEffectInfo::CalcValue() -> driver load-time value (Python Loader.calc_value)
//   sSpellMgr->GetSpellInfo      -> driver spell set (DIFFICULTY_NONE only)
//   mSpellInfoMap iteration      -> driver processing order (SpellMgr.cpp:3031 hashed container)
//
// stdin (one world at a time):
//   S id family flags0 mechanic attr0 attr1 attr4 seedmask nEffects
//   E index effect aura target_a check_a target_b check_b attributes rppl(%a) misc trigger value(%a)
//      (exactly nEffects E lines follow each S line; effect 0 = blank slot)
//   O id id ...      processing order (every spell once)
//   G                run: reset NegativeEffects to seeds, _InitializeSpellPositivity in order,
//                    print {"order": n, "neg": {"id": mask, ...}}; worlds persist until X
//   X                clear the world

#include <array>
#include <bitset>
#include <cinttypes>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

typedef int8_t int8;
typedef int16_t int16;
typedef int32_t int32;
typedef int64_t int64;
typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef uint64_t uint64;
#define TC_GAME_API
#define MAX_SPELL_EFFECTS 32

#include "Utilities/EnumFlag.h"
#include "Utilities/Hash.h"

#include "tc_positivity_decls.inc"

using SpellEffectValue = double;   // SpellDefines.h:490
enum Difficulty : uint8 { DIFFICULTY_NONE = 0 };

class SpellInfo;

class SpellImplicitTargetInfo
{
public:
    uint32 Target = 0;
    SpellTargetCheckTypes Check = TARGET_CHECK_DEFAULT;
    uint32 GetTarget() const { return Target; }
    SpellTargetCheckTypes GetCheckType() const { return Check; }
};

class SpellEffectInfo
{
public:
    SpellInfo const* _spellInfo = nullptr;
    SpellEffIndex EffectIndex = EFFECT_0;
    SpellEffects Effect = SPELL_EFFECT_NONE;
    AuraType ApplyAuraName = SPELL_AURA_NONE;
    int32 MiscValue = 0;
    float RealPointsPerLevel = 0.0f;
    uint32 TriggerSpell = 0;
    SpellImplicitTargetInfo TargetA;
    SpellImplicitTargetInfo TargetB;
    EnumFlag<SpellEffectAttributes> EffectAttributes = SpellEffectAttributes::None;
    double DriverValue = 0.0;

    bool IsEffect() const;
    bool IsAura() const;
    bool IsAreaAuraEffect() const;
    bool IsUnitOwnedAuraEffect() const;
    SpellEffectValue CalcValue() const { return DriverValue; }
};

struct Flag128Stub
{
    uint32 w[4] = { 0, 0, 0, 0 };
    uint32 operator[](int i) const { return w[i]; }
};

class SpellInfo
{
public:
    uint32 Id = 0;
    ::Difficulty Difficulty = DIFFICULTY_NONE;   // SpellInfo.h: Difficulty const Difficulty
    uint32 SpellFamilyName = 0;
    Flag128Stub SpellFamilyFlags;
    uint32 Mechanic = 0;
    uint32 Attributes = 0;
    uint32 AttributesEx = 0;
    uint32 AttributesEx4 = 0;
    std::bitset<MAX_SPELL_EFFECTS> NegativeEffects;
    std::vector<SpellEffectInfo> _effects;

    bool HasAttribute(SpellAttr0 attribute) const { return !!(Attributes & attribute); }
    bool HasAttribute(SpellAttr1 attribute) const { return !!(AttributesEx & attribute); }
    bool HasAttribute(SpellAttr4 attribute) const { return !!(AttributesEx4 & attribute); }
    std::vector<SpellEffectInfo> const& GetEffects() const { return _effects; }
    SpellEffectInfo const& GetEffect(SpellEffIndex index) const { return _effects[index]; }
    bool IsPassive() const;
    bool IsPositive() const;
    bool IsPositiveEffect(uint8 effIndex) const;
    void _InitializeSpellPositivity();
};

class SpellMgrStub
{
public:
    std::map<uint32, SpellInfo> spells;
    SpellInfo const* GetSpellInfo(uint32 id, ::Difficulty) const
    {
        auto itr = spells.find(id);
        return itr == spells.end() ? nullptr : &itr->second;
    }
};
static SpellMgrStub g_mgr;
static SpellMgrStub* sSpellMgr = &g_mgr;

#include "tc_positivity_bodies.inc"

int main()
{
    std::string line;
    std::map<uint32, uint32> seeds;
    std::vector<uint32> order;
    int runs = 0;
    SpellInfo* current = nullptr;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string cmd;
        if (!(in >> cmd))
            continue;
        if (cmd == "S")
        {
            uint32 id, family, flags0, mechanic, a0, a1, a4, seed, n;
            in >> id >> family >> flags0 >> mechanic >> a0 >> a1 >> a4 >> seed >> n;
            SpellInfo& s = g_mgr.spells[id];
            s = SpellInfo();
            s.Id = id;
            s.SpellFamilyName = family;
            s.SpellFamilyFlags.w[0] = flags0;
            s.Mechanic = mechanic;
            s.Attributes = a0;
            s.AttributesEx = a1;
            s.AttributesEx4 = a4;
            s._effects.resize(n);
            seeds[id] = seed;
            current = &s;
        }
        else if (cmd == "E")
        {
            uint32 idx, effect, aura, ta, ca, tb, cb, attrs, trigger;
            int32 misc;
            std::string rppl, value;
            in >> idx >> effect >> aura >> ta >> ca >> tb >> cb >> attrs >> rppl >> misc >> trigger >> value;
            SpellEffectInfo& e = current->_effects.at(idx);
            e._spellInfo = current;
            e.EffectIndex = SpellEffIndex(idx);
            e.Effect = SpellEffects(effect);
            e.ApplyAuraName = AuraType(aura);
            e.TargetA.Target = ta;
            e.TargetA.Check = SpellTargetCheckTypes(ca);
            e.TargetB.Target = tb;
            e.TargetB.Check = SpellTargetCheckTypes(cb);
            e.EffectAttributes = SpellEffectAttributes(attrs);
            e.RealPointsPerLevel = std::strtof(rppl.c_str(), nullptr);
            e.MiscValue = misc;
            e.TriggerSpell = trigger;
            e.DriverValue = std::strtod(value.c_str(), nullptr);
        }
        else if (cmd == "O")
        {
            order.clear();
            uint32 id;
            while (in >> id)
                order.push_back(id);
        }
        else if (cmd == "G")
        {
            for (auto& [id, s] : g_mgr.spells)
                s.NegativeEffects = std::bitset<MAX_SPELL_EFFECTS>(seeds[id]);
            for (uint32 id : order)
                g_mgr.spells.at(id)._InitializeSpellPositivity();
            std::printf("{\"run\":%d,\"neg\":{", runs++);
            bool first = true;
            for (auto& [id, s] : g_mgr.spells)
            {
                std::printf("%s\"%u\":%lu", first ? "" : ",", id, s.NegativeEffects.to_ulong());
                first = false;
            }
            std::printf("}}\n");
            std::fflush(stdout);
        }
        else if (cmd == "X")
        {
            g_mgr.spells.clear();
            seeds.clear();
            order.clear();
            current = nullptr;
        }
    }
    return 0;
}
