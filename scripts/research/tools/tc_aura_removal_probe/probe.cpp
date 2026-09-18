// Differential probe for aura removal / death / dispel (aura-lifecycle Track E).
//
// tc_removal_{decls,bodies,random}.inc are pulled verbatim out of the sibling TrinityCore checkout
// by extract.py; Random.h is included directly.  This file supplies only the scaffolding the
// bodies reference, with Trinity's member names.  BOUNDED probe: cut points replace engine state.
//
// Cut points (semantics stated, not extracted):
//   Aura::UpdateOwner (SpellAuras.cpp:817)   -> Aura::Update + AuraEffect::Update of each effect
//                                               (UpdateTargetMap / spell-mod taking spell omitted)
//   AuraEffect::PeriodicTick                 -> logs {tick, aura, n, target alive, caster state}
//   AuraApplication::_Remove / _HandleEffect -> log; _HandleEffect clears the effect bit
//   Aura::HandleAuraSpecificMods             -> log (linked spells / family branches not run)
//   Aura::SetStackAmount / RefreshTimers / SetCharges / CalcMax* -> set + log (no amount recalculation)
//   Aura::CallScript*                        -> log
//   Aura::ApplicationMap                     -> std::map (Trinity: std::unordered_map; recipient
//                                               unapply order is hash order there -- storage)
//   SpellInfo::HasAnyAuraInterruptFlag / GetAuraState / IsCooldownStartedOnEvent -> false / 0 / false
//   Unit::GetSpellModOwner -> a stub Player whose ApplySpellMod adds driver `resist` values for
//                            SpellModOp::DispelResistance (nullptr when none); Unit::ToPlayer -> nullptr
//   rand32 -> scripted words (then a fixed LCG); every word consumed is counted.
//   urand / irand / rand_chance: verbatim Random.cpp bodies, called through logging wrappers.
//
// Protocol (stdin, whitespace separated):
//   unit <name> <alive> <inworld> <faction>
//   spell <id> <a0> <a1> <a2> <a3> <a5> <a7> <stackAmount> <dispelType>
//   aura <key> <spellId> <owner> <caster> <duration> <maxDuration> <stacks> <charges> <maxCharges> <period> <positive>
//   app <key> <target> <positive>
//   death <unit>                       -> RemoveAllAurasOnDeath
//   update <unit> <diff>               -> owned-aura update + expiry sweep of Unit::_UpdateSpells
//   dispel <unit> <spellId> <caster> <charges>     -> RemoveAurasDueToSpellByDispel
//   dispellist <unit> <dispeller> <mask> <reflect> -> GetDispellableAuraList
//   dispelloop <unit> <dispeller> <mask> <amount>  -> GetDispellableAuraList + EffectDispel attempt loop
//   resist <spellId> <pct>             (DispelResistance spell-mod result for auras of that spell)
//   setalive <unit> <0|1>, setinworld <unit> <0|1>
//   words <n> <w...>                   (queue rand32 words)
//   rngcount <urand|irand|chance> <lo> <hi> <reps> -> engine words per call
//   state                              -> aura states
//   reset
// Every command prints one JSON line {"cmd":..., "log":[...], ...}.

#include "Define.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <iostream>
#include <list>
#include <map>
#include <memory>
#include <random>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#undef TC_COMMON_API
#define TC_COMMON_API
#define ABORT() do { std::fprintf(stderr, "ABORT %s:%d\n", __FILE__, __LINE__); std::abort(); } while (0)
#define ASSERT(...) do { if (!(__VA_ARGS__)) { std::fprintf(stderr, "ASSERT %s (%s:%d)\n", #__VA_ARGS__, __FILE__, __LINE__); std::abort(); } } while (0)
#define TC_LOG_DEBUG(...) ((void)0)
#define TC_LOG_ERROR(...) ((void)0)

// Callers (Random.h roll_chance templates, bodies) reach the logging wrappers; the verbatim
// Random.cpp definitions become the *_raw functions.
#define irand tc_irand
#define urand tc_urand
#define rand_chance tc_rand_chance
#include "Random.h"
#undef irand
#undef urand
#undef rand_chance

// ---------------------------------------------------------------------------
// rng + log
// ---------------------------------------------------------------------------
static std::deque<uint32> gWords;
static uint64 gWordsUsed = 0;
static uint64 gLcg = 0x2545F4914F6CDD1DULL;
static std::vector<std::string> gLog;

static void logf(std::string const& s) { gLog.push_back(s); }

uint32 rand32()
{
    ++gWordsUsed;
    if (!gWords.empty())
    {
        uint32 v = gWords.front();
        gWords.pop_front();
        return v;
    }
    gLcg = gLcg * 6364136223846793005ULL + 1442695040888963407ULL;
    return uint32(gLcg >> 32);
}

namespace { constexpr RandomEngine engine; }

#define irand tc_irand_raw
#define urand tc_urand_raw
#define rand_chance tc_rand_chance_raw
int32 irand(int32 min, int32 max);
uint32 urand(uint32 min, uint32 max);
float rand_chance();
#include "tc_removal_random.inc"
#undef irand
#undef urand
#undef rand_chance

int32 tc_irand(int32 min, int32 max)
{
    uint64 before = gWordsUsed;
    int32 v = tc_irand_raw(min, max);
    std::ostringstream o; o << "{\"rng\":\"irand\",\"lo\":" << min << ",\"hi\":" << max << ",\"v\":" << v << ",\"words\":" << (gWordsUsed - before) << "}";
    logf(o.str());
    return v;
}

uint32 tc_urand(uint32 min, uint32 max)
{
    uint64 before = gWordsUsed;
    uint32 v = tc_urand_raw(min, max);
    std::ostringstream o; o << "{\"rng\":\"urand\",\"lo\":" << min << ",\"hi\":" << max << ",\"v\":" << v << ",\"words\":" << (gWordsUsed - before) << "}";
    logf(o.str());
    return v;
}

float tc_rand_chance()
{
    uint64 before = gWordsUsed;
    float v = tc_rand_chance_raw();
    std::ostringstream o; o.precision(9); o << "{\"rng\":\"rand_chance\",\"v\":" << v << ",\"words\":" << (gWordsUsed - before) << "}";
    logf(o.str());
    return v;
}

// ---------------------------------------------------------------------------
// scaffolding
// ---------------------------------------------------------------------------
class Aura;
class AuraEffect;
class AuraApplication;
class Unit;
class WorldObject;
class Player;
class SpellInfo;

#include "tc_removal_decls.inc"

struct ObjectGuid
{
    uint64 v = 0;
    bool operator==(ObjectGuid const& o) const { return v == o.v; }
    bool operator!=(ObjectGuid const& o) const { return v != o.v; }
    bool operator<(ObjectGuid const& o) const { return v < o.v; }
    bool IsEmpty() const { return v == 0; }
    std::string ToString() const { return std::to_string(v); }
};

enum TypeID { TYPEID_UNIT = 5, TYPEID_PLAYER = 6 };
enum DeathState { ALIVE = 0, JUST_DIED = 1 };
enum TotemType { TOTEM_PASSIVE = 0 };
enum AuraStateType : uint32 { AURA_STATE_NONE = 0 };
constexpr uint32 PER_CASTER_AURA_STATE_MASK = 0;
enum Powers : int8 { POWER_HEALTH = -2, POWER_MANA = 0 };
enum class SpellModOp : uint8 { DispelResistance = 20 };
enum class CriteriaFailEvent : uint8 { LoseAura = 1 };

template <class T> T CalculatePct(T base, float pct) { return T(base * pct / 100.0f); }
template <class T> void RoundToInterval(T& num, T floor, T ceil) { num = std::min(std::max(num, floor), ceil); }

namespace Trinity::Containers::Lists
{
template <class C, class V> void RemoveUnique(C& c, V const& v) { auto it = std::find(c.begin(), c.end(), v); if (it != c.end()) c.erase(it); }
}

namespace UF { struct UnitData { uint32 AuraState = 0; }; }
struct FieldRef { template <class M> FieldRef ModifyValue(M) const { return {}; } };
struct ValuesStub { template <class M> FieldRef ModifyValue(M) { return {}; } };

struct SpellPowerEntry { int32 RequiredAuraSpellID = 0; int32 ManaPerSecond = 0; int8 PowerType = 0; float PowerPctPerSecond = 0; };

struct SpellHistoryStub { void SendCooldownEvent(SpellInfo const*) { } };
struct ConditionMgrStub { bool IsSpellUsedInSpellClickConditions(uint32) const { return false; } };
static ConditionMgrStub gConditionMgr;
#define sConditionMgr (&gConditionMgr)

struct DispelFailedStub { std::vector<int32> FailedSpells; };

class SpellInfo
{
public:
    uint32 Id = 0;
    uint32 Attributes = 0, AttributesEx = 0, AttributesEx2 = 0, AttributesEx3 = 0, AttributesEx5 = 0, AttributesEx7 = 0;
    uint32 StackAmount = 0;
    uint32 Dispel = 0;

    bool HasAttribute(SpellAttr0 a) const { return (Attributes & a) != 0; }
    bool HasAttribute(SpellAttr1 a) const { return (AttributesEx & a) != 0; }
    bool HasAttribute(SpellAttr2 a) const { return (AttributesEx2 & a) != 0; }
    bool HasAttribute(SpellAttr3 a) const { return (AttributesEx3 & a) != 0; }
    bool HasAttribute(SpellAttr5 a) const { return (AttributesEx5 & a) != 0; }
    bool HasAttribute(SpellAttr7 a) const { return (AttributesEx7 & a) != 0; }
    bool HasAnyAuraInterruptFlag() const { return false; }
    AuraStateType GetAuraState() const { return AURA_STATE_NONE; }
    bool IsCooldownStartedOnEvent() const { return false; }

    bool IsPassive() const;
    bool IsDeathPersistent() const;
    bool IsChanneled() const;
    uint32 GetDispelMask() const;
    static uint32 GetDispelMask(DispelType type);
};

class WorldObject
{
public:
    virtual ~WorldObject() = default;
    ObjectGuid m_guid;
    ObjectGuid GetGUID() const { return m_guid; }
};

static std::map<uint32, int32> gResist;   // spell id -> SpellModOp::DispelResistance flat add (driver `resist`)

class Player : public WorldObject
{
public:
    void UpdateVisibleObjectInteractions(bool, bool, bool, bool) { }
    void FailCriteria(CriteriaFailEvent, uint32) { }
    template <class T> void ApplySpellMod(SpellInfo const* si, SpellModOp op, T& v);
};
static Player gModOwner;

class Totem
{
public:
    uint32 GetSpell() const { return 0; }
    TotemType GetTotemType() const { return TOTEM_PASSIVE; }
    void setDeathState(DeathState) { }
};

class AuraApplication
{
public:
    Unit* _target = nullptr;
    Aura* _base = nullptr;
    AuraRemoveMode _removeMode = AURA_REMOVE_NONE;
    uint32 _effectMask = 0;
    bool _positive = false;

    Unit* GetTarget() const { return _target; }
    Aura* GetBase() const { return _base; }
    AuraRemoveMode GetRemoveMode() const { return _removeMode; }
    void SetRemoveMode(AuraRemoveMode m) { _removeMode = m; }
    bool HasEffect(uint8 i) const { return (_effectMask & (1u << i)) != 0; }
    uint32 GetEffectMask() const { return _effectMask; }
    bool IsPositive() const { return _positive; }
    void _Remove();
    void _HandleEffect(uint8 effIndex, bool apply);
};

class Aura
{
public:
    typedef std::map<ObjectGuid, AuraApplication*> ApplicationMap;   // Trinity: std::unordered_map (cut point)

    virtual ~Aura() = default;
    std::string m_key;
    SpellInfo const* m_spellInfo = nullptr;
    ObjectGuid m_casterGuid;
    Unit* m_owner = nullptr;
    ApplicationMap m_applications;
    bool m_isRemoved = false;
    struct DropEvent { void ScheduleAbort() { } }* m_dropEvent = nullptr;
    void* m_scriptRef = nullptr;
    std::vector<AuraApplication*> _removedApplications;
    int32 m_duration = 0, m_maxDuration = 0, m_timeCla = 0;
    std::vector<SpellPowerEntry const*> m_periodicCosts;
    uint8 m_procCharges = 0, m_maxCharges = 0, m_stackAmount = 1;
    bool m_isUsingCharges = false;
    std::vector<AuraEffect*> m_effects;

    uint32 GetId() const { return m_spellInfo->Id; }
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    ObjectGuid GetCasterGUID() const { return m_casterGuid; }
    Unit* GetCaster() const;
    Unit* GetOwner() const { return m_owner; }
    Unit* GetUnitOwner() const { return m_owner; }
    AuraApplication* GetApplicationOfTarget(ObjectGuid g) const { auto it = m_applications.find(g); return it == m_applications.end() ? nullptr : it->second; }
    ApplicationMap const& GetApplicationMap() const { return m_applications; }
    bool IsRemoved() const { return m_isRemoved; }
    bool IsSingleTarget() const { return false; }
    void UnregisterSingleTarget() { }
    std::vector<AuraEffect*> const& GetAuraEffects() const { return m_effects; }
    int32 GetDuration() const { return m_duration; }
    int32 GetMaxDuration() const { return m_maxDuration; }
    bool IsPermanent() const { return GetMaxDuration() == -1; }
    bool IsExpired() const { return !GetDuration() && !m_dropEvent; }   // SpellAuras.h:226
    bool IsUsingCharges() const { return m_isUsingCharges; }
    uint8 GetCharges() const { return m_procCharges; }
    uint8 GetStackAmount() const { return m_stackAmount; }
    uint8 CalcMaxCharges() const { return m_maxCharges; }
    uint32 CalcMaxStackAmount() const { return m_spellInfo->StackAmount; }
    void SetCharges(uint8 c) { if (m_procCharges == c) return; m_procCharges = c; m_isUsingCharges = c != 0; logf("{\"ev\":\"set_charges\",\"aura\":\"" + m_key + "\",\"v\":" + std::to_string(c) + "}"); }
    void SetStackAmount(uint8 s) { m_stackAmount = s; logf("{\"ev\":\"set_stack\",\"aura\":\"" + m_key + "\",\"v\":" + std::to_string(s) + "}"); }
    void RefreshTimers(bool reset) { logf("{\"ev\":\"refresh_timers\",\"aura\":\"" + m_key + "\",\"reset\":" + (reset ? "true" : "false") + "}"); }
    void SetNeedClientUpdateForTargets() { }
    void CallScriptDispel(DispelInfo*) { logf("{\"ev\":\"on_dispel\",\"aura\":\"" + m_key + "\"}"); }
    void CallScriptAfterDispel(DispelInfo*) { logf("{\"ev\":\"after_dispel\",\"aura\":\"" + m_key + "\"}"); }
    void CallScriptEffectUpdatePeriodicHandlers(AuraEffect*) { }
    void HandleAuraSpecificMods(AuraApplication const* app, Unit* caster, bool apply, bool onReapply);
    void UpdateOwner(uint32 diff, WorldObject* owner);

    virtual void Remove(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT) = 0;

    void _Remove(AuraRemoveMode removeMode);
    void _UnapplyForTarget(Unit* target, Unit* caster, AuraApplication* auraApp);
    void Update(uint32 diff, Unit* caster);
    bool ModCharges(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT);
    bool ModStackAmount(int32 num, AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT, bool resetPeriodicTimer = true);
    bool IsPassive() const;
    bool IsDeathPersistent() const;
    int32 CalcDispelChance(Unit const* auraTarget, bool offensive) const;
};

class UnitAura : public Aura
{
public:
    void Remove(AuraRemoveMode removeMode = AURA_REMOVE_BY_DEFAULT) override;
};

class AuraEffect
{
public:
    Aura* m_base = nullptr;
    SpellInfo const* m_spellInfo = nullptr;
    uint8 m_effIndex = 0;
    bool m_isPeriodic = false;
    int32 _periodicTimer = 0, _period = 0;
    uint32 _ticksDone = 0;

    Aura* GetBase() const { return m_base; }
    uint8 GetEffIndex() const { return m_effIndex; }
    void GetApplicationList(std::vector<AuraApplication*>& out) const
    {
        for (auto const& [g, app] : m_base->GetApplicationMap())
            if (app->HasEffect(m_effIndex))
                out.push_back(app);
    }
    void PeriodicTick(AuraApplication* aurApp, Unit* caster) const;
    void Update(uint32 diff, Unit* caster);
    uint32 GetTotalTicks() const;
};

class Unit : public WorldObject
{
public:
    typedef std::multimap<uint32, Aura*> AuraMap;
    typedef std::pair<AuraMap::iterator, AuraMap::iterator> AuraMapBoundsNonConst;
    typedef std::multimap<uint32, AuraApplication*> AuraApplicationMap;
    typedef std::pair<AuraApplicationMap::iterator, AuraApplicationMap::iterator> AuraApplicationMapBoundsNonConst;
    typedef std::multimap<AuraStateType, AuraApplication*> AuraStateAurasMap;
    typedef std::list<AuraApplication*> AuraApplicationList;

    std::string m_name;
    bool m_alive = true, m_inWorld = true;
    int m_faction = 0;
    AuraMap m_ownedAuras;
    AuraApplicationMap m_appliedAuras;
    std::list<Aura*> m_removedAuras;
    AuraMap::iterator m_auraUpdateIterator;
    uint32 m_removedAurasCount = 0;
    AuraApplicationList m_interruptableAuras;
    AuraStateAurasMap m_auraStateAuras;
    ValuesStub m_values;
    UF::UnitData m_unitData;
    SpellHistoryStub m_history;

    bool IsAlive() const { return m_alive; }
    bool IsFriendlyTo(WorldObject const* o) const;
    AuraMap const& GetOwnedAuras() const { return m_ownedAuras; }
    void UpdateInterruptMask() { }
    void ModifyAuraState(AuraStateType, bool) { }
    TypeID GetTypeId() const { return TYPEID_UNIT; }
    bool IsTotem() const { return false; }
    Totem* ToTotem() { return nullptr; }
    Player* ToPlayer() { return nullptr; }
    void ForceUpdateFieldChange(FieldRef) { }
    Player* GetSpellModOwner() const { return gResist.empty() ? nullptr : &gModOwner; }
    SpellHistoryStub* GetSpellHistory() { return &m_history; }
    bool HasAura(uint32) const { return false; }
    uint32 GetMaxPower(Powers) const { return 0; }
    uint64 GetMaxHealth() const { return 0; }
    uint64 GetHealth() const { return 0; }
    void ModifyHealth(int64) { }
    int32 GetPower(Powers) const { return 0; }
    void ModifyPower(Powers, int32) { }

    void RemoveAllAurasOnDeath();
    void RemoveOwnedAura(AuraMap::iterator& i, AuraRemoveMode removeMode);
    void RemoveOwnedAura(Aura* aura, AuraRemoveMode removeMode);
    void _UnapplyAura(AuraApplicationMap::iterator& i, AuraRemoveMode removeMode);
    void _UnapplyAura(AuraApplication* aurApp, AuraRemoveMode removeMode);
    void RemoveAurasDueToSpellByDispel(uint32 spellId, uint32 dispellerSpellId, ObjectGuid casterGUID, WorldObject* dispeller, uint8 chargesRemoved = 1);
    void GetDispellableAuraList(WorldObject const* caster, uint32 dispelMask, DispelChargesList& dispelList, bool isReflect = false) const;
    void _UpdateSpellsAuras(uint32 time);
};

// registry
static std::map<std::string, std::unique_ptr<Unit>> gUnits;
static std::map<uint64, Unit*> gByGuid;
static std::map<uint32, std::unique_ptr<SpellInfo>> gSpells;
static std::map<std::string, Aura*> gAuras;            // not owned (units delete via m_removedAuras in real code)
static std::vector<std::unique_ptr<Aura>> gAuraStore;
static std::vector<std::unique_ptr<AuraEffect>> gEffectStore;
static std::vector<std::unique_ptr<AuraApplication>> gAppStore;

namespace ObjectAccessor
{
WorldObject* GetWorldObject(WorldObject const&, ObjectGuid g) { auto it = gByGuid.find(g.v); return (it != gByGuid.end() && it->second->m_inWorld) ? it->second : nullptr; }
Unit* GetUnit(WorldObject const&, ObjectGuid g) { auto it = gByGuid.find(g.v); return (it != gByGuid.end() && it->second->m_inWorld) ? it->second : nullptr; }
}

template <class T> void Player::ApplySpellMod(SpellInfo const* si, SpellModOp op, T& v)
{
    if (op == SpellModOp::DispelResistance) { auto it = gResist.find(si->Id); if (it != gResist.end()) v += T(it->second); }
}

bool Unit::IsFriendlyTo(WorldObject const* o) const { auto u = dynamic_cast<Unit const*>(o); return u && u->m_faction == m_faction; }

Unit* Aura::GetCaster() const
{
    // SpellAuras.cpp:557-563
    if (GetOwner()->GetGUID() == GetCasterGUID())
        return GetUnitOwner();
    return ObjectAccessor::GetUnit(*GetOwner(), GetCasterGUID());
}

void AuraApplication::_Remove() { logf("{\"ev\":\"app_remove\",\"aura\":\"" + _base->m_key + "\",\"target\":\"" + _target->m_name + "\"}"); }
void AuraApplication::_HandleEffect(uint8 effIndex, bool apply)
{
    if (!apply) _effectMask &= ~(1u << effIndex);
    logf("{\"ev\":\"handle_effect\",\"aura\":\"" + _base->m_key + "\",\"target\":\"" + _target->m_name + "\",\"eff\":" + std::to_string(effIndex) + ",\"apply\":" + (apply ? "true" : "false") + ",\"mode\":" + std::to_string(int(_removeMode)) + "}");
}
void Aura::HandleAuraSpecificMods(AuraApplication const* app, Unit*, bool apply, bool)
{
    logf("{\"ev\":\"specific_mods\",\"aura\":\"" + m_key + "\",\"target\":\"" + app->GetTarget()->m_name + "\",\"apply\":" + (apply ? "true" : "false") + ",\"mode\":" + std::to_string(int(app->GetRemoveMode())) + "}");
}
void Aura::UpdateOwner(uint32 diff, WorldObject*)
{
    Unit* caster = GetCaster();
    Update(diff, caster);
    for (AuraEffect* effect : GetAuraEffects())
        effect->Update(diff, caster);
}
void AuraEffect::PeriodicTick(AuraApplication* aurApp, Unit* caster) const
{
    std::string cs = !caster ? "absent" : (caster->IsAlive() ? "alive" : "dead");
    logf("{\"ev\":\"tick\",\"aura\":\"" + m_base->m_key + "\",\"target\":\"" + aurApp->GetTarget()->m_name + "\",\"n\":" + std::to_string(_ticksDone) +
         ",\"target_alive\":" + (aurApp->GetTarget()->IsAlive() ? "true" : "false") + ",\"caster\":\"" + cs + "\"}");
}

#define urand tc_urand
#define irand tc_irand
#include "tc_removal_bodies.inc"
#undef urand
#undef irand

// ---------------------------------------------------------------------------
// driver
// ---------------------------------------------------------------------------
static void emit(std::string const& cmd, std::string const& extra = "")
{
    std::cout << "{\"cmd\":\"" << cmd << "\",\"log\":[";
    for (std::size_t i = 0; i < gLog.size(); ++i)
        std::cout << (i ? "," : "") << gLog[i];
    std::cout << "]" << extra << "}\n";
    std::cout.flush();
    gLog.clear();
}

static Aura* findAura(std::string const& key) { auto it = gAuras.find(key); if (it == gAuras.end()) { std::fprintf(stderr, "no aura %s\n", key.c_str()); std::exit(2); } return it->second; }
static Unit* findUnit(std::string const& n) { auto it = gUnits.find(n); if (it == gUnits.end()) { std::fprintf(stderr, "no unit %s\n", n.c_str()); std::exit(2); } return it->second.get(); }

static std::string auraState(Aura* a)
{
    std::ostringstream o;
    o << "{\"aura\":\"" << a->m_key << "\",\"removed\":" << (a->m_isRemoved ? "true" : "false") << ",\"duration\":" << a->m_duration
      << ",\"stacks\":" << int(a->m_stackAmount) << ",\"charges\":" << int(a->m_procCharges) << ",\"apps\":[";
    bool first = true;
    for (auto const& [g, app] : a->m_applications) { o << (first ? "" : ",") << "\"" << app->GetTarget()->m_name << "\""; first = false; }
    o << "],\"ticks\":[";
    first = true;
    for (AuraEffect* e : a->m_effects) { o << (first ? "" : ",") << e->_ticksDone; first = false; }
    o << "]}";
    return o.str();
}

int main()
{
    std::string cmd;
    uint64 nextGuid = 1;
    while (std::cin >> cmd)
    {
        if (cmd == "reset")
        {
            gAuras.clear(); gUnits.clear(); gByGuid.clear(); gSpells.clear(); gWords.clear(); gWordsUsed = 0; gLog.clear(); gResist.clear();
            emit(cmd);
        }
        else if (cmd == "unit")
        {
            auto u = std::make_unique<Unit>();
            std::cin >> u->m_name >> u->m_alive >> u->m_inWorld >> u->m_faction;
            u->m_guid.v = nextGuid++;
            u->m_auraUpdateIterator = u->m_ownedAuras.end();
            gByGuid[u->m_guid.v] = u.get();
            std::string n = u->m_name;
            gUnits[n] = std::move(u);
        }
        else if (cmd == "spell")
        {
            auto s = std::make_unique<SpellInfo>();
            std::cin >> s->Id >> s->Attributes >> s->AttributesEx >> s->AttributesEx2 >> s->AttributesEx3 >> s->AttributesEx5 >> s->AttributesEx7 >> s->StackAmount >> s->Dispel;
            uint32 id = s->Id;
            gSpells[id] = std::move(s);
        }
        else if (cmd == "aura")
        {
            std::string key, owner, caster; uint32 spellId; int32 dur, maxDur, period; int stacks, charges, maxCharges, positive;
            std::cin >> key >> spellId >> owner >> caster >> dur >> maxDur >> stacks >> charges >> maxCharges >> period >> positive;
            auto a = std::make_unique<UnitAura>();
            a->m_key = key; a->m_spellInfo = gSpells.at(spellId).get(); a->m_owner = findUnit(owner); a->m_casterGuid = findUnit(caster)->GetGUID();
            a->m_duration = dur; a->m_maxDuration = maxDur; a->m_stackAmount = uint8(stacks); a->m_procCharges = uint8(charges);
            a->m_maxCharges = uint8(maxCharges); a->m_isUsingCharges = charges != 0;
            auto e = std::make_unique<AuraEffect>();
            e->m_base = a.get(); e->m_spellInfo = a->m_spellInfo; e->m_effIndex = 0; e->m_isPeriodic = period > 0; e->_period = period;
            a->m_effects.push_back(e.get());
            gEffectStore.push_back(std::move(e));
            Unit* o = a->m_owner;
            o->m_ownedAuras.insert(Unit::AuraMap::value_type(spellId, a.get()));
            auto app = std::make_unique<AuraApplication>();
            app->_target = o; app->_base = a.get(); app->_effectMask = 1; app->_positive = positive != 0;
            a->m_applications[o->GetGUID()] = app.get();
            o->m_appliedAuras.insert(Unit::AuraApplicationMap::value_type(spellId, app.get()));
            gAppStore.push_back(std::move(app));
            gAuras[key] = a.get();
            gAuraStore.push_back(std::move(a));
        }
        else if (cmd == "app")
        {
            std::string key, target; int positive;
            std::cin >> key >> target >> positive;
            Aura* a = findAura(key); Unit* t = findUnit(target);
            auto app = std::make_unique<AuraApplication>();
            app->_target = t; app->_base = a; app->_effectMask = 1; app->_positive = positive != 0;
            a->m_applications[t->GetGUID()] = app.get();
            t->m_appliedAuras.insert(Unit::AuraApplicationMap::value_type(a->GetId(), app.get()));
            gAppStore.push_back(std::move(app));
        }
        else if (cmd == "resist") { uint32 id; int32 v; std::cin >> id >> v; gResist[id] = v; }
        else if (cmd == "setalive") { std::string n; int v; std::cin >> n >> v; findUnit(n)->m_alive = v != 0; }
        else if (cmd == "setinworld") { std::string n; int v; std::cin >> n >> v; findUnit(n)->m_inWorld = v != 0; }
        else if (cmd == "death")
        {
            std::string n; std::cin >> n; Unit* u = findUnit(n);
            u->m_alive = false;
            u->RemoveAllAurasOnDeath();
            emit(cmd);
        }
        else if (cmd == "update")
        {
            std::string n; uint32 diff; std::cin >> n >> diff;
            findUnit(n)->_UpdateSpellsAuras(diff);
            emit(cmd);
        }
        else if (cmd == "dispel")
        {
            std::string n, caster; uint32 spellId; int charges; std::cin >> n >> spellId >> caster >> charges;
            Unit* u = findUnit(n);
            u->RemoveAurasDueToSpellByDispel(spellId, 0, findUnit(caster)->GetGUID(), nullptr, uint8(charges));
            emit(cmd);
        }
        else if (cmd == "dispellist" || cmd == "dispelloop")
        {
            std::string n, disp; uint32 mask; int32 x; std::cin >> n >> disp >> mask >> x;
            Unit* u = findUnit(n);
            DispelChargesList lst;
            u->GetDispellableAuraList(findUnit(disp), mask, lst, cmd == "dispellist" ? x != 0 : false);
            std::ostringstream o;
            o << ",\"list\":[";
            for (std::size_t i = 0; i < lst.size(); ++i)
                o << (i ? "," : "") << "{\"aura\":\"" << lst[i].GetAura()->m_key << "\",\"charges\":" << int(lst[i].GetDispelCharges()) << "}";
            o << "]";
            if (cmd == "dispelloop")
            {
                DispelChargesList success;
                DispelFailedStub failed;
                uint64 before = gWordsUsed;
                DispelLoop(lst, lst.size(), x, success, failed);
                o << ",\"success\":[";
                for (std::size_t i = 0; i < success.size(); ++i)
                    o << (i ? "," : "") << "{\"aura\":\"" << success[i].GetAura()->m_key << "\",\"charges\":" << int(success[i].GetDispelCharges()) << "}";
                o << "],\"failed\":[";
                for (std::size_t i = 0; i < failed.FailedSpells.size(); ++i)
                    o << (i ? "," : "") << failed.FailedSpells[i];
                o << "],\"words\":" << (gWordsUsed - before);
            }
            emit(cmd, o.str());
        }
        else if (cmd == "words")
        {
            int n; std::cin >> n;
            for (int i = 0; i < n; ++i) { uint64 w; std::cin >> w; gWords.push_back(uint32(w)); }
        }
        else if (cmd == "rngcount")
        {
            std::string kind; int64 lo, hi; int reps; std::cin >> kind >> lo >> hi >> reps;
            uint64 before = gWordsUsed;
            for (int i = 0; i < reps; ++i)
            {
                if (kind == "urand") tc_urand(uint32(lo), uint32(hi));
                else if (kind == "irand") tc_irand(int32(lo), int32(hi));
                else if (kind == "roll_int") roll_chance(int32(lo));
                else tc_rand_chance();
            }
            gLog.clear();
            emit(cmd, ",\"kind\":\"" + kind + "\",\"reps\":" + std::to_string(reps) + ",\"words\":" + std::to_string(gWordsUsed - before));
        }
        else if (cmd == "state")
        {
            std::ostringstream o; o << ",\"auras\":[";
            bool first = true;
            for (auto const& [k, a] : gAuras) { o << (first ? "" : ",") << auraState(a); first = false; }
            o << "]";
            emit(cmd, o.str());
        }
        else
        {
            std::fprintf(stderr, "unknown command %s\n", cmd.c_str());
            return 2;
        }
    }
    return 0;
}
