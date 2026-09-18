// Differential probe for the aura identity oracle (aura_lifecycle/identity.py, Track A).
//
// tc_identity_decls.inc / tc_identity_bodies.inc are pulled verbatim out of the sibling
// TrinityCore checkout by extract.py.  This file supplies only the scaffolding the bodies
// reference, with Trinity's member names.  BOUNDED probe: it answers
//   * SpellInfo::_LoadSpellSpecific for driver-described spells,
//   * Aura::CanStackWith for a (new, existing) pair,
//   * Unit::_TryStackingOrRefreshingExistingAura + Unit::GetOwnedAura against a driver-built
//     m_ownedAuras multimap ("same spell, same caster, different cast item" and
//     "different caster, stackable-on-one-slot" questions).
//
// Cut points (semantics stated, not extracted):
//   SpellEffectInfo::IsTargetingArea (SpellInfo.cpp:475) -> driver flag per effect (the
//       SpellImplicitTargetInfo::IsArea classification is covered by the targeting probes).
//   SpellInfo::GetFirstRankSpell -> driver-supplied first-rank id (SpellMgr rank chains).
//   SpellMgr::CheckSpellGroupStackRules -> driver table `rule <a> <b> <r>` (default DEFAULT).
//   Aura::HasEffectType / IsArea / GetEffectMask -> computed from the driver effect mask exactly as
//       SpellAuras.cpp:1290 / 1141 do over m_effects.
//   Vehicle kit -> none (GetVehicleKit() == nullptr); CONTROL_VEHICLE pairs therefore take the
//       "no kit -> stack" branch (SpellAuras.cpp:1743-1744).
//   Aura::ModStackAmount -> records its arguments (track C owns its semantics).
//
// Protocol (stdin, whitespace separated):
//   spell <id> <firstRank> <family> <ff0> <ff1> <ff2> <ff3> <stack> <dispel> <interrupt0> <cu>
//         <a0> ... <a16> <n> { <index> <effect> <aura> <trigger> <targetingArea> <attrs> } * n
//   specific <id>                                   -> {"specific": N}
//   rule <a> <b> <r>                                (CheckSpellGroupStackRules result for first ranks a,b)
//   canstack <newId> <newCaster> <newItem> <newDyn> <newMask> <exId> <exCaster> <exItem> <exDyn> <exMask>
//                                                   -> {"stack": b}
//   aura <label> <spell> <caster> <item> <mask>     (append to the owner's m_ownedAuras)
//   create <spell> <caster> <item> <mask> <stack>   -> {"found": label|null, "caster": g, "item": g, "mod": [...]}
//   reset                                           (clear owned auras)

#include "Define.h"
#include "FlagsArray.h"

#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <typeinfo>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#define ABORT() std::abort()
#define ASSERT(cond, ...) do { if (!(cond)) std::abort(); } while (0)
#define ASSERT_NODEBUGINFO(cond) ASSERT(cond)

using SpellEffectValue = double;   // SpellDefines.h:490

#include "tc_identity_decls.inc"

template <typename T>
struct EnumFlag   // EnumFlag.h (only HasFlag is read by the bodies)
{
    T value{};
    constexpr bool HasFlag(T f) const { return (static_cast<uint32>(value) & static_cast<uint32>(f)) != 0; }
};

class ObjectGuid   // ObjectGuid.h (identity semantics only)
{
public:
    static ObjectGuid const Empty;
    ObjectGuid() = default;
    explicit ObjectGuid(uint64 v) : _v(v) { }
    bool IsEmpty() const { return _v == 0; }
    bool operator!() const { return IsEmpty(); }
    bool operator==(ObjectGuid const& o) const { return _v == o._v; }
    bool operator!=(ObjectGuid const& o) const { return _v != o._v; }
    uint64 raw() const { return _v; }
private:
    uint64 _v = 0;
};
ObjectGuid const ObjectGuid::Empty = ObjectGuid();

enum AuraObjectType { UNIT_AURA_TYPE, DYNOBJ_AURA_TYPE };   // SpellAuraDefines.h
enum AuraRemoveMode { AURA_REMOVE_NONE = 0, AURA_REMOVE_BY_DEFAULT = 1 };

class SpellInfo;

class SpellEffectInfo
{
public:
    uint32 EffectIndex = 0;
    SpellEffects Effect = SpellEffects(0);
    AuraType ApplyAuraName = AuraType(0);
    uint32 TriggerSpell = 0;
    SpellEffectValue BasePoints = 0;
    EnumFlag<SpellEffectAttributes> EffectAttributes;
    bool targetingArea = false;

    bool IsEffect() const;
    bool IsEffect(SpellEffects effectName) const;
    bool IsAura() const;
    bool IsAura(AuraType aura) const;
    bool IsAreaAuraEffect() const;
    bool IsUnitOwnedAuraEffect() const;
    bool IsTargetingArea() const { return targetingArea; }   // cut point
};

std::map<uint32, std::unique_ptr<SpellInfo>> g_spells;

class SpellInfo
{
public:
    uint32 Id = 0;
    uint32 firstRank = 0;
    uint32 StackAmount = 0;
    uint32 SpellFamilyName = 0;
    flag128 SpellFamilyFlags;
    uint32 Dispel = 0;
    uint32 AttributesCu = 0;
    uint32 attrs[17] = {};
    uint32 interrupt0 = 0;
    SpellSpecificType _spellSpecific = SPELL_SPECIFIC_NORMAL;
    std::vector<SpellEffectInfo> _effects;

#define HAS_ATTR(N) bool HasAttribute(SpellAttr##N a) const { return (attrs[N] & a) != 0; }
    HAS_ATTR(0) HAS_ATTR(1) HAS_ATTR(2) HAS_ATTR(3) HAS_ATTR(4) HAS_ATTR(5) HAS_ATTR(6) HAS_ATTR(7) HAS_ATTR(8)
    HAS_ATTR(9) HAS_ATTR(10) HAS_ATTR(11) HAS_ATTR(12) HAS_ATTR(13) HAS_ATTR(14) HAS_ATTR(15) HAS_ATTR(16)
#undef HAS_ATTR
    bool HasAttribute(SpellCustomAttributes a) const { return (AttributesCu & a) != 0; }
    bool HasAuraInterruptFlag(SpellAuraInterruptFlags f) const { return (interrupt0 & static_cast<uint32>(f)) != 0; }

    std::vector<SpellEffectInfo> const& GetEffects() const { return _effects; }
    SpellEffectInfo const& GetEffect(SpellEffIndex i) const { return _effects.at(i); }
    SpellInfo const* GetFirstRankSpell() const
    {
        auto it = g_spells.find(firstRank);
        return it != g_spells.end() ? it->second.get() : this;
    }
    SpellSpecificType GetSpellSpecific() const { return _spellSpecific; }

    bool IsPassive() const;
    bool IsMultiSlotAura() const;
    bool IsStackableOnOneSlotWithDifferentCasters() const;
    bool IsChanneled() const;
    bool IsAuraExclusiveBySpecificWith(SpellInfo const* spellInfo) const;
    bool IsAuraExclusiveBySpecificPerCasterWith(SpellInfo const* spellInfo) const;
    void _LoadSpellSpecific();
    bool IsRankOf(SpellInfo const* spellInfo) const;
    bool IsDifferentRankOf(SpellInfo const* spellInfo) const;
};

struct SpellMgrStub
{
    std::map<std::pair<uint32, uint32>, int> rules;
    SpellGroupStackRule CheckSpellGroupStackRules(SpellInfo const* a, SpellInfo const* b) const
    {
        auto it = rules.find({ a->GetFirstRankSpell()->Id, b->GetFirstRankSpell()->Id });
        return it == rules.end() ? SPELL_GROUP_STACK_RULE_DEFAULT : SpellGroupStackRule(it->second);
    }
} g_mgr;
SpellMgrStub* const sSpellMgr = &g_mgr;

class Vehicle { public: uint8 GetAvailableSeatCount() const { return 0; } };
class Unit;
class WorldObject { public: Unit* ToUnit(); };

class AuraEffect
{
public:
    SpellEffectValue m_baseAmount = 0;
};

class Aura
{
public:
    std::string label;
    SpellInfo const* m_spellInfo = nullptr;
    ObjectGuid m_casterGuid;
    ObjectGuid m_castItemGuid;
    uint32 m_castItemId = 0;
    int32 m_castItemLevel = -1;
    uint32 effMask = 0;
    bool dyn = false;
    bool removed = false;
    WorldObject* owner = nullptr;
    std::vector<std::unique_ptr<AuraEffect>> effects;
    std::vector<std::string> mods;

    uint32 GetId() const { return m_spellInfo->Id; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    ObjectGuid const& GetCasterGUID() const { return m_casterGuid; }
    ObjectGuid const& GetCastItemGUID() const { return m_castItemGuid; }
    AuraObjectType GetType() const { return dyn ? DYNOBJ_AURA_TYPE : UNIT_AURA_TYPE; }
    bool IsPassive() const { return m_spellInfo->IsPassive(); }
    bool IsRemoved() const { return removed; }
    uint32 GetEffectMask() const { return effMask; }
    WorldObject* GetOwner() const { return owner; }
    AuraEffect* GetEffect(uint32 index) const { return index < effects.size() ? effects[index].get() : nullptr; }
    bool HasEffectType(AuraType type) const
    {
        for (SpellEffectInfo const& e : m_spellInfo->GetEffects())
            if ((effMask & (1u << e.EffectIndex)) && e.ApplyAuraName == type)
                return true;
        return false;
    }
    bool IsArea() const
    {
        for (SpellEffectInfo const& e : m_spellInfo->GetEffects())
            if ((effMask & (1u << e.EffectIndex)) && e.IsAreaAuraEffect())
                return true;
        return false;
    }
    bool ModStackAmount(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT, bool resetPeriodicTimer = true)
    {
        mods.push_back("ModStackAmount(" + std::to_string(num) + "," + std::to_string(int(removeMode)) + "," + std::to_string(int(resetPeriodicTimer)) + ")");
        return false;
    }
    bool CanStackWith(Aura const* existingAura) const;
};

struct AuraCreateInfo   // SpellAuras.h:106 (the members the bodies read)
{
    SpellInfo const* _spellInfo = nullptr;
    uint32 _auraEffectMask = 0;
    ObjectGuid CasterGUID;
    Unit* Caster = nullptr;
    SpellEffectValue const* BaseAmount = nullptr;
    ObjectGuid CastItemGUID;
    uint32 CastItemId = 0;
    int32 CastItemLevel = -1;
    int32 StackAmount = 1;
    bool ResetPeriodicTimer = true;
    SpellInfo const* GetSpellInfo() const { return _spellInfo; }
    uint32 GetAuraEffectMask() const { return _auraEffectMask; }
};

class Unit : public WorldObject
{
public:
    typedef std::multimap<uint32, Aura*> AuraMap;
    typedef std::pair<AuraMap::const_iterator, AuraMap::const_iterator> AuraMapBounds;
    ObjectGuid guid;
    AuraMap m_ownedAuras;
    ObjectGuid GetGUID() const { return guid; }
    Vehicle* GetVehicleKit() const { return nullptr; }
    Aura* _TryStackingOrRefreshingExistingAura(AuraCreateInfo& createInfo);
    Aura* GetOwnedAura(uint32 spellId, ObjectGuid casterGUID = ObjectGuid::Empty, ObjectGuid itemCasterGUID = ObjectGuid::Empty, uint32 reqEffMask = 0, Aura* except = nullptr) const;
};
Unit* WorldObject::ToUnit() { return static_cast<Unit*>(this); }

#include "tc_identity_bodies.inc"

// ---------------------------------------------------------------------------
// driver
// ---------------------------------------------------------------------------

static Unit g_owner;
static std::vector<std::unique_ptr<Aura>> g_auras;
static std::vector<std::unique_ptr<Unit>> g_casters;

static Unit* casterUnit(uint64 id)
{
    for (auto& u : g_casters)
        if (u->guid.raw() == id)
            return u.get();
    g_casters.push_back(std::make_unique<Unit>());
    g_casters.back()->guid = ObjectGuid(id);
    return g_casters.back().get();
}

static SpellInfo* spellById(uint32 id)
{
    auto it = g_spells.find(id);
    if (it == g_spells.end())
    {
        std::fprintf(stderr, "unknown spell %u\n", id);
        std::exit(2);
    }
    return it->second.get();
}

static std::unique_ptr<Aura> makeAura(std::string label, uint32 spell, uint64 caster, uint64 item, int dyn, uint32 mask)
{
    auto a = std::make_unique<Aura>();
    a->label = std::move(label);
    a->m_spellInfo = spellById(spell);
    a->m_casterGuid = ObjectGuid(caster);
    a->m_castItemGuid = ObjectGuid(item);
    a->dyn = dyn != 0;
    a->effMask = mask;
    a->owner = &g_owner;
    for (SpellEffectInfo const& e : a->m_spellInfo->GetEffects())
    {
        if (a->effects.size() <= e.EffectIndex)
            a->effects.resize(e.EffectIndex + 1);
        if (mask & (1u << e.EffectIndex))
            a->effects[e.EffectIndex] = std::make_unique<AuraEffect>();
    }
    return a;
}

int main()
{
    g_owner.guid = ObjectGuid(1);
    std::string cmd;
    while (std::cin >> cmd)
    {
        if (cmd == "spell")
        {
            auto s = std::make_unique<SpellInfo>();
            uint32 ff[4];
            std::cin >> s->Id >> s->firstRank >> s->SpellFamilyName >> ff[0] >> ff[1] >> ff[2] >> ff[3] >> s->StackAmount
                     >> s->Dispel >> s->interrupt0 >> s->AttributesCu;
            s->SpellFamilyFlags = flag128(ff[0], ff[1], ff[2], ff[3]);
            for (uint32& a : s->attrs)
                std::cin >> a;
            size_t n;
            std::cin >> n;
            for (size_t i = 0; i < n; ++i)
            {
                SpellEffectInfo e;
                uint32 effect, aura, attrs;
                int area;
                std::cin >> e.EffectIndex >> effect >> aura >> e.TriggerSpell >> area >> attrs;
                e.Effect = SpellEffects(effect);
                e.ApplyAuraName = AuraType(aura);
                e.targetingArea = area != 0;
                e.EffectAttributes.value = SpellEffectAttributes(attrs);
                // SpellInfo::_effects is indexed by EffectIndex; absent rows are empty SpellEffectInfo
                while (s->_effects.size() <= e.EffectIndex)
                {
                    SpellEffectInfo empty;
                    empty.EffectIndex = uint32(s->_effects.size());
                    s->_effects.push_back(empty);
                }
                s->_effects[e.EffectIndex] = e;
            }
            uint32 id = s->Id;
            g_spells[id] = std::move(s);
        }
        else if (cmd == "specific")
        {
            uint32 id;
            std::cin >> id;
            SpellInfo* s = spellById(id);
            s->_LoadSpellSpecific();
            std::printf("{\"spell\": %u, \"specific\": %d}\n", id, int(s->GetSpellSpecific()));
        }
        else if (cmd == "rule")
        {
            uint32 a, b;
            int r;
            std::cin >> a >> b >> r;
            g_mgr.rules[{ a, b }] = r;
        }
        else if (cmd == "canstack")
        {
            uint32 ns, es, nm, em;
            uint64 nc, ni, ec, ei;
            int nd, ed;
            std::cin >> ns >> nc >> ni >> nd >> nm >> es >> ec >> ei >> ed >> em;
            spellById(ns)->_LoadSpellSpecific();
            spellById(es)->_LoadSpellSpecific();
            auto n = makeAura("new", ns, nc, ni, nd, nm);
            auto e = makeAura("existing", es, ec, ei, ed, em);
            std::printf("{\"stack\": %s}\n", n->CanStackWith(e.get()) ? "true" : "false");
        }
        else if (cmd == "aura")
        {
            std::string label;
            uint32 spell, mask;
            uint64 caster, item;
            std::cin >> label >> spell >> caster >> item >> mask;
            g_auras.push_back(makeAura(label, spell, caster, item, 0, mask));
            g_owner.m_ownedAuras.emplace(spell, g_auras.back().get());
        }
        else if (cmd == "create")
        {
            uint32 spell, mask;
            uint64 caster, item;
            int32 stack;
            std::cin >> spell >> caster >> item >> mask >> stack;
            AuraCreateInfo ci;
            ci._spellInfo = spellById(spell);
            ci._auraEffectMask = mask;
            ci.CasterGUID = ObjectGuid(caster);
            ci.Caster = casterUnit(caster);
            ci.CastItemGUID = ObjectGuid(item);
            ci.CastItemId = uint32(item);
            ci.StackAmount = stack;
            Aura* found = g_owner._TryStackingOrRefreshingExistingAura(ci);
            if (!found)
                std::printf("{\"found\": null}\n");
            else
            {
                std::printf("{\"found\": \"%s\", \"caster\": %llu, \"item\": %llu, \"mod\": [", found->label.c_str(),
                            (unsigned long long)found->m_casterGuid.raw(), (unsigned long long)found->m_castItemGuid.raw());
                for (size_t i = 0; i < found->mods.size(); ++i)
                    std::printf("%s\"%s\"", i ? ", " : "", found->mods[i].c_str());
                std::printf("]}\n");
                found->mods.clear();
            }
        }
        else if (cmd == "reset")
        {
            g_owner.m_ownedAuras.clear();
            g_auras.clear();
            g_mgr.rules.clear();
        }
        else
        {
            std::fprintf(stderr, "unknown command %s\n", cmd.c_str());
            return 2;
        }
        std::fflush(stdout);
    }
    return 0;
}
