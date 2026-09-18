// Differential probe for the aura stack/charge oracle (aura_lifecycle/stacks.py + charges.py, Track C).
//
// tc_stack_decls.inc / tc_stack_bodies.inc / tc_stack_createinfo.inc are pulled verbatim out of the
// sibling TrinityCore checkout by extract.py.  This file supplies only the scaffolding the bodies
// reference, with Trinity's member names.  BOUNDED probe: one Aura on one owner.
//
// Cut points (semantics stated, not extracted):
//   Aura::Aura initializer  m_stackAmount(createInfo.StackAmount) -> the same narrowing assignment here;
//                           the charge statements of the constructor are extracted (ProbeCtorCharges).
//   Aura::CalcMaxDuration   -> driver value (`calcmaxdur`), logged (track B owns duration).
//   Aura::Remove            -> logs the mode, sets m_isRemoved (track E owns removal).
//   AuraEffect::CalculateAmount -> m_baseAmount, times stacks unless SuppressPointsStacking (the stack
//                           tail of the real function; rounding/clamp: stacks.scale_amount), logged.
//   AuraEffect::ChangeAmount / CalculatePeriodic / ResetTicks / Aura::HandleAuraSpecificMods -> logged.
//   Player::GetSpellModValues -> driver flat/pct per SpellModOp (`mod`); ApplySpellMod is verbatim.
//   SpellMgr::GetSpellProcEntry -> driver entry (`proc`).
//   Unit::GetOwnedAura      -> the single aura if SpellId / caster (unless empty) / cast item (unless empty)
//                           match (Unit.cpp GetOwnedAura contract), logged with the requested key.
//   One application whose remove mode is AURA_REMOVE_NONE.
//
// Protocol (stdin, whitespace separated):
//   spell <id> <stackAmount> <procCharges> <attr0> <attr1> <attr3> <attr5> <attr6> <attr13> <customAttr> <getMaxDuration>
//   effect <idx> <basePoints> <effectAttributes>
//   proc <present> <charges> <attributesMask>
//   mod <op> <flat> <pct>            (makes the caster a spell-mod owner)
//   calcmaxdur <ms>
//   create <stackAmount> <casterGuid> <castItemGuid> <duration>
//   modstack <num> <mode> <reset>    setstack <n>    modcharges <num> <mode>
//   prepare <eventNoConsume>         consume
//   reapply <stackAmount> <reset> <casterGuid> <castItemGuid> <auraEffMask> <bp0|-> <bp1|-> ...(one per effect)
//   resetflag <triggerFlags>         query
//   reset                            (new scenario)
// Every command prints one JSON line.

#include "Define.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#define ABORT() std::abort()
#define ASSERT(cond, ...) do { if (!(cond)) std::abort(); } while (0)
#define ASSERT_NODEBUGINFO(cond) ASSERT(cond)

#include "tc_stack_decls.inc"

using SpellEffectValue = double;

static std::vector<std::string> g_events;
static void ev(std::string const& s) { g_events.push_back(s); }

template <class E>
struct EnumFlag
{
    uint32 v = 0;
    bool HasFlag(E f) const { return (v & uint32(f)) != 0; }
};

struct SpellEffectInfo
{
    uint32 EffectIndex = 0;
    SpellEffectValue BasePoints = 0.0;
    EnumFlag<SpellEffectAttributes> EffectAttributes;
};

class SpellInfo
{
public:
    uint32 Id = 0;
    uint32 StackAmount = 0;
    uint32 ProcCharges = 0;
    uint32 Attributes[14] = {};
    uint32 AttributesCu = 0;
    int32 maxDuration = -1;
    std::vector<SpellEffectInfo> effects;

    bool HasAttribute(SpellAttr0 a) const { return (Attributes[0] & a) != 0; }
    bool HasAttribute(SpellAttr1 a) const { return (Attributes[1] & a) != 0; }
    bool HasAttribute(SpellAttr2 a) const { return (Attributes[2] & a) != 0; }
    bool HasAttribute(SpellAttr3 a) const { return (Attributes[3] & a) != 0; }
    bool HasAttribute(SpellAttr4 a) const { return (Attributes[4] & a) != 0; }
    bool HasAttribute(SpellAttr5 a) const { return (Attributes[5] & a) != 0; }
    bool HasAttribute(SpellAttr6 a) const { return (Attributes[6] & a) != 0; }
    bool HasAttribute(SpellAttr7 a) const { return (Attributes[7] & a) != 0; }
    bool HasAttribute(SpellAttr8 a) const { return (Attributes[8] & a) != 0; }
    bool HasAttribute(SpellAttr9 a) const { return (Attributes[9] & a) != 0; }
    bool HasAttribute(SpellAttr10 a) const { return (Attributes[10] & a) != 0; }
    bool HasAttribute(SpellAttr11 a) const { return (Attributes[11] & a) != 0; }
    bool HasAttribute(SpellAttr12 a) const { return (Attributes[12] & a) != 0; }
    bool HasAttribute(SpellAttr13 a) const { return (Attributes[13] & a) != 0; }
    bool HasAttribute(SpellCustomAttributes a) const { return (AttributesCu & a) != 0; }

    std::vector<SpellEffectInfo> const& GetEffects() const { return effects; }
    int32 GetMaxDuration() const { return maxDuration; }
    bool IsPassive() const;
    bool IsChanneled() const;
    bool IsMultiSlotAura() const;
    bool IsStackableOnOneSlotWithDifferentCasters() const;
};

struct SpellProcEntry
{
    uint32 AttributesMask = 0;
    uint32 Charges = 0;
};

static bool g_hasProc = false;
static SpellProcEntry g_proc;

struct SpellMgrStub
{
    SpellProcEntry const* GetSpellProcEntry(SpellInfo const* /*spellInfo*/) const { return g_hasProc ? &g_proc : nullptr; }
};
static SpellMgrStub g_spellMgr;
#define sSpellMgr (&g_spellMgr)

class ObjectGuid
{
public:
    uint64 v = 0;
    static ObjectGuid const Empty;
    bool IsEmpty() const { return v == 0; }
    bool operator!() const { return IsEmpty(); }
    bool operator==(ObjectGuid const& o) const { return v == o.v; }
    bool operator!=(ObjectGuid const& o) const { return v != o.v; }
};
ObjectGuid const ObjectGuid::Empty = {};

class Spell;
class Aura;
class Player;
struct AuraCreateInfo;

struct UnitDataStub { float ModCastingSpeed = 1.0f; };

class Unit
{
public:
    ObjectGuid m_guid;
    UnitDataStub m_unitDataStorage;
    UnitDataStub* m_unitData = &m_unitDataStorage;
    bool m_isModOwner = false;

    ObjectGuid GetGUID() const { return m_guid; }
    Player* GetSpellModOwner() const;
    Aura* GetOwnedAura(uint32 spellId, ObjectGuid casterGUID, ObjectGuid itemCasterGUID, uint32 reqEffMask) const;
    Aura* _TryStackingOrRefreshingExistingAura(AuraCreateInfo& createInfo);
};

static std::map<int, std::pair<int32, float>> g_mods;   // SpellModOp -> (flat, pct)

class Player : public Unit
{
public:
    template <class T>
    void ApplySpellMod(SpellInfo const* spellInfo, SpellModOp op, T& basevalue, Spell* spell = nullptr) const;
    void GetSpellModValues(SpellInfo const* /*spellInfo*/, SpellModOp op, Spell* /*spell*/, double /*base*/, int32* flat, float* pct) const
    {
        *flat = 0;
        *pct = 1.0f;
        auto it = g_mods.find(int(op));
        if (it != g_mods.end())
        {
            *flat = it->second.first;
            *pct = it->second.second;
        }
    }
};

Player* Unit::GetSpellModOwner() const
{
    return m_isModOwner ? static_cast<Player*>(const_cast<Unit*>(this)) : nullptr;
}

static Player g_caster;   // the aura's caster (and owner): one unit is enough for these functions
static Unit* g_casterPtr = &g_caster;

struct AuraCreateInfo
{
#include "tc_stack_createinfo.inc"
    SpellInfo const* GetSpellInfo() const { return _spellInfo; }
    uint32 GetAuraEffectMask() const { return _auraEffectMask; }

    ObjectGuid CasterGUID;
    Unit* Caster = nullptr;
    SpellEffectValue const* BaseAmount = nullptr;
    ObjectGuid CastItemGUID;
    uint32 CastItemId = 0;
    int32 CastItemLevel = -1;
    int32 StackAmount = 1;
    bool ResetPeriodicTimer = true;
    SpellInfo const* _spellInfo = nullptr;
    uint32 _auraEffectMask = 0;
};

class AuraApplication
{
public:
    AuraRemoveMode GetRemoveMode() const { return AURA_REMOVE_NONE; }
};

class ProcEventInfo
{
public:
    SpellInfo const* m_spellInfo = nullptr;
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
};

class AuraEffect;

class Aura
{
public:
    SpellInfo const* m_spellInfo = nullptr;
    ObjectGuid m_casterGuid;
    ObjectGuid m_castItemGuid;
    uint32 m_castItemId = 0;
    int32 m_castItemLevel = 0;
    int32 m_maxDuration = 0;
    int32 m_duration = 0;
    int32 m_timeCla = 0;
    uint8 m_procCharges = 0;
    uint8 m_stackAmount = 0;
    bool m_isRemoved = false;
    bool m_isUsingCharges = false;
    int m_removeMode = 0;
    uint32 m_effectMask = 0;
    std::vector<int> m_periodicCosts;
    std::vector<AuraEffect*> m_effects;
    AuraApplication m_app;

    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    uint32 GetId() const { return m_spellInfo->Id; }
    Unit* GetCaster() const { return g_casterPtr; }
    ObjectGuid GetCasterGUID() const { return m_casterGuid; }
    ObjectGuid GetCastItemGUID() const { return m_castItemGuid; }
    uint32 GetEffectMask() const { return m_effectMask; }
    AuraEffect* GetEffect(uint32 index) const;
    std::vector<AuraEffect*> const& GetAuraEffects() const { return m_effects; }
    void GetApplicationVector(std::vector<AuraApplication*>& out) const { out.push_back(const_cast<AuraApplication*>(&m_app)); }
    void HandleAuraSpecificMods(AuraApplication const* /*aurApp*/, Unit* /*caster*/, bool apply, bool onReapply)
    {
        ev(std::string("specific_mods:") + (apply ? "1" : "0") + (onReapply ? "1" : "0"));
    }
    void SetNeedClientUpdateForTargets() const {}
    bool IsRemoved() const { return m_isRemoved; }
    void Remove(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT)
    {
        ev("remove:" + std::to_string(int(removeMode)));
        m_isRemoved = true;
        m_removeMode = int(removeMode);
    }

    int32 GetMaxDuration() const { return m_maxDuration; }
    void SetMaxDuration(int32 duration) { m_maxDuration = duration; }
    int32 GetDuration() const { return m_duration; }
    void SetDuration(int32 duration, bool withMods = false);
    void RefreshDuration(bool withMods = false);
    void RefreshTimers(bool resetPeriodicTimer);
    static int32 s_calcMaxDuration;
    int32 CalcMaxDuration(Unit* /*caster*/) const { ev("calc_max_duration"); return s_calcMaxDuration; }
    int32 CalcMaxDuration() const { return CalcMaxDuration(GetCaster()); }

    uint8 GetCharges() const { return m_procCharges; }
    void SetCharges(uint8 charges);
    uint8 CalcMaxCharges(Unit* caster) const;
    uint8 CalcMaxCharges() const { return CalcMaxCharges(GetCaster()); }
    bool ModCharges(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT);
    bool DropCharge(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT) { return ModCharges(-1, removeMode); }
    bool IsUsingCharges() const { return m_isUsingCharges; }

    uint8 GetStackAmount() const { return m_stackAmount; }
    void SetStackAmount(uint8 num);
    bool ModStackAmount(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT, bool resetPeriodicTimer = true);
    uint32 CalcMaxStackAmount() const;
    bool IsUsingStacks() const;

    void PrepareProcChargeDrop(SpellProcEntry const* procEntry, ProcEventInfo const& eventInfo);
    void ConsumeProcCharges(SpellProcEntry const* procEntry);
    void ProbeCtorCharges(AuraCreateInfo const& createInfo);
};
int32 Aura::s_calcMaxDuration = -1;

class AuraEffect
{
public:
    Aura* m_base = nullptr;
    uint32 m_effIndex = 0;
    SpellEffectValue m_baseAmount = 0.0;
    SpellEffectValue _amount = 0.0;
    bool m_suppressPointsStacking = false;

    Aura* GetBase() const { return m_base; }
    SpellEffectValue CalculateAmount(Unit* /*caster*/)
    {
        ev("calculate_amount:" + std::to_string(m_effIndex));
        // stack tail of SpellAuraEffects.cpp:844-845 (CalcValue replaced by m_baseAmount)
        SpellEffectValue amount = m_baseAmount;
        if (!m_suppressPointsStacking)
            amount *= GetBase()->GetStackAmount();
        return amount;
    }
    void ChangeAmount(SpellEffectValue newAmount, bool mark = true, bool onStackOrReapply = false)
    {
        ev("change_amount:" + std::to_string(m_effIndex) + ":" + (mark ? "1" : "0") + (onStackOrReapply ? "1" : "0"));
        _amount = newAmount;
    }
    void CalculatePeriodic(Unit* /*caster*/, bool resetPeriodicTimer = true, bool load = false)
    {
        ev(std::string("calculate_periodic:") + std::to_string(m_effIndex) + ":" + (resetPeriodicTimer ? "1" : "0") + (load ? "1" : "0"));
    }
    void ResetTicks() { ev("reset_ticks:" + std::to_string(m_effIndex)); }
};

AuraEffect* Aura::GetEffect(uint32 index) const
{
    for (AuraEffect* e : m_effects)
        if (e->m_effIndex == index)
            return e;
    return nullptr;
}

static Aura* g_aura = nullptr;
static SpellInfo g_spell;

Aura* Unit::GetOwnedAura(uint32 spellId, ObjectGuid casterGUID, ObjectGuid itemCasterGUID, uint32 reqEffMask) const
{
    ev("get_owned_aura:" + std::to_string(casterGUID.v) + ":" + std::to_string(itemCasterGUID.v));
    if (!g_aura || g_aura->IsRemoved() || g_aura->GetId() != spellId)
        return nullptr;
    if (!casterGUID.IsEmpty() && g_aura->GetCasterGUID() != casterGUID)
        return nullptr;
    if (!itemCasterGUID.IsEmpty() && g_aura->GetCastItemGUID() != itemCasterGUID)
        return nullptr;
    if (reqEffMask && (g_aura->GetEffectMask() & reqEffMask) != reqEffMask)
        return nullptr;
    return g_aura;
}

#include "tc_stack_bodies.inc"

// ---------------------------------------------------------------------------
// driver
// ---------------------------------------------------------------------------

static void print(std::string const& cmd, std::string const& ret)
{
    std::ostringstream o;
    o << "{\"cmd\":\"" << cmd << "\",\"ret\":" << ret << ",\"events\":[";
    for (size_t i = 0; i < g_events.size(); ++i)
        o << (i ? "," : "") << "\"" << g_events[i] << "\"";
    o << "],\"state\":";
    if (!g_aura)
        o << "null";
    else
    {
        o << "{\"stacks\":" << int(g_aura->m_stackAmount) << ",\"charges\":" << int(g_aura->m_procCharges)
          << ",\"using_charges\":" << (g_aura->m_isUsingCharges ? "true" : "false")
          << ",\"duration\":" << g_aura->m_duration << ",\"max_duration\":" << g_aura->m_maxDuration
          << ",\"removed\":" << (g_aura->m_isRemoved ? "true" : "false") << ",\"remove_mode\":" << g_aura->m_removeMode
          << ",\"cast_item\":" << g_aura->m_castItemGuid.v << ",\"effects\":[";
        for (size_t i = 0; i < g_aura->m_effects.size(); ++i)
        {
            char buf[128];
            std::snprintf(buf, sizeof(buf), "{\"index\":%u,\"base\":%.17g,\"amount\":%.17g}",
                          g_aura->m_effects[i]->m_effIndex, g_aura->m_effects[i]->m_baseAmount, g_aura->m_effects[i]->_amount);
            o << (i ? "," : "") << buf;
        }
        o << "]}";
    }
    o << "}";
    std::cout << o.str() << std::endl;
    g_events.clear();
}

static void resetAll()
{
    if (g_aura)
    {
        for (AuraEffect* e : g_aura->m_effects)
            delete e;
        delete g_aura;
    }
    g_aura = nullptr;
    g_spell = SpellInfo();
    g_hasProc = false;
    g_proc = SpellProcEntry();
    g_mods.clear();
    g_caster.m_isModOwner = false;
    Aura::s_calcMaxDuration = -1;
    g_events.clear();
}

int main()
{
    g_caster.m_guid.v = 1;
    std::string cmd;
    while (std::cin >> cmd)
    {
        if (cmd == "reset")
            resetAll();
        else if (cmd == "spell")
        {
            uint32 a0, a1, a3, a5, a6, a13;
            std::cin >> g_spell.Id >> g_spell.StackAmount >> g_spell.ProcCharges >> a0 >> a1 >> a3 >> a5 >> a6 >> a13
                     >> g_spell.AttributesCu >> g_spell.maxDuration;
            g_spell.Attributes[0] = a0; g_spell.Attributes[1] = a1; g_spell.Attributes[3] = a3;
            g_spell.Attributes[5] = a5; g_spell.Attributes[6] = a6; g_spell.Attributes[13] = a13;
        }
        else if (cmd == "effect")
        {
            SpellEffectInfo e;
            std::cin >> e.EffectIndex >> e.BasePoints >> e.EffectAttributes.v;
            g_spell.effects.push_back(e);
        }
        else if (cmd == "proc")
        {
            int present;
            std::cin >> present >> g_proc.Charges >> g_proc.AttributesMask;
            g_hasProc = present != 0;
        }
        else if (cmd == "mod")
        {
            int op; int32 flat; float pct;
            std::cin >> op >> flat >> pct;
            g_mods[op] = { flat, pct };
            g_caster.m_isModOwner = true;
        }
        else if (cmd == "calcmaxdur")
            std::cin >> Aura::s_calcMaxDuration;
        else if (cmd == "create")
        {
            int32 stackAmount; uint64 caster, item; int32 duration;
            std::cin >> stackAmount >> caster >> item >> duration;
            AuraCreateInfo createInfo;
            createInfo._spellInfo = &g_spell;
            createInfo.Caster = g_casterPtr;
            createInfo.SetStackAmount(stackAmount);
            g_aura = new Aura();
            g_aura->m_spellInfo = &g_spell;
            g_aura->m_casterGuid.v = caster;
            g_aura->m_castItemGuid.v = item;
            g_aura->m_stackAmount = createInfo.StackAmount;   // Aura::Aura initializer m_stackAmount(createInfo.StackAmount)
            g_aura->m_maxDuration = duration;
            g_aura->m_duration = duration;
            g_aura->ProbeCtorCharges(createInfo);
            for (SpellEffectInfo const& e : g_spell.effects)
            {
                AuraEffect* ae = new AuraEffect();
                ae->m_base = g_aura;
                ae->m_effIndex = e.EffectIndex;
                ae->m_baseAmount = e.BasePoints;
                ae->m_suppressPointsStacking = e.EffectAttributes.HasFlag(SpellEffectAttributes::SuppressPointsStacking);
                ae->_amount = ae->m_suppressPointsStacking ? e.BasePoints : e.BasePoints * g_aura->m_stackAmount;
                g_aura->m_effects.push_back(ae);
                g_aura->m_effectMask |= 1u << e.EffectIndex;
            }
            print(cmd, "null");
        }
        else if (cmd == "modstack")
        {
            int32 num; int mode, reset;
            std::cin >> num >> mode >> reset;
            bool r = g_aura->ModStackAmount(num, AuraRemoveMode(mode), reset != 0);
            print(cmd, r ? "true" : "false");
        }
        else if (cmd == "setstack")
        {
            int32 n;
            std::cin >> n;
            g_aura->SetStackAmount(uint8(n));
            print(cmd, "null");
        }
        else if (cmd == "modcharges")
        {
            int32 num; int mode;
            std::cin >> num >> mode;
            bool r = g_aura->ModCharges(num, AuraRemoveMode(mode));
            print(cmd, r ? "true" : "false");
        }
        else if (cmd == "prepare")
        {
            int noConsume;
            std::cin >> noConsume;
            SpellInfo eventSpell;
            if (noConsume)
                eventSpell.Attributes[6] = SPELL_ATTR6_DO_NOT_CONSUME_RESOURCES;
            ProcEventInfo info;
            info.m_spellInfo = &eventSpell;
            g_aura->PrepareProcChargeDrop(sSpellMgr->GetSpellProcEntry(&g_spell), info);
            print(cmd, "null");
        }
        else if (cmd == "consume")
        {
            g_aura->ConsumeProcCharges(sSpellMgr->GetSpellProcEntry(&g_spell));
            print(cmd, "null");
        }
        else if (cmd == "reapply")
        {
            int32 stackAmount; int reset; uint64 caster, item; uint32 mask;
            std::cin >> stackAmount >> reset >> caster >> item >> mask;
            std::vector<SpellEffectValue> bps(8, 0.0);
            bool haveBp = true;
            for (size_t i = 0; i < g_spell.effects.size(); ++i)
            {
                std::string tok;
                std::cin >> tok;
                if (tok == "-")
                    haveBp = false;
                else
                    bps[g_spell.effects[i].EffectIndex] = std::strtod(tok.c_str(), nullptr);
            }
            AuraCreateInfo createInfo;
            createInfo._spellInfo = &g_spell;
            createInfo._auraEffectMask = mask;
            createInfo.Caster = g_casterPtr;
            createInfo.CasterGUID.v = caster;
            createInfo.CastItemGUID.v = item;
            createInfo.BaseAmount = haveBp ? bps.data() : nullptr;
            createInfo.SetStackAmount(stackAmount);
            createInfo.ResetPeriodicTimer = reset != 0;
            Aura* found = g_caster._TryStackingOrRefreshingExistingAura(createInfo);
            print(cmd, found ? "true" : "false");
        }
        else if (cmd == "resetflag")
        {
            uint32 flags;
            std::cin >> flags;
            print(cmd, ProbeResetPeriodicOnHit(&g_spell, flags) ? "true" : "false");
        }
        else if (cmd == "query")
        {
            std::ostringstream o;
            o << "{\"multislot\":" << (g_spell.IsMultiSlotAura() ? "true" : "false")
              << ",\"one_slot\":" << (g_spell.IsStackableOnOneSlotWithDifferentCasters() ? "true" : "false")
              << ",\"channeled\":" << (g_spell.IsChanneled() ? "true" : "false")
              << ",\"using_stacks\":" << (g_aura && g_aura->IsUsingStacks() ? "true" : "false")
              << ",\"max_stacks\":" << (g_aura ? int64(int32(g_aura->CalcMaxStackAmount())) : 0)
              << ",\"max_charges\":" << (g_aura ? int(g_aura->CalcMaxCharges()) : 0) << "}";
            print(cmd, o.str());
        }
        else
        {
            std::cerr << "unknown command " << cmd << std::endl;
            return 2;
        }
    }
    resetAll();
    return 0;
}
