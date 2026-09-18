// Same-timestamp ordering / generation probe (aura-lifecycle Track I).
//
// tc_order_decls.inc / tc_order_bodies.inc are pulled verbatim out of the sibling TrinityCore
// checkout by extract.py; EventProcessor.cpp is included whole.  This file supplies only the
// scaffolding those bodies reference, with Trinity's member names, plus a driver that plays the
// role of Map::Update (Maps/Map.cpp:655-705): session phase first, then every unit in the declared
// order runs WorldObject::Update's `m_Events.Update(diff)` (Object.cpp:247) and Unit::Update's
// `_UpdateSpells(p_time)` (Unit.cpp:433).  BOUNDED probe: cut points replace engine state with
// driver-supplied values.
//
// Cut points (semantics stated, not extracted):
//   Aura::CalcMaxDuration()           -> driver `base` value of the aura (duration is Track B's)
//   Unit::ModSpellDuration            -> identity; ModSpellDurationTime -> no-op (no channels driven)
//   Unit::_UnapplyAura(iterator&)     -> set remove mode, erase from m_appliedAuras, Aura::_UnapplyForTarget
//                                        bookkeeping (m_applications erase, _removedApplications push), log
//   AuraEffect::PeriodicTick          -> log a tick, then run the driver action bound to that effect
//   AuraEffect::ChangeAmount/CalculateAmount, HandleAuraSpecificMods, client updates, scripts,
//   UpdateTargetMap, spell mods (GetSpellModOwner -> nullptr), SpellHistory, autorepeat -> inert
//   Spell::cast(bool)                 -> run the driver action, state FINISHED (IsDeletable true)
//
// Protocol (stdin, whitespace separated):
//   unit <name> <isPlayer 0|1> <speed>                 (update order = declaration order)
//   aura <name> <owner> <spellId> <base> <stackAmount> <flags>
//        flags: 1 EXTRA_INITIAL_PERIOD, 2 ATTR13 pandemic, 4 ATTR1_AURA_UNIQUE, 8 ATTR5_UNIQUE_PER_CASTER,
//               16 passive, 32 death persistent, 64 ATTR5_SPELL_HASTE_AFFECTS_PERIODIC
//   effect <aura> <period>                             (PERIODIC_DAMAGE effect; period 0 = non-periodic MOD_STAT)
//   ontick <aura> <effIndex> <action...>               (bound to that effect's PeriodicTick)
//   at <tick> <action...>                              (session phase of tick N, before unit updates)
//   cast <caster> <castTime> <tick> <action...>        (Spell::prepare in session phase of tick N; action at cast())
//   run <diff> <count>
//   actions: create <aura> | kill <unit> | remove <aura> | refresh <aura> <resetPeriodic 0|1> <hitDuration>
// Output: one JSON object {"log": [...], "auras": {...}}.

#include "Define.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <list>
#include <map>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#undef TC_COMMON_API
#define TC_COMMON_API
#define TRINITYCORE_ERRORS_H
#define ASSERT(cond, ...) do { if (!(cond)) { std::fprintf(stderr, "ASSERT %s\n", #cond); std::abort(); } } while (0)
#define ABORT() std::abort()
#define TC_LOG_DEBUG(...) ((void)0)
#define TC_LOG_ERROR(...) ((void)0)

#include "EventProcessor.h"
#include "EventProcessor.cpp"

#include "tc_order_decls.inc"

using SpellEffectValue = double;                     // SpellDefines.h:490
enum TypeID { TYPEID_UNIT = 5, TYPEID_PLAYER = 6 };
enum CurrentSpellTypes : uint8 { CURRENT_MELEE_SPELL = 0, CURRENT_GENERIC_SPELL = 1, CURRENT_CHANNELED_SPELL = 2, CURRENT_AUTOREPEAT_SPELL = 3, CURRENT_MAX_SPELL = 4 };
enum SpellState { SPELL_STATE_NULL = 0, SPELL_STATE_PREPARING = 1, SPELL_STATE_LAUNCHED = 2, SPELL_STATE_CHANNELING = 3, SPELL_STATE_FINISHED = 4, SPELL_STATE_IDLE = 5 };
enum class SpellModOp : uint8 { Duration, Period, ProcCharges, MaxAuraStacks };
enum Powers : int8 { POWER_HEALTH = -2 };

struct ObjectGuid
{
    uint64 v = 0;
    bool operator==(ObjectGuid const&) const = default;
    bool IsEmpty() const { return v == 0; }
    static ObjectGuid const Empty;
};
ObjectGuid const ObjectGuid::Empty = {};
constexpr int32 IN_MILLISECONDS = 1000;   // Common.h (copied)
template<> struct std::hash<ObjectGuid> { size_t operator()(ObjectGuid const& g) const { return std::hash<uint64>()(g.v); } };

// ---------------------------------------------------------------- log
static std::vector<std::string> gLog;
static uint64 gNow = 0;
static char const* gPhase = "init";
static void Log(std::string const& ev, std::string const& unit, std::string const& aura, std::string const& extra = "")
{
    std::ostringstream o;
    o << "{\"t\":" << gNow << ",\"phase\":\"" << gPhase << "\",\"ev\":\"" << ev << "\",\"unit\":\"" << unit << "\",\"aura\":\"" << aura << "\"";
    if (!extra.empty())
        o << "," << extra;
    o << "}";
    gLog.push_back(o.str());
}

// ---------------------------------------------------------------- static data
struct SpellEffectInfo { uint32 EffectIndex = 0; int32 ApplyAuraPeriod = 0; AuraType ApplyAuraName = SPELL_AURA_MOD_STAT; };
struct SpellPowerEntry { int32 RequiredAuraSpellID = 0; int32 ManaPerSecond = 0; int8 PowerType = 0; float PowerPctPerSecond = 0.0f; };
struct SpellProcEntry { uint32 Charges = 0; };

class SpellInfo
{
public:
    uint32 Id = 0;
    int32 StackAmount = 0;
    uint32 ProcCharges = 0;
    float LaunchDelay = 0.0f;
    uint32 Attributes[14] = {};
    bool Passive = false;
    bool DeathPersistent = false;
    std::vector<SpellEffectInfo> Effects;
    bool HasAttribute(SpellAttr1 a) const { return Attributes[1] & a; }
    bool HasAttribute(SpellAttr2 a) const { return Attributes[2] & a; }
    bool HasAttribute(SpellAttr5 a) const { return Attributes[5] & a; }
    bool HasAttribute(SpellAttr8 a) const { return Attributes[8] & a; }
    bool HasAttribute(SpellAttr13 a) const { return Attributes[13] & a; }
    bool IsChanneled() const { return false; }
    bool IsNextMeleeSwingSpell() const { return false; }
    int32 GetMaxDuration() const { std::abort(); }   // only reached by RefreshDuration(withMods=true), never driven
};

struct SpellMgrStub { SpellProcEntry const* GetSpellProcEntry(SpellInfo const*) const { return nullptr; } } gSpellMgr;
#define sSpellMgr (&gSpellMgr)

class Aura; class UnitAura; class AuraEffect; class AuraApplication; class Unit; class Spell; class Player; class GameObject;
typedef std::multimap<uint32, Aura*> AuraMap;                          // Unit.h
typedef std::pair<AuraMap::iterator, AuraMap::iterator> AuraMapBoundsNonConst;
typedef std::multimap<uint32, AuraApplication*> AuraApplicationMap;
typedef std::pair<AuraApplicationMap::iterator, AuraApplicationMap::iterator> AuraApplicationMapBoundsNonConst;
typedef std::list<Aura*> AuraList;
typedef std::list<GameObject*> GameObjectList;

class Player
{
public:
    template<class T> void ApplySpellMod(SpellInfo const*, SpellModOp, T&, Spell* = nullptr) const { std::abort(); }
    Spell* FindCurrentSpellBySpellId(uint32) const { return nullptr; }
    void SetSpellModTakingSpell(Spell*, bool) { }
};

class GameObject { public: bool isSpawned() const { return true; } void SetOwnerGUID(ObjectGuid) { } void SetRespawnTime(int) { } void Delete() { } };
struct SpellHistory { bool IsPaused() const { return false; } void Update() { } };
struct UnitData { float ModCastingSpeed = 1.0f; float ModHaste = 1.0f; };

class WorldObject
{
public:
    virtual ~WorldObject() = default;
    EventProcessor m_Events;
    ObjectGuid m_guid;
    std::string m_name;
    ObjectGuid GetGUID() const { return m_guid; }
    Unit* ToUnit();
};

// ---------------------------------------------------------------- AuraApplication
class AuraApplication
{
public:
    AuraApplication(Unit* target, Aura* base, uint8 mask) : _target(target), _base(base), _effectsToApply(mask), _effectMask(mask) { }
    Unit* GetTarget() const { return _target; }
    Aura* GetBase() const { return _base; }
    AuraRemoveMode GetRemoveMode() const { return _removeMode; }
    void SetRemoveMode(AuraRemoveMode mode) { _removeMode = mode; }
    bool HasEffect(uint8 effect) const { return (_effectMask & (1 << effect)) != 0; }
    void ClientUpdate(bool = false) { }
    Unit* const _target;
    Aura* const _base;
    AuraRemoveMode _removeMode = AURA_REMOVE_NONE;
    uint8 _effectsToApply;
    uint8 _effectMask;
};

// ---------------------------------------------------------------- Aura / AuraEffect
class AuraEffect
{
public:
    AuraEffect(Aura* base, SpellEffectInfo const& info) : m_base(base), m_spellInfo(nullptr), m_effectInfo(info) { }
    Aura* GetBase() const { return m_base; }
    uint8 GetEffIndex() const { return uint8(m_effectInfo.EffectIndex); }
    SpellEffectInfo const& GetSpellEffectInfo() const { return m_effectInfo; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    AuraType GetAuraType() const { return m_effectInfo.ApplyAuraName; }
    int32 GetPeriod() const { return _period; }
    void ResetTicks() { _ticksDone = 0; }                         // SpellAuraEffects.h:89 (copied)
    template <typename Container> void GetApplicationList(Container& applicationContainer) const;
    void Update(uint32 diff, Unit* caster);
    uint32 GetTotalTicks() const;
    void ResetPeriodic(bool resetPeriodicTimer = false);
    void CalculatePeriodic(Unit* caster, bool resetPeriodicTimer = true, bool load = false);
    void PeriodicTick(AuraApplication* aurApp, Unit* caster) const;
    SpellEffectValue CalculateAmount(Unit* caster);
    void ChangeAmount(SpellEffectValue newAmount, bool mark = true, bool onStackOrReapply = false, AuraEffect const* triggeredBy = nullptr);

    Aura* const m_base;
    SpellInfo const* m_spellInfo;
    SpellEffectInfo m_effectInfo;
    SpellEffectValue _amount = 0;
    int32 _periodicTimer = 0;
    int32 _period = 0;
    uint32 _ticksDone = 0;
    bool m_isPeriodic = false;
};

class ChargeDropEvent : public BasicEvent { };

class Aura
{
public:
    typedef std::unordered_map<ObjectGuid, AuraApplication*> ApplicationMap;
    virtual ~Aura();
    uint32 GetId() const { return m_spellInfo->Id; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    WorldObject* GetOwner() const { return m_owner; }
    Unit* GetUnitOwner() const;
    Unit* GetCaster() const;
    ObjectGuid GetCasterGUID() const { return m_casterGuid; }
    int32 GetMaxDuration() const { return m_maxDuration; }
    void SetMaxDuration(int32 duration) { m_maxDuration = duration; }
    int32 GetDuration() const { return m_duration; }
    int32 CalcMaxDuration() const { return m_driverBase; }         // cut point (Track B)
    uint8 GetStackAmount() const { return m_stackAmount; }
    bool IsRemoved() const { return m_isRemoved; }
    bool IsPassive() const { return m_spellInfo->Passive; }
    bool IsDeathPersistent() const { return m_spellInfo->DeathPersistent; }
    bool IsSingleTarget() const { return false; }
    void UnregisterSingleTarget() { std::abort(); }
    bool IsExpired() const;
    bool IsPermanent() const;
    std::vector<AuraEffect*> const& GetAuraEffects() const { return m_effects; }
    ApplicationMap const& GetApplicationMap() const { return m_applications; }
    AuraApplication* GetApplicationOfTarget(ObjectGuid guid) const { auto i = m_applications.find(guid); return i == m_applications.end() ? nullptr : i->second; }
    void GetApplicationVector(std::vector<AuraApplication*>& out) const { for (auto const& [g, a] : m_applications) out.push_back(a); }
    uint32 GetEffectMask() const { uint32 m = 0; for (AuraEffect* e : m_effects) m |= 1u << e->GetEffIndex(); return m; }
    void SetNeedClientUpdateForTargets() const { }
    void HandleAuraSpecificMods(AuraApplication const*, Unit*, bool, bool) { }
    void CallScriptEffectUpdatePeriodicHandlers(AuraEffect*) { }
    void CallScriptEffectCalcPeriodicHandlers(AuraEffect const*, bool&, int32&) { }
    void UpdateTargetMap(Unit*, bool = true) { }
    void _UnapplyForTarget(Unit* target, Unit* caster, AuraApplication* auraApp);
    uint8 CalcMaxCharges() const { return CalcMaxCharges(GetCaster()); }

    void UpdateOwner(uint32 diff, WorldObject* owner);
    void Update(uint32 diff, Unit* caster);
    void _Remove(AuraRemoveMode removeMode);
    virtual void Remove(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT) = 0;
    void SetDuration(int32 duration, bool withMods = false);
    void RefreshDuration(bool withMods = false);
    void RefreshTimers(bool resetPeriodicTimer);
    void SetCharges(uint8 charges);
    uint8 CalcMaxCharges(Unit* caster) const;
    void SetStackAmount(uint8 stackAmount);
    uint32 CalcMaxStackAmount() const;
    bool ModStackAmount(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT, bool resetPeriodicTimer = true);
    void _DeleteRemovedApplications();

    SpellInfo const* m_spellInfo = nullptr;
    WorldObject* m_owner = nullptr;
    ObjectGuid m_casterGuid;
    int32 m_driverBase = 0;
    int32 m_maxDuration = 0;
    int32 m_duration = 0;
    int32 m_timeCla = 0;
    std::vector<SpellPowerEntry const*> m_periodicCosts;
    int32 m_updateTargetMapInterval = 0;
    uint8 m_procCharges = 0;
    uint8 m_stackAmount = 1;
    bool m_isRemoved = false;
    bool m_isUsingCharges = false;
    ChargeDropEvent* m_dropEvent = nullptr;
    std::shared_ptr<Aura> m_scriptRef;
    std::vector<AuraEffect*> m_effects;
    ApplicationMap m_applications;
    std::vector<AuraApplication*> _removedApplications;
    std::string m_name;
    int m_gen = 0;
};

class UnitAura : public Aura
{
public:
    void Remove(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT) override;
};

// ---------------------------------------------------------------- Spell
struct SpellValue { std::optional<int32> Duration; float DurationMul = 1.0f; int32 AuraStackAmount = 1; };
struct TargetInfo { int32 AuraDuration = 0; UnitAura* HitAura = nullptr; bool Positive = false; };

class Spell
{
public:
    Spell(WorldObject* caster, std::vector<std::string> action) : m_caster(caster), m_originalCaster(nullptr), m_action(std::move(action)) { m_spellValue = &m_value; m_spellInfo = &m_info; }
    SpellState getState() const { return m_spellState; }
    void update(uint32 difftime) { UpdatePreparing(difftime); }
    void UpdatePreparing(uint32 difftime);
    void CommitHitDuration(Unit* caster, Unit* unit, TargetInfo& hitInfo, bool refresh);
    bool IsDeletable() const { return true; }
    uint64 GetDelayStart() const { return 0; }
    void SetDelayStart(uint64) { }
    uint64 handle_delayed(uint64) { std::abort(); }
    uint64 GetDelayMoment() const { return 0; }
    WorldObject* GetCaster() const { return m_caster; }
    void cancel() { m_spellState = SPELL_STATE_FINISHED; }
    void cast(bool skipCheck = false);

    WorldObject* m_caster;
    Unit* m_originalCaster;
    SpellValue m_value;
    SpellValue* m_spellValue;
    SpellInfo m_info;
    SpellInfo const* m_spellInfo;
    int32 m_timer = 0;
    int32 m_casttime = 0;
    SpellState m_spellState = SPELL_STATE_PREPARING;
    std::vector<std::string> m_action;
    void SetReferencedFromCurrent(bool) { }
};

class SpellEvent : public BasicEvent
{
public:
    explicit SpellEvent(Spell* spell) : m_Spell(spell) { }
    bool Execute(uint64 e_time, uint32 p_time) override;
    bool IsDeletable() const override { return m_Spell->IsDeletable(); }
    Spell* m_Spell;
};

// ---------------------------------------------------------------- Unit
namespace ObjectAccessor { inline WorldObject* GetWorldObject(WorldObject const&, ObjectGuid) { return reinterpret_cast<WorldObject*>(1); } }

class Unit : public WorldObject
{
public:
    Unit* ToUnit() { return this; }
    Player* GetSpellModOwner() const { return nullptr; }
    void ModSpellDurationTime(SpellInfo const*, int32&, Spell* = nullptr) const { }
    int32 ModSpellDuration(SpellInfo const*, WorldObject const*, int32 duration, bool, uint32) const { return duration; }
    bool HasAura(uint32) const { return false; }
    uint32 GetMaxPower(Powers) const { return 0; }
    uint64 GetMaxHealth() const { return 0; }
    uint64 GetHealth() const { return 0; }
    void ModifyHealth(int64) { }
    void ModifyPower(Powers, int32) { }
    int32 GetPower(Powers) const { return 0; }
    void _UpdateAutoRepeatSpell() { }

    void _DeleteRemovedAuras();
    void _UpdateSpells(uint32 time);
    void RemoveOwnedAura(AuraMap::iterator& i, AuraRemoveMode removeMode);
    void RemoveOwnedAura(Aura* aura, AuraRemoveMode removeMode);
    void RemoveAllAurasOnDeath();
    void _UnapplyAura(AuraApplication* aurApp, AuraRemoveMode removeMode);
    void _UnapplyAura(AuraApplicationMap::iterator& i, AuraRemoveMode removeMode);

    UnitData m_unitDataStore;
    UnitData* m_unitData = &m_unitDataStore;
    SpellHistory m_history;
    SpellHistory* _spellHistory = &m_history;
    std::array<Spell*, CURRENT_MAX_SPELL> m_currentSpells = {};
    AuraMap m_ownedAuras;
    AuraApplicationMap m_appliedAuras;
    AuraList m_removedAuras;
    AuraMap::iterator m_auraUpdateIterator;
    uint32 m_removedAurasCount = 0;
    std::vector<AuraApplication*> m_visibleAurasToUpdate;
    GameObjectList m_gameObj;
    bool m_isPlayer = false;
    bool m_alive = true;
};

Unit* WorldObject::ToUnit() { return static_cast<Unit*>(this); }
Unit* Aura::GetUnitOwner() const { return static_cast<Unit*>(m_owner); }

static std::map<std::string, Unit*> gUnits;
static std::vector<Unit*> gOrder;
struct AuraTemplate
{
    std::string owner;
    int32 base = 0;
    SpellInfo info;
    std::vector<std::vector<std::string>> onTick;
    int generations = 0;
};
static std::map<std::string, AuraTemplate> gTemplates;
static std::map<std::string, UnitAura*> gLive;              // current generation object of each template
static std::map<std::string, std::string> gFinal;           // state snapshot of the last deleted generation

static std::string AuraState(Aura const* a)
{
    std::string eff;
    for (AuraEffect* e : a->m_effects)
        eff += (eff.empty() ? "" : ",") + std::string("{\"timer\":") + std::to_string(e->_periodicTimer) + ",\"period\":" +
            std::to_string(e->_period) + ",\"ticks\":" + std::to_string(e->_ticksDone) + "}";
    return "{\"gen\":" + std::to_string(a->m_gen) + ",\"duration\":" + std::to_string(a->m_duration) + ",\"max\":" +
        std::to_string(a->m_maxDuration) + ",\"stack\":" + std::to_string(int(a->m_stackAmount)) + ",\"removed\":" +
        (a->m_isRemoved ? "true" : "false") + ",\"effects\":[" + eff + "]}";
}

Aura::~Aura()
{
    gFinal[m_name] = AuraState(this);
    auto i = gLive.find(m_name);
    if (i != gLive.end() && i->second == this)
        gLive.erase(i);
}
static std::map<uint64, Unit*> gByGuid;

Unit* Aura::GetCaster() const { auto i = gByGuid.find(m_casterGuid.v); return i == gByGuid.end() ? nullptr : i->second; }

// Cut point: Unit::_UnapplyAura(AuraApplicationMap::iterator&, AuraRemoveMode) (Unit.cpp:3601) reduced to
// its container bookkeeping; Aura::_UnapplyForTarget (SpellAuras.cpp:606) reduced to m_applications / _removedApplications.
void Aura::_UnapplyForTarget(Unit* target, Unit*, AuraApplication* auraApp)
{
    m_applications.erase(target->GetGUID());
    _removedApplications.push_back(auraApp);
}

void Unit::_UnapplyAura(AuraApplicationMap::iterator& i, AuraRemoveMode removeMode)
{
    AuraApplication* aurApp = i->second;
    ASSERT(!aurApp->GetRemoveMode());
    aurApp->SetRemoveMode(removeMode);
    Aura* aura = aurApp->GetBase();
    ++m_removedAurasCount;
    m_appliedAuras.erase(i);
    aura->_UnapplyForTarget(this, nullptr, aurApp);
    aurApp->_effectMask = 0;
    Log("unapply", m_name, aura->m_name, "\"mode\":" + std::to_string(int(removeMode)));
    i = m_appliedAuras.begin();
}

SpellEffectValue AuraEffect::CalculateAmount(Unit*) { return SpellEffectValue(m_base->GetStackAmount()); }
void AuraEffect::ChangeAmount(SpellEffectValue newAmount, bool, bool, AuraEffect const*) { _amount = newAmount; }

static void RunAction(std::vector<std::string> const& a, size_t from);

void AuraEffect::PeriodicTick(AuraApplication* aurApp, Unit*) const
{
    Log("tick", aurApp->GetTarget()->m_name, m_base->m_name,
        "\"eff\":" + std::to_string(GetEffIndex()) + ",\"n\":" + std::to_string(_ticksDone) + ",\"stack\":" + std::to_string(m_base->GetStackAmount()));
    auto const& act = gTemplates.at(m_base->m_name).onTick.at(GetEffIndex());
    if (!act.empty())
        RunAction(act, 0);
}

void Spell::cast(bool)
{
    Log("cast", m_caster->m_name, "", "\"timer\":" + std::to_string(m_timer));
    m_spellState = SPELL_STATE_FINISHED;
    RunAction(m_action, 0);
}

#include "tc_order_bodies.inc"

// ---------------------------------------------------------------- driver
static uint64 gGuid = 1;

static UnitAura* Instantiate(std::string const& name)
{
    AuraTemplate& t = gTemplates.at(name);
    UnitAura* a = new UnitAura();
    a->m_name = name;
    a->m_gen = ++t.generations;
    a->m_owner = gUnits.at(t.owner);
    a->m_casterGuid = a->m_owner->GetGUID();
    a->m_driverBase = t.base;
    a->m_spellInfo = &t.info;
    for (SpellEffectInfo const& info : t.info.Effects)
    {
        AuraEffect* e = new AuraEffect(a, info);
        e->m_spellInfo = &t.info;
        a->m_effects.push_back(e);
    }
    gLive[name] = a;
    return a;
}

static void CreateAura(UnitAura* aura)
{
    // Minimal Aura::Create + Unit::_AddAura/_CreateAuraApplication/_ApplyAura bookkeeping (cut point):
    // m_ownedAuras.emplace (Unit.cpp:3452), application registration, m_duration = m_maxDuration (SpellAuras.cpp:497-498),
    // CalculatePeriodic(caster, resetPeriodicTimer=true, load=false) per effect (SpellAuras.cpp _InitEffects path).
    Unit* owner = aura->GetUnitOwner();
    aura->m_maxDuration = aura->CalcMaxDuration();
    aura->m_duration = aura->m_maxDuration;
    for (AuraEffect* eff : aura->m_effects)
        eff->CalculatePeriodic(aura->GetCaster(), true, false);
    owner->m_ownedAuras.emplace(aura->GetId(), aura);
    uint8 mask = uint8(aura->GetEffectMask());
    AuraApplication* app = new AuraApplication(owner, aura, mask);
    aura->m_applications[owner->GetGUID()] = app;
    owner->m_appliedAuras.emplace(aura->GetId(), app);
    Log("create", owner->m_name, aura->m_name, "\"gen\":" + std::to_string(aura->m_gen) + ",\"duration\":" + std::to_string(aura->m_duration));
}

static void RunAction(std::vector<std::string> const& a, size_t from)
{
    std::string const& verb = a.at(from);
    if (verb == "create")
        CreateAura(Instantiate(a.at(from + 1)));
    else if (verb == "kill")
    {
        Unit* u = gUnits.at(a.at(from + 1));
        Log("death", u->m_name, "");
        u->m_alive = false;
        u->RemoveAllAurasOnDeath();
    }
    else if (verb == "remove")
    {
        auto i = gLive.find(a.at(from + 1));
        if (i == gLive.end() || i->second->IsRemoved())
            Log("remove-miss", "", a.at(from + 1));
        else
            i->second->Remove(AURA_REMOVE_BY_ENEMY_SPELL);
    }
    else if (verb == "refresh")
    {
        auto live = gLive.find(a.at(from + 1));
        bool reset = std::stoi(a.at(from + 2)) != 0;
        int32 hit = std::stoi(a.at(from + 3));
        if (live == gLive.end() || live->second->IsRemoved())
        {
            // Unit::GetOwnedAura finds nothing (removed auras are no longer in m_ownedAuras): the
            // real path would Create a new generation; the driver logs the miss and creates.
            Log("refresh-miss", "", a.at(from + 1));
            CreateAura(Instantiate(a.at(from + 1)));
            return;
        }
        UnitAura* aura = live->second;
        // Unit::_TryStackingOrRefreshingExistingAura tail (Unit.cpp:3441) then Aura::TryRefreshStackOrCreate
        // (*IsRefresh = true, SpellAuras.cpp:372-373) then the DoSpellEffectHit duration block.
        aura->ModStackAmount(1, AURA_REMOVE_BY_DEFAULT, reset);
        Spell spell(aura->GetCaster(), {});
        spell.m_originalCaster = aura->GetCaster();
        spell.m_info = *aura->GetSpellInfo();
        TargetInfo hitInfo;
        hitInfo.AuraDuration = hit;
        hitInfo.HitAura = aura;
        spell.CommitHitDuration(aura->GetCaster() ? aura->GetCaster() : aura->GetUnitOwner(), aura->GetUnitOwner(), hitInfo, true);
        std::string eff;
        for (AuraEffect* e : aura->m_effects)
            eff += (eff.empty() ? "" : ",") + std::string("[") + std::to_string(e->_periodicTimer) + "," + std::to_string(e->_ticksDone) + "]";
        Log("refresh", aura->GetUnitOwner()->m_name, aura->m_name,
            "\"duration\":" + std::to_string(aura->m_duration) + ",\"max\":" + std::to_string(aura->m_maxDuration) +
            ",\"stack\":" + std::to_string(aura->m_stackAmount) + ",\"effects\":[" + eff + "]");
    }
    else
        std::abort();
}

int main()
{
    std::map<uint64, std::vector<std::vector<std::string>>> session;
    std::string line;
    std::vector<std::string> out;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::vector<std::string> w;
        for (std::string s; in >> s;)
            w.push_back(s);
        if (w.empty())
            continue;
        if (w[0] == "unit")
        {
            Unit* u = new Unit();
            u->m_name = w[1];
            u->m_isPlayer = w[2] == "1";
            u->m_unitData->ModCastingSpeed = std::stof(w[3]);
            u->m_guid.v = gGuid++;
            gUnits[w[1]] = u;
            gByGuid[u->m_guid.v] = u;
            gOrder.push_back(u);
        }
        else if (w[0] == "aura")
        {
            AuraTemplate& t = gTemplates[w[1]];
            t.owner = w[2];
            t.info.Id = std::stoul(w[3]);
            t.base = std::stoi(w[4]);
            t.info.StackAmount = std::stoi(w[5]);
            int flags = std::stoi(w[6]);
            if (flags & 1) t.info.Attributes[5] |= SPELL_ATTR5_EXTRA_INITIAL_PERIOD;
            if (flags & 2) t.info.Attributes[13] |= SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION;
            if (flags & 4) t.info.Attributes[1] |= SPELL_ATTR1_AURA_UNIQUE;
            if (flags & 8) t.info.Attributes[5] |= SPELL_ATTR5_AURA_UNIQUE_PER_CASTER;
            if (flags & 16) t.info.Passive = true;
            if (flags & 32) t.info.DeathPersistent = true;
            if (flags & 64) t.info.Attributes[5] |= SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC;
        }
        else if (w[0] == "effect")
        {
            AuraTemplate& t = gTemplates.at(w[1]);
            SpellEffectInfo info;
            info.EffectIndex = uint32(t.info.Effects.size());
            info.ApplyAuraPeriod = std::stoi(w[2]);
            info.ApplyAuraName = info.ApplyAuraPeriod ? SPELL_AURA_PERIODIC_DAMAGE : SPELL_AURA_MOD_STAT;
            t.info.Effects.push_back(info);
            t.onTick.emplace_back();
        }
        else if (w[0] == "ontick")
            gTemplates.at(w[1]).onTick.at(std::stoul(w[2])).assign(w.begin() + 3, w.end());
        else if (w[0] == "at")
            session[std::stoull(w[1])].push_back(std::vector<std::string>(w.begin() + 2, w.end()));
        else if (w[0] == "cast")
        {
            std::vector<std::string> s = { "__cast", w[1], w[2] };
            s.insert(s.end(), w.begin() + 4, w.end());
            session[std::stoull(w[3])].push_back(s);
        }
        else if (w[0] == "run")
        {
            uint32 diff = std::stoul(w[1]);
            uint64 count = std::stoull(w[2]);
            static uint64 tick = 0;
            for (uint64 n = 0; n < count; ++n)
            {
                ++tick;
                gNow += diff;
                gPhase = "session";
                for (auto const& act : session[tick])
                {
                    if (act[0] == "__cast")
                    {
                        Unit* caster = gUnits.at(act[1]);
                        Spell* spell = new Spell(caster, std::vector<std::string>(act.begin() + 3, act.end()));
                        spell->m_casttime = std::stoi(act[2]);
                        spell->m_timer = spell->m_casttime;              // Spell::ReSetTimer (cut point)
                        // Spell.cpp:3466-3467 (copied): create and add update event for this spell
                        SpellEvent* _spellEvent = new SpellEvent(spell);
                        spell->m_caster->m_Events.AddEvent(_spellEvent, spell->m_caster->m_Events.CalculateTime(1ms));
                        Log("prepare", caster->m_name, "", "\"cast_time\":" + std::to_string(spell->m_casttime));
                    }
                    else
                        RunAction(act, 0);
                }
                for (Unit* u : gOrder)
                {
                    gPhase = "events";
                    u->m_Events.Update(diff);                           // WorldObject::Update (Object.cpp:247)
                    gPhase = "spells";
                    u->_UpdateSpells(diff);                             // Unit::Update (Unit.cpp:433)
                    if (u->m_isPlayer)
                    {
                        gPhase = "melee";
                        std::string present;
                        for (auto const& [id, app] : u->m_appliedAuras)
                            present += (present.empty() ? "\"" : ",\"") + app->GetBase()->m_name + "\"";
                        Log("swing-point", u->m_name, "", "\"auras\":[" + present + "]");  // Player::Update DoMeleeAttackIfReady (Player.cpp:1012)
                    }
                }
            }
        }
    }
    std::printf("{\"log\":[");
    for (size_t i = 0; i < gLog.size(); ++i)
        std::printf("%s%s", i ? "," : "", gLog[i].c_str());
    std::printf("],\"auras\":{");
    bool first = true;
    for (auto const& [name, t] : gTemplates)
    {
        auto live = gLive.find(name);
        std::string state = live != gLive.end() ? AuraState(live->second) : (gFinal.count(name) ? gFinal[name] : "null");
        std::printf("%s\"%s\":{\"generations\":%d,\"deleted\":%s,\"state\":%s}", first ? "" : ",", name.c_str(), t.generations,
                    live == gLive.end() ? "true" : "false", state.c_str());
        first = false;
    }
    std::printf("}}\n");
    return 0;
}
