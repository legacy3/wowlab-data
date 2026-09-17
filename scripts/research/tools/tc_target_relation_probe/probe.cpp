// Relation-predicate differential probe (Track B).
//
// Trinity's WorldObject::GetReactionTo / GetFactionReactionTo / IsHostileTo /
// IsFriendlyTo / IsValidAttackTarget / IsValidAssistTarget, Unit::IsInPartyWith /
// IsInRaidWith, Player::IsInSameGroupWith / IsInSameRaidWith, the ownership
// helpers and the ReputationMgr lookups are compiled verbatim (extract.py) against
// the minimal stubs below.  The stubs are a single "fat" object whose class
// hierarchy is WorldObject <- GameObject <- Unit <- Creature <- TempSummon <- Player
// (all data lives in WorldObject; every object is allocated as the most derived
// type, so every static_cast in the To*() helpers is a valid downcast).
//
// Stubbed (not Trinity code): object storage / ObjectAccessor, CanSeeOrDetect
// (a per-pair table), faction stores, reputation lists (stated rank + AtWar),
// groups (id + subgroup), SpellInfo (stated IsPositive / IsAffectingArea /
// IsAllowingDeadTarget / attribute words), vehicles (never present).
//
// stdin (whitespace separated records, one world per `E`):
//   T id faction flags fgroup friendgroup enemygroup e0..e7 f0..f7
//   F id repindex
//   O idx kind tpl uflags uflags2 pvp pflags alive gm unattackable mounted treatraid canassist
//     owner charmer summoner attackable_by_summoner group subgroup
//        kind: 0 player 1 creature 2 pet 3 guardian 4 totem 5 gameobject-trap; ids -1 = none; tpl -1 = no entry
//   R player faction rank atwar      (reputation state)
//   X player faction rank            (forced reaction)
//   D player opponent inprogress     (duel)
//   V a b                            (a cannot see b)
//   Q a b hasspell attr0 attr5 attr6 attr8 attr11 cu positive area allowdead
//   E                                (evaluate queries, print one JSON line per query, reset world)
#include <array>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <type_traits>
#include <vector>

typedef int8_t int8;
typedef int16_t int16;
typedef int32_t int32;
typedef int64_t int64;
typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef uint64_t uint64;

#include "EnumFlag.h"

#define ASSERT(x) do { if (!(x)) { std::fprintf(stderr, "ASSERT %s\n", #x); std::abort(); } } while (0)
#define MAX_FACTION_RELATIONS 8

#include "tc_relation_decls.inc"

enum TypeID : uint8 { TYPEID_OBJECT, TYPEID_UNIT = 5, TYPEID_PLAYER = 6, TYPEID_GAMEOBJECT = 8, TYPEID_CORPSE = 10 };
enum GameobjectTypes : uint8 { GAMEOBJECT_TYPE_DOOR = 0, GAMEOBJECT_TYPE_TRAP = 6 };
constexpr uint32 CREATURE_TYPE_FLAG_CAN_ASSIST = 0x00001000;   // SharedDefines.h:5140

template<typename Ret, typename T1, typename... T>
constexpr Ret* Coalesce(T1* first, T*... rest)                  // Util.h:572 (verbatim)
{
    if constexpr (sizeof...(T) > 0)
        return (first ? static_cast<Ret*>(first) : Coalesce<Ret>(rest...));
    else
        return static_cast<Ret*>(first);
}

namespace Trinity::Containers
{
    template<class M> auto MapGetValuePtr(M& map, typename M::key_type const& key) -> decltype(&map.find(key)->second)
    {
        auto itr = map.find(key);
        return itr != map.end() ? &itr->second : nullptr;
    }
}

struct ObjectGuid
{
    int64 v = -1;   // object index, -1 = empty
    bool isPlayer = false;
    bool IsEmpty() const { return v < 0; }
    bool IsPlayer() const { return !IsEmpty() && isPlayer; }
    bool operator!() const { return IsEmpty(); }
    bool operator==(ObjectGuid const& o) const { return v == o.v; }
    bool operator!=(ObjectGuid const& o) const { return v != o.v; }
};

struct CanSeeOrDetectExtraArgs
{
    bool ImplicitDetection = false;
    bool IgnorePhaseShift = false;
    bool IncludeHiddenBySpawnTracking = false;
    bool IncludeAnyPrivateObject = false;
};

struct SpellInfo
{
    uint32 a0 = 0, a5 = 0, a6 = 0, a8 = 0, a11 = 0, cu = 0;
    bool positive = false, area = false, allowDead = false;
    bool IsPositive() const { return positive; }
    bool IsAffectingArea() const { return area; }
    bool IsAllowingDeadTarget() const { return allowDead; }
    bool HasAttribute(SpellAttr0 a) const { return (a0 & a) != 0; }
    bool HasAttribute(SpellAttr5 a) const { return (a5 & a) != 0; }
    bool HasAttribute(SpellAttr6 a) const { return (a6 & a) != 0; }
    bool HasAttribute(SpellAttr8 a) const { return (a8 & a) != 0; }
    bool HasAttribute(SpellAttr11 a) const { return (a11 & a) != 0; }
    bool HasAttribute(SpellCustomAttributes a) const { return (cu & a) != 0; }
};

template<class T> struct Store
{
    std::map<uint32, T> rows;
    T const* LookupEntry(uint32 id) const { auto it = rows.find(id); return it == rows.end() ? nullptr : &it->second; }
};
static Store<FactionEntry> sFactionStore;
static Store<FactionTemplateEntry> sFactionTemplateStore;

typedef uint32 RepListID;
struct FactionState { EnumFlag<ReputationFlags> Flags = ReputationFlags::None; ReputationRank rank = REP_NEUTRAL; };

class Player;
class ReputationMgr
{
public:
    FactionState const* GetState(FactionEntry const* factionEntry) const;
    FactionState const* GetState(RepListID id) const
    {
        auto repItr = _factions.find(id);
        return repItr != _factions.end() ? &repItr->second : nullptr;
    }
    bool IsAtWar(FactionEntry const* factionEntry) const;
    ReputationRank GetRank(FactionEntry const* factionEntry) const
    {
        FactionState const* s = GetState(factionEntry);
        if (!s) { std::fprintf(stderr, "GetRank without state\n"); std::abort(); }
        return s->rank;
    }
    ReputationRank const* GetForcedRankIfAny(FactionTemplateEntry const* factionTemplateEntry) const;
    ReputationRank const* GetForcedRankIfAny(uint32 factionId) const;

    std::map<RepListID, FactionState> _factions;      // keyed by ReputationIndex
    std::map<uint32, ReputationRank> _forcedReactions;
};

struct SummonPropertiesEntry
{
    EnumFlag<SummonPropertiesFlags> flags = SummonPropertiesFlags::None;
    EnumFlag<SummonPropertiesFlags> GetFlags() const { return flags; }
};
struct CreatureDifficulty { uint32 TypeFlags = 0; };
class Player;
struct Group
{
    int id = 0;
    bool SameSubGroup(Player const* member1, Player const* member2) const;   // stub: compares subgroup ids
};

class Unit; class Creature; class TempSummon; class GameObject; class Vehicle;
struct DuelInfo { Player* Opponent = nullptr; DuelState State = DUEL_STATE_CHALLENGED; };

class WorldObject
{
public:
    // ---- stub data -------------------------------------------------------
    int idx = -1, kind = 0;                    // 0 player 1 creature 2 pet 3 guardian 4 totem 5 go-trap
    int tpl = -1;
    uint32 uflags = 0, uflags2 = 0, pflags = 0;
    uint8 pvp = 0;
    bool alive = true, gm = false, unattackable = false, mounted = false, treatRaid = false;
    int owner = -1, charmer = -1, summoner = -1, groupId = 0, subgroup = 0;
    CreatureDifficulty difficulty;
    std::unique_ptr<SummonPropertiesEntry> props;
    ReputationMgr rep;
    std::unique_ptr<DuelInfo> duelInfo;

    // ---- Trinity surface ---------------------------------------------------
    TypeID GetTypeId() const { return kind == 0 ? TYPEID_PLAYER : kind == 5 ? TYPEID_GAMEOBJECT : TYPEID_UNIT; }
    bool IsUnit() const { return kind <= 4; }
    bool IsCorpse() const { return false; }
    Unit* ToUnit();
    Unit const* ToUnit() const;
    Player* ToPlayer();
    Player const* ToPlayer() const;
    Creature const* ToCreature() const;
    GameObject const* ToGameObject() const;
    ObjectGuid GetGUID() const;
    ObjectGuid GetOwnerGUID() const;
    ObjectGuid GetCharmerOrOwnerGUID() const;
    Unit* GetOwner() const;
    Unit* GetCharmerOrOwner() const;
    Unit* GetCharmerOrOwnerOrSelf() const;
    Player* GetCharmerOrOwnerPlayerOrPlayerItself() const;
    Player* GetAffectingPlayer() const;
    uint32 GetFaction() const { return uint32(tpl); }
    FactionTemplateEntry const* GetFactionTemplateEntry() const { return tpl < 0 ? nullptr : sFactionTemplateStore.LookupEntry(uint32(tpl)); }
    bool CanSeeOrDetect(WorldObject const* obj, CanSeeOrDetectExtraArgs const& args = { }) const;

    ReputationRank GetReactionTo(WorldObject const* target) const;
    static ReputationRank GetFactionReactionTo(FactionTemplateEntry const* factionTemplateEntry, WorldObject const* target);
    bool IsHostileTo(WorldObject const* target) const;
    bool IsFriendlyTo(WorldObject const* target) const;
    bool IsValidAttackTarget(WorldObject const* target, SpellInfo const* bySpell = nullptr) const;
    bool IsValidAssistTarget(WorldObject const* target, SpellInfo const* bySpell = nullptr) const;
};

class GameObject : public WorldObject
{
public:
    GameobjectTypes GetGoType() const { return GAMEOBJECT_TYPE_TRAP; }
};

class Unit : public GameObject
{
public:
    bool HasUnitState(uint32 f) const { return unattackable && (f & UNIT_STATE_UNATTACKABLE); }
    bool HasUnitFlag(UnitFlags f) const { return (uflags & f) != 0; }
    bool HasUnitFlag(uint32 f) const { return (uflags & f) != 0; }
    bool HasUnitFlag2(UnitFlags2 f) const { return (uflags2 & f) != 0; }
    bool HasPvpFlag(UnitPVPStateFlags f) const { return (pvp & f) != 0; }
    bool IsInSanctuary() const { return HasPvpFlag(UNIT_BYTE2_FLAG_SANCTUARY); }
    bool IsPvP() const { return HasPvpFlag(UNIT_BYTE2_FLAG_PVP); }
    bool IsFFAPvP() const { return HasPvpFlag(UNIT_BYTE2_FLAG_FFA_PVP); }
    bool IsAlive() const { return alive; }
    bool IsUninteractible() const { return HasUnitFlag(UNIT_FLAG_UNINTERACTIBLE); }
    bool IsImmuneToPC() const { return HasUnitFlag(UNIT_FLAG_IMMUNE_TO_PC); }
    bool IsImmuneToNPC() const { return HasUnitFlag(UNIT_FLAG_IMMUNE_TO_NPC); }
    bool IsPet() const { return kind == 2; }
    bool IsSummon() const { return kind >= 2 && kind <= 4; }
    bool IsMounted() const { return mounted; }
    bool IsCharmed() const { return charmer >= 0; }
    Unit* GetCharmer() const;
    Unit* GetCharmerOrOwner() const { return IsCharmed() ? GetCharmer() : GetOwner(); }   // Unit.h:1220
    bool IsContestedGuard() const
    {
        if (FactionTemplateEntry const* entry = GetFactionTemplateEntry())
            return entry->IsContestedGuardFaction();
        return false;
    }
    Vehicle* GetVehicle() const { return nullptr; }
    bool IsOnVehicle(Unit const*) const { return false; }
    Unit* GetVehicleBase() const { return nullptr; }
    TempSummon const* ToTempSummon() const;
    bool IsInPartyWith(Unit const* unit) const;
    bool IsInRaidWith(Unit const* unit) const;
};

class Creature : public Unit
{
public:
    bool IsTreatedAsRaidUnit() const { return treatRaid; }
    CreatureDifficulty const* GetCreatureDifficulty() const { return &difficulty; }
};

class TempSummon : public Creature
{
public:
    ObjectGuid GetSummonerGUID() const;
    // Trinity exposes this as a public member
    SummonPropertiesEntry const* m_Properties = nullptr;
};

class Player : public TempSummon
{
public:
    bool IsGameMaster() const { return gm; }
    bool HasPlayerFlag(PlayerFlags f) const { return (pflags & f) != 0; }
    ReputationMgr& GetReputationMgr() { return rep; }
    ReputationMgr const& GetReputationMgr() const { return rep; }
    Group* GetGroup() const;
    bool IsInSameGroupWith(Player const* p) const;
    bool IsInSameRaidWith(Player const* p) const;
    std::unique_ptr<DuelInfo>& duel = duelInfo;
};

static std::vector<std::unique_ptr<Player>> g_objects;
static std::map<int, Group> g_groups;
static std::set<std::pair<int, int>> g_hidden;

bool Group::SameSubGroup(Player const* member1, Player const* member2) const { return member1->subgroup == member2->subgroup; }

static Player* obj(int i) { return (i >= 0 && i < int(g_objects.size())) ? g_objects[i].get() : nullptr; }

Unit* WorldObject::ToUnit() { return IsUnit() ? static_cast<Unit*>(this) : nullptr; }
Unit const* WorldObject::ToUnit() const { return IsUnit() ? static_cast<Unit const*>(this) : nullptr; }
Player* WorldObject::ToPlayer() { return kind == 0 ? static_cast<Player*>(this) : nullptr; }
Player const* WorldObject::ToPlayer() const { return kind == 0 ? static_cast<Player const*>(this) : nullptr; }
Creature const* WorldObject::ToCreature() const { return (kind >= 1 && kind <= 4) ? static_cast<Creature const*>(this) : nullptr; }
GameObject const* WorldObject::ToGameObject() const { return kind == 5 ? static_cast<GameObject const*>(this) : nullptr; }
ObjectGuid WorldObject::GetGUID() const { return ObjectGuid{ idx, kind == 0 }; }
ObjectGuid WorldObject::GetOwnerGUID() const { return owner >= 0 ? obj(owner)->GetGUID() : ObjectGuid{}; }
ObjectGuid WorldObject::GetCharmerOrOwnerGUID() const
{
    if (IsUnit() && charmer >= 0)   // Unit.h:1215 IsCharmed() ? GetCharmerGUID() : GetOwnerGUID()
        return obj(charmer)->GetGUID();
    return GetOwnerGUID();
}
Unit* WorldObject::GetOwner() const { return owner >= 0 ? obj(owner) : nullptr; }   // Object.cpp:1605 via ObjectAccessor
Unit* Unit::GetCharmer() const { return charmer >= 0 ? obj(charmer) : nullptr; }
TempSummon const* Unit::ToTempSummon() const { return IsSummon() ? static_cast<TempSummon const*>(this) : nullptr; }
ObjectGuid TempSummon::GetSummonerGUID() const { return summoner >= 0 ? obj(summoner)->GetGUID() : ObjectGuid{}; }
Group* Player::GetGroup() const { if (groupId == 0) return nullptr; return &g_groups[groupId]; }
bool WorldObject::CanSeeOrDetect(WorldObject const* o, CanSeeOrDetectExtraArgs const&) const { return !g_hidden.count({ idx, o->idx }); }

namespace ObjectAccessor
{
    static Player* GetPlayer(WorldObject const&, ObjectGuid const& guid) { Player* p = obj(int(guid.v)); return p && p->kind == 0 ? p : nullptr; }
}

#include "tc_relation_bodies.inc"


static void reset()
{
    g_objects.clear();
    g_groups.clear();
    g_hidden.clear();
    sFactionStore.rows.clear();
    sFactionTemplateStore.rows.clear();
}

int main()
{
    std::string tag;
    struct Query { int a, b, has; SpellInfo s; };
    std::vector<Query> queries;
    while (std::cin >> tag)
    {
        if (tag == "T")
        {
            FactionTemplateEntry t{};
            int64 v;
            std::cin >> v; t.ID = uint32(v);
            std::cin >> v; t.Faction = uint16(v);
            std::cin >> v; t.Flags = int32(v);
            std::cin >> v; t.FactionGroup = uint8(v);
            std::cin >> v; t.FriendGroup = uint8(v);
            std::cin >> v; t.EnemyGroup = uint8(v);
            for (auto& e : t.Enemies) { std::cin >> v; e = uint16(v); }
            for (auto& f : t.Friend) { std::cin >> v; f = uint16(v); }
            sFactionTemplateStore.rows[t.ID] = t;
        }
        else if (tag == "F")
        {
            FactionEntry f{};
            int64 id, ri;
            std::cin >> id >> ri;
            f.ID = uint32(id); f.ReputationIndex = int16(ri);
            sFactionStore.rows[f.ID] = f;
        }
        else if (tag == "O")
        {
            int i;
            std::cin >> i;
            if (int(g_objects.size()) <= i)
                g_objects.resize(i + 1);
            auto p = std::make_unique<Player>();
            p->idx = i;
            int64 v;
            std::cin >> p->kind >> p->tpl;
            std::cin >> v; p->uflags = uint32(v);
            std::cin >> v; p->uflags2 = uint32(v);
            std::cin >> v; p->pvp = uint8(v);
            std::cin >> v; p->pflags = uint32(v);
            int b;
            std::cin >> b; p->alive = b;
            std::cin >> b; p->gm = b;
            std::cin >> b; p->unattackable = b;
            std::cin >> b; p->mounted = b;
            std::cin >> b; p->treatRaid = b;
            std::cin >> b; p->difficulty.TypeFlags = b ? CREATURE_TYPE_FLAG_CAN_ASSIST : 0;
            std::cin >> p->owner >> p->charmer >> p->summoner;
            std::cin >> b;
            if (p->kind >= 2 && p->kind <= 4)
            {
                p->props = std::make_unique<SummonPropertiesEntry>();
                if (b)
                    p->props->flags = SummonPropertiesFlags::AttackableBySummoner;
                p->m_Properties = p->props.get();
            }
            std::cin >> p->groupId >> p->subgroup;
            if (p->groupId)
                g_groups[p->groupId].id = p->groupId;
            g_objects[i] = std::move(p);
        }
        else if (tag == "R")
        {
            int pl, rank, war; int64 fac;
            std::cin >> pl >> fac >> rank >> war;
            FactionEntry const* fe = sFactionStore.LookupEntry(uint32(fac));
            FactionState st;
            st.rank = ReputationRank(rank);
            if (war)
                st.Flags |= ReputationFlags::AtWar;
            obj(pl)->rep._factions[RepListID(fe->ReputationIndex)] = st;
        }
        else if (tag == "X")
        {
            int pl, rank; int64 fac;
            std::cin >> pl >> fac >> rank;
            obj(pl)->rep._forcedReactions[uint32(fac)] = ReputationRank(rank);
        }
        else if (tag == "D")
        {
            int pl, opp, prog;
            std::cin >> pl >> opp >> prog;
            obj(pl)->duelInfo = std::make_unique<DuelInfo>();
            obj(pl)->duelInfo->Opponent = obj(opp);
            obj(pl)->duelInfo->State = prog ? DUEL_STATE_IN_PROGRESS : DUEL_STATE_COUNTDOWN;
        }
        else if (tag == "V")
        {
            int a, b;
            std::cin >> a >> b;
            g_hidden.insert({ a, b });
        }
        else if (tag == "Q")
        {
            Query q;
            int64 v;
            std::cin >> q.a >> q.b >> q.has;
            std::cin >> v; q.s.a0 = uint32(v);
            std::cin >> v; q.s.a5 = uint32(v);
            std::cin >> v; q.s.a6 = uint32(v);
            std::cin >> v; q.s.a8 = uint32(v);
            std::cin >> v; q.s.a11 = uint32(v);
            std::cin >> v; q.s.cu = uint32(v);
            int b;
            std::cin >> b; q.s.positive = b;
            std::cin >> b; q.s.area = b;
            std::cin >> b; q.s.allowDead = b;
            queries.push_back(q);
        }
        else if (tag == "E")
        {
            for (Query const& q : queries)
            {
                WorldObject const* a = obj(q.a);
                WorldObject const* b = obj(q.b);
                SpellInfo const* s = q.has ? &q.s : nullptr;
                std::printf("{\"a\":%d,\"b\":%d,\"r_ab\":%d,\"r_ba\":%d,\"attack\":%s,\"assist\":%s}\n",
                    q.a, q.b, int(a->GetReactionTo(b)), int(b->GetReactionTo(a)),
                    a->IsValidAttackTarget(b, s) ? "true" : "false",
                    a->IsValidAssistTarget(b, s) ? "true" : "false");
            }
            std::printf("END\n");
            std::fflush(stdout);
            queries.clear();
            reset();
        }
    }
    return 0;
}
