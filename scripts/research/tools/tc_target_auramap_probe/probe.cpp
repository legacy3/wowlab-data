// Differential probe for the aura target-map oracle (targeting/auratargets.py, Track J).
//
// tc_auramap_decls.inc / tc_auramap_bodies.inc are pulled verbatim out of the sibling
// TrinityCore checkout by extract.py; Position.h is included directly.  This file supplies
// only the scaffolding the bodies reference, with Trinity's member names.  BOUNDED probe:
// the cut points below replace engine state with driver-supplied values.
//
// Cut points (semantics stated, not extracted):
//   Spell.cpp:9294-9396  WorldObjectSpellTargetCheck ctor / operator(): relation, CheckTarget and
//                        conditions -> driver table `rel <unit> <checkType> <0|1>` (default reject);
//                        every construction is logged (caster, referer, check type).
//   Spell.cpp:2163       Spell::SearchTargets -> visit the driver `visit` list, container-mask filter,
//                        searcher phase filter; the requested search radius is logged.
//   CellImpl.h:190       Cell::VisitAllObjects -> same list (units only), radius logged.
//   GridNotifiers.h:561  UnitListSearcher -> InSamePhase(phaseShift) then check.
//   Unit state           IsAlive / IsInWorld / IsDuringRemoveFromWorld / HasAuraState(BANISHED) /
//                        GetCharmerOrOwner / GetPetGUID / InSamePhase(owner) / IsInFlight /
//                        IsImmunedToSpell / IsImmunedToSpellEffect (effect mask) / IsHighestExclusiveAura /
//                        HasAuraTypeWithMiscvalue(label) / IsSelfOrInSameMap / GetSpellOtherImmunityMask
//                        -> per-unit driver fields.
//   Aura::CanStackWith   -> foreign aura objects carrying a driver `stack` flag (self always stacks).
//   CallScriptCheckAreaTargetHandlers -> true (no hooks); conditions -> none (nullptr).
//   GetSpellModOwner -> nullptr (no spell mods); rand_norm unused (no *_RANDOM targets driven).
//   ObjectAccessor::GetUnit -> the named unit if its `inmap` flag is set.
//
// Protocol (stdin, whitespace separated, floats in any strtod form):
//   spell <id> <attr3> <attr5> <attr7> <attr8> <attr9> <singleTarget>
//   effect <idx> <type> <hasEntry> <radius> <perLevel> <rmin> <rmax> <targetA> <checkA> <refA> <checkB> <refB> <playersOnly>
//   unit <name> <P|C> <x> <y> <z> <reach> <alive> <inworld> <removing> <banished> <level> <moving>
//        <master|-> <pet|-> <inmap> <samePhaseOwner> <phase> <inFlight> <immune> <immuneExisting>
//        <immuneEffMask> <foreignStack:-1 none|0|1> <highestExcl> <suppressed> <aoeImmune>
//   rel <name> <checkType> <0|1>
//   visit <n> <names...>
//   aura <unit|dyn> <owner> <caster|-> [<dynX> <dynY> <dynZ> <dynRadius> <dynPhase>]
//   static <name> <mask>        app <name> <mask>
//   fill                        -> {"fill": {...}, "checks": [...], "searches": [...]}
//   update <apply>              -> {"events": [...]}
//   timer <interval> <diff>     -> {"ran": b, "interval": n}
//   reset                       (new scenario)

#include "Define.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#undef TC_COMMON_API
#define TC_COMMON_API
#define ABORT() std::abort()
#define ABORT_MSG(...) std::abort()
#define ASSERT(cond, ...) do { if (!(cond)) std::abort(); } while (0)
#define TC_LOG_FATAL(...) ((void)0)
#define ASSERT_NODEBUGINFO(cond) ASSERT(cond)

#include "Position.h"
#include <G3D/g3dmath.h>
#include <limits>

namespace G3D { double inf() { return std::numeric_limits<double>::infinity(); } }

#include "tc_auramap_decls.inc"

struct SpellRange   // SpellDefines.h:346 (copied)
{
    float Min = 0.0f;
    float Max = 0.0f;

    constexpr SpellRange operator*(float mul) const { return { Min * mul, Max * mul }; }
    bool operator==(SpellRange const&) const = default;
};

enum class SpellOtherImmunity : uint8 { None = 0x0, AoETarget = 0x1, ChainTarget = 0x2 };
enum class SpellEffectAttributes : uint32 { None = 0, PlayersOnly = 0x00004000 };
template<class E> struct FlagSet { uint32 v = 0; bool HasFlag(E f) const { return (v & uint32(f)) != 0; } };
enum class SpellModOp : uint8 { Radius };
enum AuraRemoveMode { AURA_REMOVE_BY_DEFAULT = 1 };
enum AuraObjectType { UNIT_AURA_TYPE, DYNOBJ_AURA_TYPE };
enum TypeID { TYPEID_UNIT = 5, TYPEID_PLAYER = 6, TYPEID_DYNAMICOBJECT = 7 };
enum class WorldObjectSpellAreaTargetSearchReason { Area, Chain };   // Spell.h (copied)

struct ObjectGuid
{
    uint64 v = 0;
    bool operator==(ObjectGuid const&) const = default;
    bool IsEmpty() const { return v == 0; }
};
template<> struct std::hash<ObjectGuid> { size_t operator()(ObjectGuid const& g) const { return std::hash<uint64>()(g.v); } };

float rand_norm() { std::abort(); }

class Unit; class Aura; class Spell; class WorldObject; class Player; class DynamicObject; class GameObject;
struct ConditionContainer { };
struct ConditionSourceInfo { ConditionSourceInfo(void*, WorldObject const*) { } WorldObject* mConditionTargets[3]; };
struct ConditionMgr
{
    bool IsObjectMeetToConditions(WorldObject const*, WorldObject const*, ConditionContainer const&) const { std::abort(); }
    uint32 GetSearcherTypeMaskForConditionList(ConditionContainer const&) const { std::abort(); }
};
ConditionMgr gConditionMgr;
#define sConditionMgr (&gConditionMgr)

struct PhaseShift { bool always = false; int id = 0; };
struct PhasingHandler { static PhaseShift const& GetAlwaysVisiblePhaseShift() { static PhaseShift s{ true, 0 }; return s; } };

struct Map { uint32 GetId() const { return 0; } };
Map gMap;

class Player { public: template<class T> void ApplySpellMod(T const*, SpellModOp, float&, Spell*) const { } };

struct SpellRadiusEntry { float Radius = 0, RadiusPerLevel = 0, RadiusMin = 0, RadiusMax = 0; };

struct SpellImplicitTargetInfo
{
    uint32 target = 0;
    SpellTargetCheckTypes check = TARGET_CHECK_DEFAULT;
    SpellTargetReferenceTypes ref = TARGET_REFERENCE_TYPE_NONE;
    uint32 GetTarget() const { return target; }
    SpellTargetCheckTypes GetCheckType() const { return check; }
    SpellTargetReferenceTypes GetReferenceType() const { return ref; }
};

class SpellInfo;
class SpellEffectInfo
{
public:
    SpellInfo const* _spellInfo = nullptr;
    uint32 EffectIndex = 0;
    SpellEffects Effect = SpellEffects(0);
    SpellImplicitTargetInfo TargetA, TargetB;
    SpellRadiusEntry const* TargetARadiusEntry = nullptr;
    SpellRadiusEntry const* TargetBRadiusEntry = nullptr;
    std::shared_ptr<ConditionContainer> ImplicitTargetConditions;
    FlagSet<SpellEffectAttributes> EffectAttributes;

    bool IsEffect() const;
    bool IsEffect(SpellEffects effectName) const;
    bool IsAreaAuraEffect() const;
    bool IsUnitOwnedAuraEffect() const;
    bool HasRadius(SpellTargetIndex targetIndex) const;
    SpellRange CalcRadius(WorldObject const* caster = nullptr, SpellTargetIndex targetIndex = SpellTargetIndex::TargetA, Spell* spell = nullptr) const;
};

class SpellInfo
{
public:
    uint32 Id = 0;
    uint32 a3 = 0, a5 = 0, a7 = 0, a8 = 0, a9 = 0;
    bool singleTarget = false;
    std::vector<uint32> Labels{ 1 };
    std::vector<SpellEffectInfo> effects;
    std::vector<SpellEffectInfo> const& GetEffects() const { return effects; }
    bool HasAttribute(SpellAttr3 a) const { return (a3 & a) != 0; }
    bool HasAttribute(SpellAttr5 a) const { return (a5 & a) != 0; }
    bool HasAttribute(SpellAttr7 a) const { return (a7 & a) != 0; }
    bool HasAttribute(SpellAttr8 a) const { return (a8 & a) != 0; }
    bool HasAttribute(SpellAttr9 a) const { return (a9 & a) != 0; }
    bool IsSingleTarget() const { return singleTarget; }
};

class WorldObject : public Position
{
public:
    std::string name;
    uint64 guid = 0;
    float reach = 0.0f;
    bool inworld = true;
    int phase = 0;
    virtual ~WorldObject() = default;
    virtual float GetCombatReach() const { return reach; }
    ObjectGuid GetGUID() const { return { guid }; }
    std::string const& GetName() const { return name; }
    bool IsInWorld() const { return inworld; }
    Map* GetMap() const { return &gMap; }
    bool IsInMap(WorldObject const*) const { return true; }
    virtual Unit* ToUnit() { return nullptr; }
    virtual Unit const* ToUnit() const { return nullptr; }
    GameObject* ToGameObject() { return nullptr; }
    virtual TypeID GetTypeId() const = 0;
    Player* GetSpellModOwner() const { return nullptr; }
    bool InSamePhase(PhaseShift const& s) const { return s.always || s.id == phase; }
    PhaseShift GetPhaseShift() const { return { false, phase }; }
    bool IsInRange2d(Position const* pos, float minRange, float maxRange) const;
    bool IsInRange3d(Position const* pos, float minRange, float maxRange) const;
    bool IsSelfOrInSameMap(WorldObject const*) const { return true; }
};

class GameObject : public WorldObject   // GameObject.cpp:3599 IsInRange: unreachable (units only)
{
public:
    TypeID GetTypeId() const override { std::abort(); }
    bool IsInRange(float, float, float, float) const { std::abort(); }
};

class AuraApplication;
class Unit : public WorldObject
{
public:
    bool isPlayer = true;
    bool alive = true, removing = false, banished = false, moving = false, inmap = true, samePhaseOwner = true;
    bool inFlight = false, immune = false, immuneExisting = false, highestExcl = true, suppressed = false, aoeImmune = false;
    uint32 immuneEffMask = 0;
    uint8 level = 1;
    Unit* master = nullptr;
    Unit* pet = nullptr;
    using AuraApplicationMap = std::multimap<uint32, AuraApplication*>;
    AuraApplicationMap applied;

    Unit* ToUnit() override { return this; }
    Unit const* ToUnit() const override { return this; }
    TypeID GetTypeId() const override { return isPlayer ? TYPEID_PLAYER : TYPEID_UNIT; }
    bool IsAlive() const { return alive; }
    bool IsDuringRemoveFromWorld() const { return removing; }
    uint8 GetLevel() const { return level; }
    bool HasAuraState(AuraStateType, SpellInfo const*, Unit const*) const { return banished; }
    Unit* GetCharmerOrOwner() const { return master; }
    ObjectGuid GetPetGUID() const { return pet ? pet->GetGUID() : ObjectGuid{}; }
    bool InSamePhase(WorldObject const*) const { return samePhaseOwner; }
    using WorldObject::InSamePhase;
    bool IsInFlight() const { return inFlight; }
    bool IsImmunedToSpell(SpellInfo const*, uint32, WorldObject const*, bool requireImmunityPurgesEffectAttribute = false) const
    { return requireImmunityPurgesEffectAttribute ? immuneExisting : immune; }
    bool IsImmunedToSpellEffect(SpellInfo const*, SpellEffectInfo const& eff, WorldObject const*, bool = false) const
    { return (immuneEffMask & (1u << eff.EffectIndex)) != 0; }
    bool IsHighestExclusiveAura(Aura const*, bool) const { return highestExcl; }
    bool HasAuraTypeWithMiscvalue(AuraType, int32) const { return suppressed; }
    FlagSet<SpellOtherImmunity> GetSpellOtherImmunityMask() const { return { aoeImmune ? 1u : 0u }; }
    AuraApplicationMap& GetAppliedAuras() { return applied; }
    AuraApplication* _CreateAuraApplication(Aura* aura, uint32 effMask);
    void _UnapplyAura(AuraApplication* aurApp, AuraRemoveMode);
    void _ApplyAura(AuraApplication* aurApp, uint32 effMask);
};

class DynamicObject : public WorldObject
{
public:
    Unit* caster = nullptr;
    float radius = 0.0f;
    TypeID GetTypeId() const override { return TYPEID_DYNAMICOBJECT; }
    Unit* GetCaster() const { return caster; }
    float GetRadius() const { return radius; }
};

std::vector<std::string> gEvents;
std::vector<std::string> gChecks;
std::vector<std::string> gSearches;
std::map<std::pair<std::string, int>, bool> gRel;
std::vector<Unit*> gVisit;
std::map<std::string, Unit*> gUnits;
std::vector<WorldObject const*> gObjects;
int gFillCalls = 0;
static std::string nameOf(Position const* p)
{
    for (WorldObject const* o : gObjects)
        if (static_cast<Position const*>(o) == p)
            return o->name;
    return "?";
}

static std::string hexf(float f) { char b[64]; std::snprintf(b, sizeof b, "\"%a\"", double(f)); return b; }

class AuraApplication
{
public:
    Unit* target; Aura* base; uint32 mask;
    Unit* GetTarget() const { return target; }
    Aura* GetBase() const { return base; }
    uint32 GetEffectMask() const { return mask; }
    void UpdateApplyEffectMask(uint32 newMask, bool)
    {
        gEvents.push_back("{\"ev\":\"update\",\"unit\":\"" + target->name + "\",\"mask\":" + std::to_string(newMask) + "}");
        mask = newMask;
    }
};

class Spell
{
public:
    static uint32 GetSearcherTypeMask(SpellInfo const* spellInfo, SpellEffectInfo const& spellEffectInfo, SpellTargetObjectTypes objType, ConditionContainer const* condList);
    static bool CanIncreaseRangeByMovement(Unit const* unit) { return unit->moving; }
    template<class SEARCHER>
    static void SearchTargets(SEARCHER& searcher, uint32 containerMask, WorldObject* referer, Position const* pos, float radius)
    {
        gSearches.push_back("{\"mask\":" + std::to_string(containerMask) + ",\"radius\":" + hexf(radius) + ",\"pos\":\"" + referer->name + "\"}");
        if (!containerMask)
            return;
        for (Unit* u : gVisit)
            if (containerMask & (u->isPlayer ? GRID_MAP_TYPE_MASK_PLAYER : GRID_MAP_TYPE_MASK_CREATURE))
                searcher.Visit(u);
    }
};

namespace Trinity
{
    struct WorldObjectSpellTargetCheck
    {
        WorldObject* _caster; WorldObject* _referer; SpellInfo const* _spellInfo;
        SpellTargetCheckTypes _targetSelectionType; SpellTargetObjectTypes _objectType;
        WorldObjectSpellTargetCheck(WorldObject* caster, WorldObject* referer, SpellInfo const* spellInfo,
            SpellTargetCheckTypes selectionType, ConditionContainer const*, SpellTargetObjectTypes objectType)
            : _caster(caster), _referer(referer), _spellInfo(spellInfo), _targetSelectionType(selectionType), _objectType(objectType) { }
        bool operator()(WorldObject* target) const
        {
            auto it = gRel.find({ target->name, int(_targetSelectionType) });
            return it != gRel.end() && it->second;
        }
    };

    struct WorldObjectSpellAreaTargetCheck : public WorldObjectSpellTargetCheck
    {
        SpellRange _range;
        Position const* _position;
        WorldObjectSpellAreaTargetSearchReason _searchReason;
        WorldObjectSpellAreaTargetCheck(SpellRange range, Position const* position, WorldObject* caster,
            WorldObject* referer, SpellInfo const* spellInfo, SpellTargetCheckTypes selectionType, ConditionContainer const* condList, SpellTargetObjectTypes objectType,
            WorldObjectSpellAreaTargetSearchReason searchReason = WorldObjectSpellAreaTargetSearchReason::Area);
        bool operator()(WorldObject* target) const;
    };

    template<class Check>
    struct UnitListSearcher
    {
        PhaseShift phase; std::vector<Unit*>* out; Check& check;
        template<typename Container>
        UnitListSearcher(PhaseShift const& phaseShift, Container& container, Check& c) : phase(phaseShift), out(&container), check(c) { log(); }
        template<typename Container>
        UnitListSearcher(WorldObject const* searcher, Container& container, Check& c) : phase(searcher->GetPhaseShift()), out(&container), check(c) { log(); }
        void log() const
        {
            gChecks.push_back("{\"caster\":\"" + (check._caster ? check._caster->name : std::string("-")) + "\",\"referer\":\"" +
                (check._referer ? check._referer->name : std::string("-")) + "\",\"check\":" + std::to_string(int(check._targetSelectionType)) +
                ",\"min\":" + hexf(check._range.Min) + ",\"max\":" + hexf(check._range.Max) + ",\"pos\":\"" + nameOf(check._position) +
                "\",\"always_visible\":" + (phase.always ? "true" : "false") + "}");
        }
        void Visit(Unit* u) { if (!u->WorldObject::InSamePhase(phase)) return; if (check(u)) out->push_back(u); }
    };

    namespace Containers
    {
        template<class C, class P> void EraseIf(C& c, P p) { c.erase(std::remove_if(c.begin(), c.end(), p), c.end()); }
    }
}

struct Cell
{
    template<class T> static void VisitAllObjects(WorldObject const* center, T& visitor, float radius, bool = false)
    {
        gSearches.push_back("{\"mask\":-1,\"radius\":" + hexf(radius + center->GetCombatReach()) + ",\"pos\":\"" + center->name + "\"}");
        for (Unit* u : gVisit)
            visitor.Visit(u);
    }
};

struct ObjectAccessor
{
    static Unit* GetUnit(WorldObject const&, ObjectGuid guid)
    {
        for (auto& [n, u] : gUnits)
            if (u->GetGUID() == guid && u->inmap)
                return u;
        return nullptr;
    }
};

class Aura
{
public:
    using ApplicationMap = std::unordered_map<ObjectGuid, AuraApplication*>;
    SpellInfo const* m_spellInfo = nullptr;
    WorldObject* m_owner = nullptr;
    ObjectGuid m_casterGuid;
    Unit* m_caster = nullptr;
    int32 m_updateTargetMapInterval = 0;
    ApplicationMap m_applications;
    uint32 m_effectMask = 0;
    bool foreign = false, stack = true;

    virtual ~Aura() = default;
    virtual void FillTargetMap(std::unordered_map<Unit*, uint32>& targets, Unit* caster) = 0;
    virtual AuraObjectType GetType() const = 0;
    SpellInfo const* GetSpellInfo() const { return m_spellInfo; }
    uint32 GetId() const { return m_spellInfo->Id; }
    WorldObject* GetOwner() const { return m_owner; }
    ObjectGuid GetCasterGUID() const { return m_casterGuid; }
    bool IsRemoved() const { return false; }
    bool HasEffect(uint8 i) const { return (m_effectMask & (1u << i)) != 0; }
    AuraApplication* GetApplicationOfTarget(ObjectGuid guid) const { auto it = m_applications.find(guid); return it == m_applications.end() ? nullptr : it->second; }
    bool CanStackWith(Aura const* existingAura) const { return this == existingAura || existingAura->stack; }
    bool CallScriptCheckAreaTargetHandlers(Unit*) { return true; }
    static uint32 BuildEffectMaskForOwner(SpellInfo const* spellProto, uint32 availableEffectMask, WorldObject* owner);
    void UpdateTargetMap(Unit* caster, bool apply = true);
    bool CanBeAppliedOn(Unit* target);
    bool CheckAreaTarget(Unit* target);
    void UpdateOwnerTargetMapTimer(uint32 diff, Unit* caster);
};

class UnitAura : public Aura
{
public:
    std::unordered_map<ObjectGuid, uint32> _staticApplications;
    AuraObjectType GetType() const override { return UNIT_AURA_TYPE; }
    Unit* GetUnitOwner() const { return static_cast<Unit*>(m_owner); }
    void FillTargetMap(std::unordered_map<Unit*, uint32>& targets, Unit* caster) override;
    void AddStaticApplication(Unit* target, uint32 effMask);
};

class DynObjAura : public Aura
{
public:
    AuraObjectType GetType() const override { return DYNOBJ_AURA_TYPE; }
    DynamicObject* GetDynobjOwner() const { return static_cast<DynamicObject*>(m_owner); }
    void FillTargetMap(std::unordered_map<Unit*, uint32>& targets, Unit* caster) override;
};

class CountingAura : public Aura
{
public:
    AuraObjectType GetType() const override { return UNIT_AURA_TYPE; }
    void FillTargetMap(std::unordered_map<Unit*, uint32>&, Unit*) override { ++gFillCalls; }
};

class ForeignAura : public Aura
{
public:
    AuraObjectType GetType() const override { return UNIT_AURA_TYPE; }
    void FillTargetMap(std::unordered_map<Unit*, uint32>&, Unit*) override { }
};

AuraApplication* Unit::_CreateAuraApplication(Aura* aura, uint32 effMask)
{
    gEvents.push_back("{\"ev\":\"create\",\"unit\":\"" + name + "\",\"mask\":" + std::to_string(effMask) + "}");
    auto* app = new AuraApplication{ this, aura, effMask };
    aura->m_applications[GetGUID()] = app;
    applied.emplace(aura->GetId(), app);
    return app;
}

void Unit::_UnapplyAura(AuraApplication* aurApp, AuraRemoveMode)
{
    gEvents.push_back("{\"ev\":\"remove\",\"unit\":\"" + name + "\"}");
    aurApp->GetBase()->m_applications.erase(GetGUID());
}

void Unit::_ApplyAura(AuraApplication*, uint32 effMask)
{
    gEvents.push_back("{\"ev\":\"apply\",\"unit\":\"" + name + "\",\"mask\":" + std::to_string(effMask) + "}");
}

#include "tc_auramap_bodies.inc"

// ---------------------------------------------------------------------------
// driver
// ---------------------------------------------------------------------------
struct Scenario
{
    SpellInfo spell;
    std::vector<SpellRadiusEntry> radii = std::vector<SpellRadiusEntry>(32);
    std::vector<std::unique_ptr<Unit>> units;
    std::unique_ptr<DynamicObject> dyn;
    std::unique_ptr<Aura> aura;
    std::vector<std::unique_ptr<ForeignAura>> foreign;
    Unit* caster = nullptr;
};

static Unit* named(std::string const& n) { if (n == "-") return nullptr; auto it = gUnits.find(n); if (it == gUnits.end()) std::abort(); return it->second; }

int main()
{
    auto sc = std::make_unique<Scenario>();
    std::string cmd;
    uint64 nextGuid = 1;
    std::vector<std::tuple<std::string, std::string, std::string>> links;
    while (std::cin >> cmd)
    {
        if (cmd == "reset")
        {
            sc = std::make_unique<Scenario>();
            gUnits.clear(); gRel.clear(); gVisit.clear(); links.clear(); gObjects.clear();
        }
        else if (cmd == "spell")
        {
            int st;
            std::cin >> sc->spell.Id >> sc->spell.a3 >> sc->spell.a5 >> sc->spell.a7 >> sc->spell.a8 >> sc->spell.a9 >> st;
            sc->spell.singleTarget = st != 0;
        }
        else if (cmd == "effect")
        {
            uint32 idx, type, hasEntry, targetA, checkA, refA, checkB, refB, playersOnly;
            std::string r, pl, mn, mx;
            std::cin >> idx >> type >> hasEntry >> r >> pl >> mn >> mx >> targetA >> checkA >> refA >> checkB >> refB >> playersOnly;
            if (sc->spell.effects.size() <= idx)
                sc->spell.effects.resize(idx + 1);
            SpellEffectInfo& e = sc->spell.effects[idx];
            for (uint32 i = 0; i < sc->spell.effects.size(); ++i) { sc->spell.effects[i].EffectIndex = i; sc->spell.effects[i]._spellInfo = &sc->spell; }
            e.Effect = SpellEffects(type);
            sc->radii[idx] = { std::strtof(r.c_str(), nullptr), std::strtof(pl.c_str(), nullptr), std::strtof(mn.c_str(), nullptr), std::strtof(mx.c_str(), nullptr) };
            e.TargetARadiusEntry = hasEntry ? &sc->radii[idx] : nullptr;
            e.TargetA = { targetA, SpellTargetCheckTypes(checkA), SpellTargetReferenceTypes(refA) };
            e.TargetB = { 0, SpellTargetCheckTypes(checkB), SpellTargetReferenceTypes(refB) };
            e.EffectAttributes.v = playersOnly ? uint32(SpellEffectAttributes::PlayersOnly) : 0;
        }
        else if (cmd == "unit")
        {
            auto u = std::make_unique<Unit>();
            std::string kind, x, y, z, reach, master, pet;
            int alive, inworld, removing, banished, level, moving, inmap, samePhase, inFlight, immune, immuneEx, foreignStack, highest, suppressed, aoe;
            uint32 immMask;
            std::cin >> u->name >> kind >> x >> y >> z >> reach >> alive >> inworld >> removing >> banished >> level >> moving
                     >> master >> pet >> inmap >> samePhase >> u->phase >> inFlight >> immune >> immuneEx >> immMask >> foreignStack
                     >> highest >> suppressed >> aoe;
            u->isPlayer = kind == "P";
            u->Relocate(std::strtof(x.c_str(), nullptr), std::strtof(y.c_str(), nullptr), std::strtof(z.c_str(), nullptr));
            u->reach = std::strtof(reach.c_str(), nullptr);
            u->alive = alive; u->inworld = inworld; u->removing = removing; u->banished = banished; u->level = uint8(level);
            u->moving = moving; u->inmap = inmap; u->samePhaseOwner = samePhase; u->inFlight = inFlight; u->immune = immune;
            u->immuneExisting = immuneEx; u->immuneEffMask = immMask; u->highestExcl = highest; u->suppressed = suppressed; u->aoeImmune = aoe;
            u->guid = nextGuid++;
            if (foreignStack >= 0)
            {
                auto f = std::make_unique<ForeignAura>();
                f->m_spellInfo = &sc->spell; f->foreign = true; f->stack = foreignStack != 0;
                u->applied.emplace(0, new AuraApplication{ u.get(), f.get(), 1 });
                sc->foreign.push_back(std::move(f));
            }
            links.emplace_back(u->name, master, pet);
            gUnits[u->name] = u.get();
            gObjects.push_back(u.get());
            sc->units.push_back(std::move(u));
        }
        else if (cmd == "rel")
        {
            std::string n; int check, ok;
            std::cin >> n >> check >> ok;
            gRel[{ n, check }] = ok != 0;
        }
        else if (cmd == "visit")
        {
            int n; std::cin >> n;
            for (auto& [a, m, p] : links) { gUnits[a]->master = named(m); gUnits[a]->pet = named(p); }
            gVisit.clear();
            for (int i = 0; i < n; ++i) { std::string s; std::cin >> s; gVisit.push_back(named(s)); }
        }
        else if (cmd == "aura")
        {
            std::string type, owner, caster;
            std::cin >> type >> owner >> caster;
            sc->caster = named(caster);
            if (type == "unit")
            {
                auto a = std::make_unique<UnitAura>();
                a->m_owner = named(owner);
                sc->aura = std::move(a);
            }
            else
            {
                std::string x, y, z, r; int phase;
                std::cin >> x >> y >> z >> r >> phase;
                sc->dyn = std::make_unique<DynamicObject>();
                sc->dyn->name = owner; sc->dyn->guid = nextGuid++; sc->dyn->phase = phase;
                sc->dyn->Relocate(std::strtof(x.c_str(), nullptr), std::strtof(y.c_str(), nullptr), std::strtof(z.c_str(), nullptr));
                sc->dyn->radius = std::strtof(r.c_str(), nullptr);
                sc->dyn->caster = sc->caster;
                gObjects.push_back(sc->dyn.get());
                auto a = std::make_unique<DynObjAura>();
                a->m_owner = sc->dyn.get();
                sc->aura = std::move(a);
            }
            sc->aura->m_spellInfo = &sc->spell;
            sc->aura->m_caster = sc->caster;
            sc->aura->m_casterGuid = sc->caster ? sc->caster->GetGUID() : ObjectGuid{};
            sc->aura->m_effectMask = Aura::BuildEffectMaskForOwner(&sc->spell, 0xFFFFFFFF, sc->aura->m_owner);
        }
        else if (cmd == "static")
        {
            std::string n; uint32 m; std::cin >> n >> m;
            static_cast<UnitAura*>(sc->aura.get())->AddStaticApplication(named(n), m);
        }
        else if (cmd == "app")
        {
            std::string n; uint32 m; std::cin >> n >> m;
            Unit* u = named(n);
            auto* app = new AuraApplication{ u, sc->aura.get(), m };
            sc->aura->m_applications[u->GetGUID()] = app;
            u->applied.emplace(sc->aura->GetId(), app);
        }
        else if (cmd == "fill")
        {
            gChecks.clear(); gSearches.clear();
            std::unordered_map<Unit*, uint32> targets;
            sc->aura->FillTargetMap(targets, sc->caster);
            std::map<std::string, uint32> sorted;
            for (auto& [u, m] : targets) sorted[u->name] = m;
            std::ostringstream o;
            o << "{\"fill\":{";
            bool first = true;
            for (auto& [n, m] : sorted) { o << (first ? "" : ",") << "\"" << n << "\":" << m; first = false; }
            o << "},\"checks\":[";
            for (size_t i = 0; i < gChecks.size(); ++i) o << (i ? "," : "") << gChecks[i];
            o << "],\"searches\":[";
            for (size_t i = 0; i < gSearches.size(); ++i) o << (i ? "," : "") << gSearches[i];
            o << "]}";
            std::cout << o.str() << std::endl;
        }
        else if (cmd == "update")
        {
            int apply; std::cin >> apply;
            gEvents.clear();
            sc->aura->UpdateTargetMap(sc->caster, apply != 0);
            std::sort(gEvents.begin(), gEvents.end());
            std::ostringstream o;
            o << "{\"events\":[";
            for (size_t i = 0; i < gEvents.size(); ++i) o << (i ? "," : "") << gEvents[i];
            o << "],\"interval\":" << sc->aura->m_updateTargetMapInterval << "}";
            std::cout << o.str() << std::endl;
        }
        else if (cmd == "timer")
        {
            int32 interval; uint32 diff;
            std::cin >> interval >> diff;
            CountingAura a;
            a.m_spellInfo = &sc->spell;
            a.m_updateTargetMapInterval = interval;
            gFillCalls = 0;
            a.UpdateOwnerTargetMapTimer(diff, nullptr);
            std::cout << "{\"ran\":" << (gFillCalls ? "true" : "false") << ",\"interval\":" << a.m_updateTargetMapInterval << "}" << std::endl;
        }
        else
        {
            std::cerr << "unknown command " << cmd << std::endl;
            return 2;
        }
    }
    return 0;
}
