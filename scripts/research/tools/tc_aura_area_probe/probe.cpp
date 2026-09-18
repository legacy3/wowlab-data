// Differential probe for the area-aura recipient lifecycle model (aura_lifecycle/recipients.py, Track F).
//
// tc_area_bodies.inc is pulled verbatim out of the sibling TrinityCore checkout by extract.py:
// Aura::UpdateOwner, Aura::Update, Aura::UpdateTargetMap, Aura::_Remove, AuraEffect::Update,
// AuraEffect::ResetPeriodic, AuraEffect::GetTotalTicks, AuraEffect::GetApplicationList,
// Unit::RemoveOwnedAura(iterator) and the two aura loops of Unit::_UpdateSpells.  This file supplies
// only the scaffolding the bodies reference, with Trinity's member names.  BOUNDED probe: one UnitAura,
// one owner, no caster object (GetCaster -> nullptr: no spell mods, no periodic power costs).
//
// Cut points (semantics stated, not extracted):
//   UnitAura::FillTargetMap   -> static application of the owner (emplace) then every driver `area`
//                                unit whose interval contains the current time (|= area mask).
//                                The range / relation / alive checks are the driver's (targeting pass).
//   Unit::_CreateAuraApplication / _ApplyAura / _UnapplyAura -> register / set effect mask / unregister
//                                + log (no effect handlers).  _UnapplyAura mirrors _UnapplyForTarget's
//                                bookkeeping (erase from m_applications, push to _removedApplications).
//   AuraApplication::UpdateApplyEffectMask -> replace masks + log.
//   CanBeAppliedOn / CanStackWith / IsImmunedToSpell(Effect) / IsHighestExclusiveAura / IsInFlight
//                             -> pass (single aura, no immunity).
//   AuraEffect::PeriodicTick -> log (time, effect, tick number, target).
//   CallScriptEffectUpdatePeriodicHandlers -> no-op.
//
// Protocol (stdin, whitespace separated):
//   world <tick_ms> <until_ms>
//   aura <spellId> <maxDuration> <passive> <extraInitialPeriod> <creation: 0 spell-hit | 1 apply-now> <staticMask>
//   effect <idx> <periodic 0|1> <period> <area 0|1>
//   area <unit> <from> <to|-1>
//   run                       -> one JSON line {"events": [...]}
//   reset

#include "Define.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <list>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#define ABORT() std::abort()
#define ASSERT(cond, ...) do { if (!(cond)) std::abort(); } while (0)
#define TC_LOG_FATAL(...) ((void)0)
#define TC_LOG_ERROR(...) ((void)0)
#define TC_LOG_DEBUG(...) ((void)0)

enum AuraRemoveMode   // SpellAuraDefines.h:55 (copied)
{
    AURA_REMOVE_NONE = 0,
    AURA_REMOVE_BY_DEFAULT = 1,
    AURA_REMOVE_BY_INTERRUPT,
    AURA_REMOVE_BY_CANCEL,
    AURA_REMOVE_BY_ENEMY_SPELL,
    AURA_REMOVE_BY_EXPIRE,
    AURA_REMOVE_BY_DEATH
};
static char const* const REMOVE_MODE[] = { "NONE", "BY_DEFAULT", "INTERRUPT", "CANCEL", "ENEMY_SPELL", "EXPIRE", "DEATH" };

enum AuraObjectType { UNIT_AURA_TYPE, DYNOBJ_AURA_TYPE };
enum SpellAttr2 : uint32 { SPELL_ATTR2_NO_TARGET_PER_SECOND_COSTS = 0x00000800 };
enum SpellAttr5 : uint32 { SPELL_ATTR5_EXTRA_INITIAL_PERIOD = 0x00000200 };
enum Powers : int8 { POWER_HEALTH = -2 };

template <class T> T CalculatePct(T base, float pct) { return T(base * pct / 100.0f); }

struct ObjectGuid
{
    uint64 v = 0;
    bool IsEmpty() const { return v == 0; }
    bool operator==(ObjectGuid const& o) const { return v == o.v; }
    bool operator!=(ObjectGuid const& o) const { return v != o.v; }
    bool operator<(ObjectGuid const& o) const { return v < o.v; }
};
template <> struct std::hash<ObjectGuid> { size_t operator()(ObjectGuid const& g) const { return std::hash<uint64>()(g.v); } };

struct SpellPowerEntry { int32 RequiredAuraSpellID = 0; int32 ManaPerSecond = 0; int8 PowerType = 0; float PowerPctPerSecond = 0.0f; };
struct SpellEffectInfo { uint32 EffectIndex = 0; };
struct SpellInfo
{
    uint32 Id = 0;
    bool extraInitial = false;
    std::vector<SpellEffectInfo> effects;
    std::vector<SpellEffectInfo> const& GetEffects() const { return effects; }
    bool IsChanneled() const { return false; }
    bool HasAttribute(SpellAttr2) const { return false; }
    bool HasAttribute(SpellAttr5 a) const { return a == SPELL_ATTR5_EXTRA_INITIAL_PERIOD && extraInitial; }
};

struct Map { uint32 GetId() const { return 0; } };
static Map gMap;
static int32 gNow = 0;
static std::ostringstream gOut;
static bool gFirst = true;

static void emit(std::string const& body)
{
    gOut << (gFirst ? "" : ",") << "{\"t\":" << gNow << "," << body << "}";
    gFirst = false;
}

class Unit;
class Aura;
class AuraEffect;
struct Spell;
struct Player
{
    Spell* FindCurrentSpellBySpellId(uint32) const { return nullptr; }
    void SetSpellModTakingSpell(Spell*, bool) { }
};

class WorldObject
{
public:
    virtual ~WorldObject() = default;
    ObjectGuid guid;
    std::string name;
    ObjectGuid GetGUID() const { return guid; }
    std::string const& GetName() const { return name; }
    bool IsInWorld() const { return true; }
    Map* GetMap() const { return &gMap; }
    bool IsSelfOrInSameMap(WorldObject const*) const { return true; }
    bool IsInMap(WorldObject const*) const { return true; }
};

namespace ObjectAccessor { WorldObject* GetWorldObject(WorldObject const&, ObjectGuid) { return nullptr; } }

class AuraApplication
{
public:
    Unit* _target;
    Aura* _base;
    AuraRemoveMode _removeMode = AURA_REMOVE_NONE;
    uint32 _effectsToApply;
    uint32 _effectMask = 0;
    AuraApplication(Unit* t, Aura* b, uint32 m) : _target(t), _base(b), _effectsToApply(m) { }
    Unit* GetTarget() const { return _target; }
    Aura* GetBase() const { return _base; }
    uint32 GetEffectMask() const { return _effectMask; }
    uint32 GetEffectsToApply() const { return _effectsToApply; }
    bool HasEffect(uint8 i) const { return (_effectMask & (1 << i)) != 0; }
    AuraRemoveMode GetRemoveMode() const { return _removeMode; }
    void SetRemoveMode(AuraRemoveMode m) { _removeMode = m; }
    void UpdateApplyEffectMask(uint32 newEffMask, bool canHandleNewEffects);
};

class Unit : public WorldObject
{
public:
    typedef std::multimap<uint32, Aura*> AuraMap;
    typedef std::multimap<uint32, AuraApplication*> AuraApplicationMap;
    AuraMap m_ownedAuras;
    AuraMap::iterator m_auraUpdateIterator;
    std::list<Aura*> m_removedAuras;
    AuraApplicationMap m_appliedAuras;

    AuraApplicationMap& GetAppliedAuras() { return m_appliedAuras; }
    bool IsImmunedToSpell(SpellInfo const*, uint32, Unit*, bool = false) const { return false; }
    bool IsImmunedToSpellEffect(SpellInfo const*, SpellEffectInfo const&, Unit*, bool = false) const { return false; }
    bool IsHighestExclusiveAura(Aura const*, bool = false) { return true; }
    bool IsInFlight() const { return false; }
    Player* GetSpellModOwner() const { return nullptr; }
    bool HasAura(uint32) const { return false; }
    uint32 GetMaxPower(Powers) const { return 0; }
    uint64 GetMaxHealth() const { return 0; }
    uint64 GetHealth() const { return 0; }
    void ModifyHealth(int64) { }
    int32 GetPower(Powers) const { return 0; }
    void ModifyPower(Powers, int32) { }

    AuraApplication* _CreateAuraApplication(Aura* aura, uint32 effMask);
    void _ApplyAura(AuraApplication* aurApp, uint32 effMask);
    void _UnapplyAura(AuraApplication* aurApp, AuraRemoveMode removeMode);
    void RemoveOwnedAura(AuraMap::iterator& i, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT);
    void UpdateSpellsAuraLoops(uint32 time);
};
typedef Unit::AuraMap AuraMap;
typedef Unit::AuraApplicationMap AuraApplicationMap;

struct DropEvent { void ScheduleAbort() { } };

class Aura
{
public:
    typedef std::unordered_map<ObjectGuid, AuraApplication*> ApplicationMap;

    SpellInfo const* m_spellInfo;
    WorldObject* m_owner;
    ObjectGuid m_casterGuid;
    int32 m_maxDuration = 0;
    int32 m_duration = 0;
    int32 m_timeCla = 0;
    int32 m_updateTargetMapInterval = 0;          // SpellAuras.cpp:480
    std::vector<SpellPowerEntry const*> m_periodicCosts;
    ApplicationMap m_applications;
    std::vector<AuraApplication*> _removedApplications;
    bool m_isRemoved = false;
    bool m_passive = false;
    DropEvent* m_dropEvent = nullptr;
    void* m_scriptRef = this;
    std::vector<AuraEffect*> m_effects;
    // driver: FillTargetMap cut point
    uint32 staticMask = 0;
    uint32 areaMask = 0;
    std::vector<std::tuple<Unit*, int32, int32>> area;

    Aura(SpellInfo const* info, WorldObject* owner) : m_spellInfo(info), m_owner(owner), m_casterGuid(owner->GetGUID()) { }

    uint32 GetId() const { return m_spellInfo->Id; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    WorldObject* GetOwner() const { return m_owner; }
    Unit* GetUnitOwner() const { return static_cast<Unit*>(m_owner); }
    ObjectGuid GetCasterGUID() const { return m_casterGuid; }
    Unit* GetCaster() const { return nullptr; }
    bool IsRemoved() const { return m_isRemoved; }
    int32 GetDuration() const { return m_duration; }
    int32 GetMaxDuration() const { return m_maxDuration; }
    bool IsPermanent() const { return GetMaxDuration() == -1; }
    bool IsExpired() const { return !GetDuration() && !m_dropEvent; }   // SpellAuras.h:226 (copied)
    bool IsPassive() const { return m_passive; }
    bool IsSingleTarget() const { return false; }
    void UnregisterSingleTarget() { }
    AuraObjectType GetType() const { return UNIT_AURA_TYPE; }
    ApplicationMap const& GetApplicationMap() { return m_applications; }
    AuraApplication* GetApplicationOfTarget(ObjectGuid guid) const
    {
        auto it = m_applications.find(guid);
        return it == m_applications.end() ? nullptr : it->second;
    }
    std::vector<AuraEffect*> GetAuraEffects() const
    {
        std::vector<AuraEffect*> out;
        for (AuraEffect* e : m_effects) if (e) out.push_back(e);
        return out;
    }
    bool CanBeAppliedOn(Unit*) { return true; }
    bool CanStackWith(Aura const*) const { return true; }
    void CallScriptEffectUpdatePeriodicHandlers(AuraEffect*) { }
    void Remove(AuraRemoveMode = AURA_REMOVE_BY_DEFAULT) { std::abort(); }   // power-cost branch: unreachable (no caster)
    void _DeleteRemovedApplications()
    {
        for (AuraApplication* a : _removedApplications) delete a;
        _removedApplications.clear();
    }
    void FillTargetMap(std::unordered_map<Unit*, uint32>& targets, Unit* /*caster*/)
    {
        Unit* owner = GetUnitOwner();
        if (staticMask)
            targets.emplace(owner, staticMask);
        for (auto const& [unit, from, to] : area)
            if (from <= gNow && (to < 0 || gNow < to))
                targets[unit] |= areaMask;
    }

    void UpdateOwner(uint32 diff, WorldObject* owner);
    void Update(uint32 diff, Unit* caster);
    void UpdateTargetMap(Unit* caster, bool apply = true);
    void _Remove(AuraRemoveMode removeMode);
};

class AuraEffect
{
public:
    Aura* m_base;
    SpellInfo const* m_spellInfo;
    uint8 m_effIndex;
    bool m_isPeriodic;
    int32 _periodicTimer = 0;
    int32 _period;
    uint32 _ticksDone = 0;
    AuraEffect(Aura* base, uint8 idx, bool periodic, int32 period) : m_base(base), m_spellInfo(base->GetSpellInfo()), m_effIndex(idx), m_isPeriodic(periodic), _period(period) { }
    Aura* GetBase() const { return m_base; }
    uint8 GetEffIndex() const { return m_effIndex; }
    void PeriodicTick(AuraApplication* aurApp, Unit* /*caster*/) const
    {
        emit("\"ev\":\"tick\",\"eff\":" + std::to_string(m_effIndex) + ",\"n\":" + std::to_string(_ticksDone) +
             ",\"unit\":\"" + aurApp->GetTarget()->GetName() + "\"");
    }
    template <typename Container> void GetApplicationList(Container& applicationContainer) const;
    uint32 GetTotalTicks() const;
    void ResetPeriodic(bool resetPeriodicTimer = false);
    void Update(uint32 diff, Unit* caster);
};

void AuraApplication::UpdateApplyEffectMask(uint32 newEffMask, bool)
{
    _effectsToApply = newEffMask;
    _effectMask = newEffMask;
    emit("\"ev\":\"mask\",\"unit\":\"" + _target->GetName() + "\",\"mask\":" + std::to_string(newEffMask));
}

AuraApplication* Unit::_CreateAuraApplication(Aura* aura, uint32 effMask)
{
    AuraApplication* app = new AuraApplication(this, aura, effMask);
    m_appliedAuras.insert({ aura->GetId(), app });
    aura->m_applications[GetGUID()] = app;                       // Aura::_ApplyForTarget
    emit("\"ev\":\"apply\",\"unit\":\"" + GetName() + "\",\"mask\":" + std::to_string(effMask) +
         ",\"duration\":" + std::to_string(aura->GetDuration()));
    return app;
}

void Unit::_ApplyAura(AuraApplication* aurApp, uint32 effMask)
{
    aurApp->_effectMask |= effMask;                              // _HandleEffect(i, true) for each bit
}

void Unit::_UnapplyAura(AuraApplication* aurApp, AuraRemoveMode removeMode)
{
    aurApp->SetRemoveMode(removeMode);
    for (auto it = m_appliedAuras.begin(); it != m_appliedAuras.end(); ++it)
        if (it->second == aurApp) { m_appliedAuras.erase(it); break; }
    Aura* aura = aurApp->GetBase();
    aura->m_applications.erase(GetGUID());                       // Aura::_UnapplyForTarget
    aura->_removedApplications.push_back(aurApp);
    aurApp->_effectMask = 0;
    emit("\"ev\":\"unapply\",\"unit\":\"" + GetName() + "\",\"mode\":\"" + REMOVE_MODE[removeMode] + "\"");
}

#include "tc_area_bodies.inc"

struct Scenario
{
    int32 tick = 100, until = 0;
    SpellInfo spell;
    std::map<std::string, std::unique_ptr<Unit>> units;
    uint64 nextGuid = 1;
    int32 maxDuration = 0;
    bool passive = false;
    int creation = 0;
    uint32 staticMask = 0;
    struct Eff { uint32 idx; bool periodic; int32 period; bool area; };
    std::vector<Eff> effects;
    std::vector<std::tuple<std::string, int32, int32>> area;

    Unit* unit(std::string const& n)
    {
        auto& u = units[n];
        if (!u) { u = std::make_unique<Unit>(); u->name = n; u->guid.v = nextGuid++; }
        return u.get();
    }

    void run()
    {
        gOut.str(""); gOut.clear(); gFirst = true; gNow = 0;
        Unit* owner = unit("o");
        auto* aura = new Aura(&spell, owner);
        aura->m_maxDuration = maxDuration;
        aura->m_duration = maxDuration;
        aura->m_passive = passive;
        aura->staticMask = staticMask;
        for (Eff const& e : effects)
        {
            if (aura->m_effects.size() <= e.idx) aura->m_effects.resize(e.idx + 1, nullptr);
            aura->m_effects[e.idx] = new AuraEffect(aura, uint8(e.idx), e.periodic && e.period > 0, e.period);
            aura->m_effects[e.idx]->ResetPeriodic(true);            // ctor -> CalculatePeriodic(reset=true)
            if (e.area) aura->areaMask |= 1u << e.idx;
        }
        for (auto const& [n, from, to] : area)
            aura->area.emplace_back(unit(n), from, to);
        owner->m_ownedAuras.insert({ spell.Id, aura });
        if (creation == 0)
        {
            if (staticMask)
            {
                AuraApplication* app = owner->_CreateAuraApplication(aura, staticMask);
                owner->_ApplyAura(app, staticMask);
            }
        }
        else
            aura->UpdateTargetMap(nullptr, true);                   // ApplyForTargets (SpellAuras.h:210)
        for (gNow = tick; gNow <= until; gNow += tick)
        {
            owner->UpdateSpellsAuraLoops(uint32(tick));
            if (aura->IsRemoved())
            {
                emit("\"ev\":\"removed\"");
                break;
            }
        }
        std::cout << "{\"events\":[" << gOut.str() << "]}" << std::endl;
    }
};

int main()
{
    auto sc = std::make_unique<Scenario>();
    std::string cmd;
    while (std::cin >> cmd)
    {
        if (cmd == "reset")
            sc = std::make_unique<Scenario>();
        else if (cmd == "world")
            std::cin >> sc->tick >> sc->until;
        else if (cmd == "aura")
        {
            int passive, extra;
            std::cin >> sc->spell.Id >> sc->maxDuration >> passive >> extra >> sc->creation >> sc->staticMask;
            sc->passive = passive != 0;
            sc->spell.extraInitial = extra != 0;
        }
        else if (cmd == "effect")
        {
            Scenario::Eff e{};
            int periodic, area;
            std::cin >> e.idx >> periodic >> e.period >> area;
            e.periodic = periodic != 0;
            e.area = area != 0;
            sc->effects.push_back(e);
            if (sc->spell.effects.size() <= e.idx) sc->spell.effects.resize(e.idx + 1);
            for (uint32 i = 0; i < sc->spell.effects.size(); ++i) sc->spell.effects[i].EffectIndex = i;
        }
        else if (cmd == "area")
        {
            std::string n; int32 from, to;
            std::cin >> n >> from >> to;
            sc->area.emplace_back(n, from, to);
        }
        else if (cmd == "run")
            sc->run();
        else
        {
            std::cerr << "unknown command " << cmd << std::endl;
            return 2;
        }
    }
    return 0;
}
