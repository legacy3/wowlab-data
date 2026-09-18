// Differential probe for the periodic-timer oracle (aura_lifecycle/periodic.py, Track D).
//
// tc_periodic_decls.inc / tc_periodic_bodies.inc are pulled verbatim out of the sibling
// TrinityCore checkout by extract.py.  This file supplies only the scaffolding the bodies
// reference, with Trinity's member names.  BOUNDED probe: the cut points below replace engine
// state with driver-supplied values.
//
// Cut points (semantics stated, not extracted):
//   Aura ctor / AuraEffect ctor   create: m_maxDuration = m_duration = CalcMaxDuration(), m_stackAmount = 1,
//                                 m_updateTargetMapInterval = 0 (SpellAuras.cpp:432-458), then per effect
//                                 CalculatePeriodic(caster, true, false) (SpellAuraEffects.cpp:742); amounts not computed.
//   Aura::CalcMaxDuration()       -> SpellInfo duration (caster duration mods = Track B).
//   GetSpellModOwner()            -> nullptr (no spell modifiers of any op).
//   UpdateTargetMap               -> resets m_updateTargetMapInterval to 500 (Track F owns the map).
//   SetStackAmount                -> stores m_stackAmount (amount recalculation = snapshot.py / Track C).
//   SetCharges / CalcMaxCharges   -> 0; Remove / RemoveOwnedAura -> mark + erase; script hooks -> none.
//   PeriodicTick                  -> logs {spell, effect, tick} (tick handlers are not run).
//   Owner == caster (self aura); ObjectAccessor::GetWorldObject -> owner/caster present.
//
// Protocol (stdin, whitespace separated):
//   spell <id> <attr0> <attr1> <attr2> <attr3> <attr5> <attr8> <attr13> <stackAmount> <duration> <family>
//   effect <spellId> <auraType> <period>          (effects in index order)
//   caster <castSpeed> <haste>
//   create <spellId>                               -> {"event":"create",...state}
//   reapply <spellId> <triggerFlags>               -> {"event":"reapply","reset":b,...state}
//   modstack <spellId> <num> <reset> <createIfAbsent>  -> ModStackAmount(num, DEFAULT, reset) (AddAura / effect 289 paths;
//                                                     no Spell.cpp duration override); absent aura: create without override
//                                                     when createIfAbsent (AddAura), else nothing (effect 289)
//   setdur <spellId> <delta>                       -> SetDuration(GetDuration() + delta) (extracted SetDuration)
//   setmax <spellId> <delta>                       -> SetMaxDuration(GetDuration() + delta) (SpellAuras.h:218, inline)
//   remove <spellId>
//   update <diff>                                  -> {"event":"update","ticks":[...],"removed":[...],"auras":[...]}

#include "Define.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#define ASSERT(cond, ...) do { if (!(cond)) std::abort(); } while (0)

#include "tc_periodic_decls.inc"

class Player;
class Spell;
class Aura;
class AuraEffect;
class Unit;
typedef Aura UnitAura;

struct ObjectGuid
{
    uint64 v = 0;
    bool operator==(ObjectGuid const&) const = default;
    bool IsEmpty() const { return v == 0; }
};

struct SpellEffectInfo { uint32 EffectIndex = 0; AuraType ApplyAuraName = SPELL_AURA_NONE; int32 ApplyAuraPeriod = 0; };

struct SpellInfo
{
    uint32 Id = 0;
    uint32 Attributes = 0, AttributesEx = 0, AttributesEx2 = 0, AttributesEx3 = 0, AttributesEx5 = 0, AttributesEx8 = 0, AttributesEx13 = 0;
    uint32 StackAmount = 0;
    int32 Duration = 0;
    uint32 SpellFamilyName = 1;
    std::vector<SpellEffectInfo> Effects;

    bool HasAttribute(SpellAttr0 a) const { return Attributes & a; }
    bool HasAttribute(SpellAttr1 a) const { return AttributesEx & a; }
    bool HasAttribute(SpellAttr2 a) const { return AttributesEx2 & a; }
    bool HasAttribute(SpellAttr3 a) const { return AttributesEx3 & a; }
    bool HasAttribute(SpellAttr5 a) const { return AttributesEx5 & a; }
    bool HasAttribute(SpellAttr8 a) const { return AttributesEx8 & a; }
    bool HasAttribute(SpellAttr13 a) const { return AttributesEx13 & a; }
    int32 GetDuration() const { return Duration; }        // cut: DurationEntry
    int32 GetMaxDuration() const { return Duration; }
    bool IsPassive() const;
    bool IsChanneled() const;
};

struct SpellPowerEntry { int32 RequiredAuraSpellID = 0; int32 ManaPerSecond = 0; int8 PowerType = 0; float PowerPctPerSecond = 0.0f; };
struct UnitData { float ModCastingSpeed = 1.0f; float ModHaste = 1.0f; };

class WorldObject
{
public:
    virtual ~WorldObject() = default;
    TypeID m_typeId = TYPEID_PLAYER;
    ObjectGuid m_guid{1};
    Player* GetSpellModOwner() const { return nullptr; }     // cut: no spell modifiers
    Unit const* ToUnit() const;
    Unit* ToUnit();
    TypeID GetTypeId() const { return m_typeId; }
    ObjectGuid GetGUID() const { return m_guid; }
    void ModSpellDurationTime(SpellInfo const* spellInfo, int32& duration, Spell* spell = nullptr) const;
};

typedef std::multimap<uint32, Aura*> AuraMap;
struct AuraApplication;

class Unit : public WorldObject
{
public:
    UnitData data;
    UnitData* m_unitData = &data;
    float m_modAttackSpeedPct[MAX_ATTACK] = { 1.0f, 1.0f, 1.0f };
    AuraMap m_ownedAuras;
    AuraMap::iterator m_auraUpdateIterator;
    std::vector<std::string> removals;

    bool HasAura(uint32) const { return false; }
    int32 GetMaxPower(Powers) const { return 0; }
    int32 GetPower(Powers) const { return 0; }
    int32 ModifyPower(Powers, int32) { return 0; }
    uint64 GetMaxHealth() const { return 0; }
    uint64 GetHealth() const { return 0; }
    int32 ModifyHealth(int64) { return 0; }
    void RemoveOwnedAura(AuraMap::iterator& i, AuraRemoveMode mode);
    void ProbeUpdateOwnedAuras(uint32 time);
};

Unit const* WorldObject::ToUnit() const { return static_cast<Unit const*>(this); }
Unit* WorldObject::ToUnit() { return static_cast<Unit*>(this); }

class Player : public Unit
{
public:
    template <class T> void ApplySpellMod(SpellInfo const*, SpellModOp, T&, Spell* = nullptr) const { }
    Spell* FindCurrentSpellBySpellId(uint32) const { return nullptr; }
    void SetSpellModTakingSpell(Spell*, bool) { }
};

namespace ObjectAccessor
{
    WorldObject* GetWorldObject(WorldObject const& u, ObjectGuid) { return const_cast<WorldObject*>(&u); }
}

struct AuraApplication { Unit* target; Unit* GetTarget() const { return target; } };

struct TickLog { uint32 spell; uint32 effect; uint32 tick; };
static std::vector<TickLog> g_ticks;

class AuraEffect
{
public:
    AuraEffect(Aura* base, SpellInfo const* info, SpellEffectInfo const& eff) : m_base(base), m_spellInfo(info), m_effectInfo(eff) { }
    Aura* GetBase() const { return m_base; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    SpellEffectInfo const& GetSpellEffectInfo() const { return m_effectInfo; }
    AuraType GetAuraType() const { return m_effectInfo.ApplyAuraName; }
    uint32 GetEffIndex() const { return m_effectInfo.EffectIndex; }
    int32 GetPeriod() const { return _period; }
    uint32 GetTotalTicks() const;
    void ResetPeriodic(bool resetPeriodicTimer = false);
    void CalculatePeriodic(Unit* caster, bool resetPeriodicTimer = true, bool load = false);
    void Update(uint32 diff, Unit* caster);
    void ResetTicks() { _ticksDone = 0; }                         // SpellAuraEffects.h:89 (copied)
    template <typename Container> void GetApplicationList(Container& c) const;
    void PeriodicTick(AuraApplication*, Unit*) const { g_ticks.push_back({ m_spellInfo->Id, m_effectInfo.EffectIndex, _ticksDone }); }

    Aura* const m_base;
    SpellInfo const* const m_spellInfo;
    SpellEffectInfo const& m_effectInfo;
    int32 _periodicTimer = 0;
    int32 _period = 0;
    uint32 _ticksDone = 0;
    bool m_isPeriodic = false;
};

struct DropEvent;

class Aura
{
public:
    Aura(SpellInfo const* info, Unit* owner) : m_spellInfo(info), m_owner(owner) { app.target = owner; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    uint32 GetId() const { return m_spellInfo->Id; }
    WorldObject* GetOwner() const { return m_owner; }
    Unit* GetCaster() const { return m_owner; }                   // self aura
    ObjectGuid GetCasterGUID() const { return m_owner->GetGUID(); }
    std::vector<AuraEffect*> const& GetAuraEffects() const { return m_effects; }
    int32 GetMaxDuration() const { return m_maxDuration; }
    void SetMaxDuration(int32 duration) { m_maxDuration = duration; }
    int32 GetDuration() const { return m_duration; }
    void SetDuration(int32 duration, bool withMods = false);
    void RefreshDuration(bool withMods = false);
    void RefreshTimers(bool resetPeriodicTimer);
    bool IsPermanent() const { return GetMaxDuration() == -1; }        // SpellAuras.h:227 (copied)
    bool IsExpired() const { return !GetDuration() && !m_dropEvent; }  // SpellAuras.h:226 (copied)
    bool IsPassive() const;
    uint8 GetStackAmount() const { return m_stackAmount; }
    void SetStackAmount(uint8 stackAmount) { m_stackAmount = stackAmount; }   // cut
    uint32 CalcMaxStackAmount() const;
    bool ModStackAmount(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT, bool resetPeriodicTimer = true);
    int32 CalcMaxDuration() const { return m_spellInfo->GetDuration(); }     // cut
    uint8 CalcMaxCharges() const { return 0; }
    void SetCharges(uint8) { }
    void SetNeedClientUpdateForTargets() const { }
    void Remove(AuraRemoveMode = AURA_REMOVE_BY_DEFAULT) { m_isRemoved = true; }
    bool IsRemoved() const { return m_isRemoved; }
    void UpdateTargetMap(Unit*, bool = true) { m_updateTargetMapInterval = 500; }   // cut (Track F)
    void _DeleteRemovedApplications() { }
    void CallScriptEffectCalcPeriodicHandlers(AuraEffect const*, bool&, int32&) { }
    void CallScriptEffectUpdatePeriodicHandlers(AuraEffect*) { }
    void UpdateOwner(uint32 diff, WorldObject* owner);
    void Update(uint32 diff, Unit* caster);

    SpellInfo const* const m_spellInfo;
    Unit* const m_owner;
    AuraApplication app;
    std::vector<AuraEffect*> m_effects;
    std::vector<SpellPowerEntry const*> m_periodicCosts;
    int32 m_maxDuration = 0;
    int32 m_duration = 0;
    int32 m_timeCla = 0;
    int32 m_updateTargetMapInterval = 0;
    uint8 m_stackAmount = 1;
    bool m_isRemoved = false;
    DropEvent* m_dropEvent = nullptr;
};

template <typename Container>
void AuraEffect::GetApplicationList(Container& c) const { c.push_back(&m_base->app); }

struct SpellValue { float DurationMul = 1.0f; };
struct TargetInfo { int32 AuraDuration = 0; UnitAura* HitAura = nullptr; };

class Spell
{
public:
    SpellInfo const* m_spellInfo = nullptr;
    SpellValue value;
    SpellValue* m_spellValue = &value;
    Unit* m_originalCaster = nullptr;
    uint32 _triggeredCastFlags = 0;
    bool ProbeResetPeriodicTimer() const;
    void ProbeHitDuration(WorldObject* caster, TargetInfo& hitInfo, bool refresh);
};

void Unit::RemoveOwnedAura(AuraMap::iterator& i, AuraRemoveMode mode)
{
    std::ostringstream s;
    s << "{\"spell\":" << i->second->GetId() << ",\"mode\":" << int(mode) << "}";
    removals.push_back(s.str());
    i->second->m_isRemoved = true;
    i = m_ownedAuras.erase(i);
}

#include "tc_periodic_bodies.inc"

// ---------------------------------------------------------------------------- driver
static std::map<uint32, std::unique_ptr<SpellInfo>> g_spells;
static std::vector<std::unique_ptr<Aura>> g_auras;
static std::vector<std::unique_ptr<AuraEffect>> g_effects;

static Aura* FindAura(Unit& owner, uint32 id)
{
    auto it = owner.m_ownedAuras.find(id);
    return it == owner.m_ownedAuras.end() ? nullptr : it->second;
}

static void PrintAura(std::ostream& o, Aura const* a)
{
    o << "{\"spell\":" << a->GetId() << ",\"duration\":" << a->m_duration << ",\"max_duration\":" << a->m_maxDuration
      << ",\"stack\":" << int(a->m_stackAmount) << ",\"effects\":[";
    for (std::size_t i = 0; i < a->m_effects.size(); ++i)
    {
        AuraEffect const* e = a->m_effects[i];
        o << (i ? "," : "") << "{\"period\":" << e->_period << ",\"timer\":" << e->_periodicTimer << ",\"ticks_done\":"
          << e->_ticksDone << ",\"total_ticks\":" << e->GetTotalTicks() << ",\"is_periodic\":" << (e->m_isPeriodic ? "true" : "false") << "}";
    }
    o << "]}";
}

static void RunHitDuration(Unit& owner, Aura* aura, uint32 triggerFlags, bool refresh)
{
    Spell spell;
    spell.m_spellInfo = aura->GetSpellInfo();
    spell.m_originalCaster = &owner;
    spell._triggeredCastFlags = triggerFlags;
    TargetInfo hit;
    hit.AuraDuration = aura->GetSpellInfo()->GetDuration();   // Aura::CalcMaxDuration at PreprocessSpellHit (cut: no mods)
    hit.HitAura = aura;
    spell.ProbeHitDuration(&owner, hit, refresh);
}

static void CreateAura(Unit& owner, uint32 id, bool spellHit = true)
{
    SpellInfo const* s = g_spells.at(id).get();
    auto a = std::make_unique<Aura>(s, &owner);
    a->m_maxDuration = a->CalcMaxDuration();
    a->m_duration = a->m_maxDuration;
    for (SpellEffectInfo const& e : s->Effects)
    {
        auto eff = std::make_unique<AuraEffect>(a.get(), s, e);
        a->m_effects.push_back(eff.get());
        g_effects.push_back(std::move(eff));
    }
    for (AuraEffect* eff : a->m_effects)
        eff->CalculatePeriodic(&owner, true, false);
    owner.m_ownedAuras.emplace(id, a.get());
    if (spellHit)
        RunHitDuration(owner, a.get(), 0, false);
    std::ostringstream o; o << "{\"event\":\"create\",\"aura\":"; PrintAura(o, a.get()); o << "}";
    std::cout << o.str() << "\n";
    g_auras.push_back(std::move(a));
}

int main()
{
    Unit owner;
    std::string cmd;
    while (std::cin >> cmd)
    {
        if (cmd == "spell")
        {
            auto s = std::make_unique<SpellInfo>();
            std::cin >> s->Id >> s->Attributes >> s->AttributesEx >> s->AttributesEx2 >> s->AttributesEx3 >> s->AttributesEx5
                     >> s->AttributesEx8 >> s->AttributesEx13 >> s->StackAmount >> s->Duration >> s->SpellFamilyName;
            g_spells[s->Id] = std::move(s);
        }
        else if (cmd == "effect")
        {
            uint32 id, type; int32 period;
            std::cin >> id >> type >> period;
            SpellInfo* s = g_spells.at(id).get();
            SpellEffectInfo e;
            e.EffectIndex = uint32(s->Effects.size());
            e.ApplyAuraName = AuraType(type);
            e.ApplyAuraPeriod = period;
            s->Effects.push_back(e);
        }
        else if (cmd == "caster")
            std::cin >> owner.data.ModCastingSpeed >> owner.data.ModHaste;
        else if (cmd == "create")
        {
            uint32 id; std::cin >> id;
            CreateAura(owner, id);
        }
        else if (cmd == "reapply")
        {
            uint32 id, flags; std::cin >> id >> flags;
            Aura* a = FindAura(owner, id);
            if (!a) { CreateAura(owner, id); continue; }   // TryRefreshStackOrCreate -> Create (SpellAuras.cpp:388)
            Spell spell; spell.m_spellInfo = a->GetSpellInfo(); spell._triggeredCastFlags = flags;
            bool reset = spell.ProbeResetPeriodicTimer();
            bool removed = a->ModStackAmount(1, AURA_REMOVE_BY_DEFAULT, reset);      // Unit.cpp:3441 (StackAmount 1)
            if (!removed)
                RunHitDuration(owner, a, flags, true);
            std::ostringstream o; o << "{\"event\":\"reapply\",\"reset\":" << (reset ? "true" : "false") << ",\"aura\":"; PrintAura(o, a); o << "}";
            std::cout << o.str() << "\n";
        }
        else if (cmd == "modstack")
        {
            uint32 id; int32 num; int reset, create; std::cin >> id >> num >> reset >> create;
            Aura* a = FindAura(owner, id);
            if (!a) { if (create) CreateAura(owner, id, false); else std::cout << "{\"event\":\"noop\"}\n"; continue; }
            a->ModStackAmount(num, AURA_REMOVE_BY_DEFAULT, reset != 0);
            std::ostringstream o; o << "{\"event\":\"modstack\",\"aura\":"; PrintAura(o, a); o << "}";
            std::cout << o.str() << "\n";
        }
        else if (cmd == "setdur" || cmd == "setmax")
        {
            uint32 id; int32 delta; std::cin >> id >> delta;
            if (Aura* a = FindAura(owner, id))
            {
                if (cmd == "setdur")
                    a->SetDuration(a->GetDuration() + delta);
                else
                    a->SetMaxDuration(a->GetDuration() + delta);
            }
        }
        else if (cmd == "remove")
        {
            uint32 id; std::cin >> id;
            auto it = owner.m_ownedAuras.find(id);
            if (it != owner.m_ownedAuras.end())
                owner.RemoveOwnedAura(it, AURA_REMOVE_BY_CANCEL);
        }
        else if (cmd == "update")
        {
            uint32 diff; std::cin >> diff;
            g_ticks.clear();
            owner.removals.clear();
            owner.ProbeUpdateOwnedAuras(diff);
            std::ostringstream o;
            o << "{\"event\":\"update\",\"diff\":" << diff << ",\"ticks\":[";
            for (std::size_t i = 0; i < g_ticks.size(); ++i)
                o << (i ? "," : "") << "{\"spell\":" << g_ticks[i].spell << ",\"effect\":" << g_ticks[i].effect << ",\"tick\":" << g_ticks[i].tick << "}";
            o << "],\"removed\":[";
            for (std::size_t i = 0; i < owner.removals.size(); ++i)
                o << (i ? "," : "") << owner.removals[i];
            o << "],\"auras\":[";
            bool first = true;
            for (auto const& [id, a] : owner.m_ownedAuras) { o << (first ? "" : ","); first = false; PrintAura(o, a); }
            o << "]}";
            std::cout << o.str() << "\n";
        }
        else
        {
            std::cerr << "unknown command " << cmd << "\n";
            return 2;
        }
    }
    return 0;
}
