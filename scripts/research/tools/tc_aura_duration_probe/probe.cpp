// Differential probe for aura duration / refresh / pandemic (aura_lifecycle/duration.py + refresh.py, track B).
//
// tc_duration_decls.inc / tc_duration_bodies.inc are pulled verbatim out of the sibling TrinityCore
// checkout by extract.py.  This file supplies only the scaffolding the bodies reference, with
// Trinity's member names.  BOUNDED probe: the cut points below replace engine state with
// driver-supplied values.
//
// Cut points (semantics stated, not extracted):
//   Player::ApplySpellMod        -> Player.cpp:22851 formula with driver (flat, mul) per SpellModOp
//                                   (Duration, Period, ChangeCastTime, MaxAuraStacks); other ops untouched.
//   WorldObject::ModSpellDuration -> identity (target duration mods are not the question here).
//   Aura::Aura duration init     -> SpellAuras.cpp:494-495 (m_maxDuration = CalcMaxDuration(caster);
//                                   m_duration = m_maxDuration), stack 1; AuraEffect ctor ->
//                                   CalculatePeriodic(caster, true, false) (SpellAuraEffects.cpp:742).
//   Refresh path                 -> the driver calls ModStackAmount(1, AURA_REMOVE_BY_DEFAULT,
//                                   ResetPeriodicTimerForHit()) exactly as Unit.cpp:3441 does, then
//                                   CommitAuraDuration(refresh=true) as Spell.cpp:3254-3298 does.
//   Hit quote                    -> Aura::CalcMaxDuration(spellInfo, caster, &powerCosts) (Spell.cpp:3216); no DR.
//   Aura::SetStackAmount         -> stores the count (amount recalculation is track C/D).
//   SetCharges / CalcMaxCharges  -> no-op / 0 (track C).
//   Script hooks                 -> none.  PeriodicTick -> logged.  Unit::_UpdateSpells order ->
//                                   Aura::Update, then each AuraEffect::Update, then IsExpired (Unit.cpp:2976-2991,
//                                   SpellAuras.cpp:817-853).
//
// Protocol (stdin, whitespace separated):
//   spell <dur> <maxdur> <perres> <hasEntry> <stackAmount> <family> <empower> <a0> <a1> <a2> <a3> <a5> <a8> <a13>
//   effect <auraType> <period>
//   caster <typeid> <castSpeed> <haste> <durFlat> <durMul> <perFlat> <perMul> <ctFlat> <ctMul> <stkFlat>
//   spellvalue <durationMul> <duration|-> <triggeredFlags>
//   apply <comboPoints|-1>        -> {"apply": {...state, "refresh": b}}
//   update <diff>                 -> {"update": diff, "events": [...], "state": {...}|null}
//   calcpct <base> <pct>          -> {"calcpct": n}
//   maxdur <comboPoints|-1>       -> {"maxdur": n}
//   reset

#include "Define.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#define ASSERT(cond, ...) do { if (!(cond)) std::abort(); } while (0)
#define IN_MILLISECONDS 1000

#include "tc_duration_decls.inc"

template <class T> using Optional = std::optional<T>;

enum Powers : int8 { POWER_HEALTH = -2, POWER_MANA = 0, POWER_COMBO_POINTS = 4 };   // SharedDefines.h (copied)
enum TypeID : uint8 { TYPEID_UNIT = 5, TYPEID_PLAYER = 6 };                            // ObjectGuid.h (copied)
enum WeaponAttackType : uint8 { BASE_ATTACK = 0, OFF_ATTACK = 1, RANGED_ATTACK = 2 };  // SharedDefines.h (copied)

struct SpellPowerCost { Powers Power; int32 Amount; };
struct SpellPowerEntry { int32 ManaPerSecond; float PowerPctPerSecond; uint32 RequiredAuraSpellID; int8 PowerType; };
struct SpellDurationEntry { uint32 ID; int32 Duration; int32 MaxDuration; int32 DurationPerResource; };
struct SpellEffectInfo { uint32 EffectIndex; int32 ApplyAuraPeriod; };

class Spell;
class Player;
class Unit;
class Aura;
class AuraEffect;
struct AuraApplication {};

class SpellInfo
{
public:
    uint32 Id = 1;
    uint32 Attr[17] = {};
    SpellDurationEntry const* DurationEntry = nullptr;
    uint32 StackAmount = 0;
    uint32 SpellFamilyName = 1;
    bool empower = false;

    bool HasAttribute(SpellAttr0 a) const { return Attr[0] & a; }
    bool HasAttribute(SpellAttr1 a) const { return Attr[1] & a; }
    bool HasAttribute(SpellAttr2 a) const { return Attr[2] & a; }
    bool HasAttribute(SpellAttr3 a) const { return Attr[3] & a; }
    bool HasAttribute(SpellAttr5 a) const { return Attr[5] & a; }
    bool HasAttribute(SpellAttr8 a) const { return Attr[8] & a; }
    bool HasAttribute(SpellAttr13 a) const { return Attr[13] & a; }
    bool IsPassive() const;
    bool IsChanneled() const;
    bool IsEmpowerSpell() const { return empower; }
    int32 GetDuration() const;
    int32 GetMaxDuration() const;
};

struct UnitData { float ModCastingSpeed = 1.0f; float ModHaste = 1.0f; };

struct ModPair { int32 flat = 0; float mul = 1.0f; };

class WorldObject
{
public:
    virtual ~WorldObject() = default;
    TypeID typeId = TYPEID_PLAYER;
    TypeID GetTypeId() const { return typeId; }
    Player* GetSpellModOwner() const;
    Unit const* ToUnit() const;
    int32 CalcSpellDuration(SpellInfo const* spellInfo, std::vector<SpellPowerCost> const* powerCosts) const;
    void ModSpellDurationTime(SpellInfo const* spellInfo, int32& duration, Spell* spell = nullptr) const;
    int32 ModSpellDuration(SpellInfo const*, WorldObject const*, int32 duration, bool, uint32) const { return duration; }
};

class Unit : public WorldObject
{
public:
    UnitData unitData;
    UnitData* m_unitData = &unitData;
    float m_modAttackSpeedPct[3] = { 1.0f, 1.0f, 1.0f };
    bool IsPlayer() const { return typeId == TYPEID_PLAYER; }
    bool HasAura(uint32) const { return false; }
    uint32 GetMaxPower(Powers) const { return 0; }
    uint32 GetMaxHealth() const { return 0; }
    uint32 GetHealth() const { return 0; }
    uint32 GetPower(Powers) const { return 0; }
    void ModifyHealth(int32) {}
    void ModifyPower(Powers, int32) {}
};

class Player : public Unit
{
public:
    ModPair duration, period, castTime, stacks;
    template <class T> void ApplySpellMod(SpellInfo const*, SpellModOp op, T& basevalue, Spell* = nullptr) const
    {
        ModPair const* m = op == SpellModOp::Duration ? &duration : op == SpellModOp::Period ? &period
            : op == SpellModOp::ChangeCastTime ? &castTime : op == SpellModOp::MaxAuraStacks ? &stacks : nullptr;
        if (!m)
            return;
        float totalmul = m->mul;
        int32 totalflat = m->flat;
        basevalue = T((double(basevalue) + totalflat) * totalmul);   // Player.cpp:22851
    }
};

Player* WorldObject::GetSpellModOwner() const
{
    return typeId == TYPEID_PLAYER ? static_cast<Player*>(const_cast<WorldObject*>(this)) : nullptr;
}
Unit const* WorldObject::ToUnit() const { return static_cast<Unit const*>(this); }

class Aura
{
public:
    SpellInfo const* m_spellInfo = nullptr;
    int32 m_maxDuration = 0;
    int32 m_duration = 0;
    int32 m_timeCla = 0;
    std::vector<SpellPowerEntry const*> m_periodicCosts;
    uint8 m_stackAmount = 1;
    void* m_dropEvent = nullptr;
    bool m_isRemoved = false;
    Unit* m_caster = nullptr;
    std::vector<AuraEffect*> m_effects;

    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    Unit* GetCaster() const { return m_caster; }
    WorldObject* GetOwner() const { return nullptr; }
    int32 GetMaxDuration() const { return m_maxDuration; }
    void SetMaxDuration(int32 duration) { m_maxDuration = duration; }
    int32 CalcMaxDuration() const { return CalcMaxDuration(GetCaster()); }
    int32 CalcMaxDuration(Unit* caster) const;
    static int32 CalcMaxDuration(SpellInfo const* spellInfo, WorldObject const* caster, std::vector<SpellPowerCost> const* powerCosts);
    int32 GetDuration() const { return m_duration; }
    void SetDuration(int32 duration, bool withMods = false);
    void RefreshDuration(bool withMods = false);
    void RefreshTimers(bool resetPeriodicTimer);
    bool IsExpired() const { return !GetDuration() && !m_dropEvent; }   // SpellAuras.h:226 (copied)
    bool IsPermanent() const { return GetMaxDuration() == -1; }          // SpellAuras.h:227 (copied)
    bool IsPassive() const { return GetSpellInfo()->IsPassive(); }
    uint8 GetStackAmount() const { return m_stackAmount; }
    void SetStackAmount(uint8 stackAmount) { m_stackAmount = stackAmount; }
    uint32 CalcMaxStackAmount() const;
    bool ModStackAmount(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT, bool resetPeriodicTimer = true);
    void SetCharges(uint8) {}
    uint8 CalcMaxCharges() const { return 0; }
    void SetNeedClientUpdateForTargets() {}
    void Remove(AuraRemoveMode = AURA_REMOVE_BY_DEFAULT) { m_isRemoved = true; }
    std::vector<AuraEffect*> const& GetAuraEffects() const { return m_effects; }
    uint32 GetEffectMask() const { return 1; }
    void Update(uint32 diff, Unit* caster);
    void CallScriptEffectCalcPeriodicHandlers(AuraEffect const*, bool&, int32&) {}
    void CallScriptEffectUpdatePeriodicHandlers(AuraEffect*) {}
};
using UnitAura = Aura;

static std::vector<std::string> g_events;

class AuraEffect
{
public:
    Aura* m_base;
    SpellInfo const* m_spellInfo;
    SpellEffectInfo m_effectInfo;
    AuraType m_auraType;
    int32 _periodicTimer = 0;
    int32 _period = 0;
    uint32 _ticksDone = 0;
    bool m_isPeriodic = false;
    AuraApplication m_app;

    AuraEffect(Aura* base, SpellEffectInfo info, AuraType type) : m_base(base), m_spellInfo(base->GetSpellInfo()), m_effectInfo(info), m_auraType(type) {}
    Aura* GetBase() const { return m_base; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    SpellEffectInfo const& GetSpellEffectInfo() const { return m_effectInfo; }
    AuraType GetAuraType() const { return m_auraType; }
    int32 GetPeriod() const { return _period; }
    void ResetTicks() { _ticksDone = 0; }
    uint32 GetTotalTicks() const;
    void ResetPeriodic(bool resetPeriodicTimer = false);
    void CalculatePeriodic(Unit* caster, bool resetPeriodicTimer = true, bool load = false);
    void Update(uint32 diff, Unit* caster);
    void GetApplicationList(std::vector<AuraApplication*>& list) { list.push_back(&m_app); }
    void PeriodicTick(AuraApplication*, Unit*)
    {
        std::ostringstream o;
        o << "{\"event\":\"tick\",\"effect\":" << m_effectInfo.EffectIndex << ",\"n\":" << _ticksDone << ",\"total\":" << GetTotalTicks() << "}";
        g_events.push_back(o.str());
    }
};

struct SpellValue { Optional<int32> Duration; float DurationMul = 1.0f; };
struct TargetInfo { int32 AuraDuration = 0; bool Positive = false; UnitAura* HitAura = nullptr; };

class Spell
{
public:
    SpellInfo const* m_spellInfo = nullptr;
    SpellValue* m_spellValue = nullptr;
    Unit* m_originalCaster = nullptr;
    uint32 _triggeredCastFlags = 0;
    bool ResetPeriodicTimerForHit() const;
    void CommitAuraDuration(Unit* unit, TargetInfo& hitInfo, WorldObject* caster, bool refresh);
};

#include "tc_duration_bodies.inc"

// ---------------------------------------------------------------------------------------------
static SpellInfo g_info;
static SpellDurationEntry g_entry;
static std::vector<std::pair<AuraType, int32>> g_effects;
static Player g_caster;
static SpellValue g_value;
static Aura* g_aura = nullptr;
static uint32 g_triggered = 0;

static std::string state()
{
    if (!g_aura)
        return "null";
    std::ostringstream o;
    o << "{\"duration\":" << g_aura->m_duration << ",\"max_duration\":" << g_aura->m_maxDuration
      << ",\"stacks\":" << int(g_aura->m_stackAmount) << ",\"effects\":[";
    for (size_t i = 0; i < g_aura->m_effects.size(); ++i)
    {
        AuraEffect* e = g_aura->m_effects[i];
        o << (i ? "," : "") << "{\"period\":" << e->_period << ",\"timer\":" << e->_periodicTimer << ",\"ticks\":" << e->_ticksDone
          << ",\"periodic\":" << (e->m_isPeriodic ? "true" : "false") << "}";
    }
    o << "]}";
    return o.str();
}

static std::vector<SpellPowerCost> costs(int cp)
{
    std::vector<SpellPowerCost> c;
    if (cp >= 0)
        c.push_back({ POWER_COMBO_POINTS, cp });
    return c;
}

int main()
{
    std::string cmd;
    while (std::cin >> cmd)
    {
        if (cmd == "reset")
        {
            delete g_aura;
            g_aura = nullptr;
            g_info = SpellInfo();
            g_effects.clear();
            g_caster = Player();
            g_value = SpellValue();
        }
        else if (cmd == "spell")
        {
            int32 hasEntry, emp, a0, a1, a2, a3, a5, a8, a13;
            std::cin >> g_entry.Duration >> g_entry.MaxDuration >> g_entry.DurationPerResource >> hasEntry >> g_info.StackAmount
                     >> g_info.SpellFamilyName >> emp >> a0 >> a1 >> a2 >> a3 >> a5 >> a8 >> a13;
            g_info.DurationEntry = hasEntry ? &g_entry : nullptr;
            g_info.empower = emp;
            g_info.Attr[0] = a0; g_info.Attr[1] = a1; g_info.Attr[2] = a2; g_info.Attr[3] = a3;
            g_info.Attr[5] = a5; g_info.Attr[8] = a8; g_info.Attr[13] = a13;
        }
        else if (cmd == "effect")
        {
            uint32 type; int32 period;
            std::cin >> type >> period;
            g_effects.push_back({ AuraType(type), period });
        }
        else if (cmd == "caster")
        {
            int t;
            std::cin >> t >> g_caster.unitData.ModCastingSpeed >> g_caster.unitData.ModHaste >> g_caster.duration.flat >> g_caster.duration.mul
                     >> g_caster.period.flat >> g_caster.period.mul >> g_caster.castTime.flat >> g_caster.castTime.mul >> g_caster.stacks.flat;
            g_caster.typeId = TypeID(t);
        }
        else if (cmd == "spellvalue")
        {
            std::string d;
            std::cin >> g_value.DurationMul >> d >> g_triggered;
            g_value.Duration = d == "-" ? Optional<int32>() : Optional<int32>(std::stoi(d));
            std::cout << "{\"spellvalue\":true}\n";
        }
        else if (cmd == "calcpct")
        {
            int32 base; int32 pct;
            std::cin >> base >> pct;
            std::cout << "{\"calcpct\":" << CalculatePct(base, pct) << "}\n";
        }
        else if (cmd == "maxdur")
        {
            int cp;
            std::cin >> cp;
            std::vector<SpellPowerCost> c = costs(cp);
            std::cout << "{\"maxdur\":" << Aura::CalcMaxDuration(&g_info, &g_caster, cp >= 0 ? &c : nullptr) << "}\n";
        }
        else if (cmd == "apply")
        {
            int cp;
            std::cin >> cp;
            std::vector<SpellPowerCost> c = costs(cp);
            Spell spell;
            spell.m_spellInfo = &g_info;
            spell.m_spellValue = &g_value;
            spell.m_originalCaster = &g_caster;
            spell._triggeredCastFlags = g_triggered;
            TargetInfo hitInfo;
            hitInfo.AuraDuration = g_value.Duration ? *g_value.Duration : Aura::CalcMaxDuration(&g_info, &g_caster, &c);
            bool refresh = false;
            if (!g_aura)
            {
                g_aura = new Aura();
                g_aura->m_spellInfo = &g_info;
                g_aura->m_caster = &g_caster;
                g_aura->m_maxDuration = g_aura->CalcMaxDuration(&g_caster);
                g_aura->m_duration = g_aura->m_maxDuration;
                uint32 idx = 0;
                for (auto const& [type, period] : g_effects)
                {
                    AuraEffect* e = new AuraEffect(g_aura, { idx++, period }, type);
                    g_aura->m_effects.push_back(e);
                    e->CalculatePeriodic(&g_caster, true, false);
                }
            }
            else
            {
                refresh = true;
                g_aura->ModStackAmount(1, AURA_REMOVE_BY_DEFAULT, spell.ResetPeriodicTimerForHit());
            }
            hitInfo.HitAura = g_aura;
            std::string mid = state();
            spell.CommitAuraDuration(&g_caster, hitInfo, &g_caster, refresh);
            std::cout << "{\"apply\":" << state() << ",\"after_try_refresh\":" << mid << ",\"refresh\":" << (refresh ? "true" : "false")
                      << ",\"hit\":" << hitInfo.AuraDuration << "}\n";
        }
        else if (cmd == "update")
        {
            uint32 diff;
            std::cin >> diff;
            g_events.clear();
            bool expired = false;
            if (g_aura)
            {
                g_aura->Update(diff, &g_caster);
                for (AuraEffect* e : g_aura->GetAuraEffects())
                    e->Update(diff, &g_caster);
                if (g_aura->IsExpired())
                {
                    expired = true;
                    g_events.push_back("{\"event\":\"expire\"}");
                }
            }
            std::cout << "{\"update\":" << diff << ",\"events\":[";
            for (size_t i = 0; i < g_events.size(); ++i)
                std::cout << (i ? "," : "") << g_events[i];
            std::cout << "],\"state\":" << state() << "}\n";
            if (expired)
            {
                for (AuraEffect* e : g_aura->m_effects)
                    delete e;
                delete g_aura;
                g_aura = nullptr;
            }
        }
        std::cout.flush();
    }
    return 0;
}

