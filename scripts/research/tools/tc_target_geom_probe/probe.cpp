// Differential probe for the targeting geometry oracle (targeting/geometry.py, targeting/area.py).
//
// tc_geom_decls.inc / tc_geom_bodies.inc are pulled verbatim out of the sibling
// TrinityCore checkout by extract.py; Position.h and G3D/g3dmath.h are included
// directly.  This file supplies only the scaffolding the bodies reference, with
// Trinity's member names.  BOUNDED probe: the cut points below replace engine
// state with driver-supplied values.
//
// Cut points (semantics copied from the cited lines, not extracted):
//   Spell.cpp:9294-9396   WorldObjectSpellTargetCheck ctor / operator(): relation, CheckTarget and
//                         conditions -> driver-supplied per-target boolean `rel`
//   SpellDefines.h:346    struct SpellRange (copied)
//   Object.h:302 / Unit.h:704,706  GetCombatReach / GetBoundingRadius -> stored floats
//   Object.cpp:463        IsInMap -> true (single map); InSamePhase -> true; GetTransport -> nullptr
//   GameObject.cpp:3599   GameObject::IsInRange -> not reachable (probe candidates are units)
//   SpellInfo::HasAttribute -> driver flags (CU_CONE_LINE, CU_CONE_BACK, ATTR8_CAN_HIT_AOE_UNTARGETABLE)
//   Spell.cpp:1301, 1994  check construction replicated: coneSrc = *m_caster, DegToRad(ConeAngle),
//                         lineWidth = Width ? Width : caster combat reach
//   GridNotifiersImpl.h:184 WorldObjectSearcherBase::VisitImpl + SearcherLastObjectResult for NEAR
//                         (phase filter dropped: GetAlwaysVisiblePhaseShift at Spell.cpp:2201)
//
// Protocol: one request per line (floats as C99 hex, e.g. 0x1.8p+0), one reply per line.

#include "Define.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <list>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#undef TC_COMMON_API
#define TC_COMMON_API
#define ABORT_MSG(...) std::abort()

#include "Position.h"
#include <G3D/g3dmath.h>
#include <limits>

namespace G3D { double inf() { return std::numeric_limits<double>::infinity(); } }   // g3dmath.cpp (cut point: link-time definition)

struct SpellRange   // SpellDefines.h:346 (copied)
{
    float Min = 0.0f;
    float Max = 0.0f;

    constexpr SpellRange operator*(float mul) const { return { Min * mul, Max * mul }; }
    bool operator==(SpellRange const&) const = default;
};

enum SpellTargetCheckTypes : uint8 { TARGET_CHECK_DEFAULT };
enum SpellTargetObjectTypes : uint8 { TARGET_OBJECT_TYPE_NONE, TARGET_OBJECT_TYPE_UNIT };
enum SpellCustomAttributes : uint32 { SPELL_ATTR0_CU_CONE_BACK = 0x2, SPELL_ATTR0_CU_CONE_LINE = 0x4 };
enum SpellAttr8 : uint32 { SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE = 0x1 };
struct ConditionSourceInfo { ConditionSourceInfo(void*, void*) { } };
struct ConditionContainer { };

enum class SpellOtherImmunity : uint8 { None = 0x0, AoETarget = 0x1, ChainTarget = 0x2 };
struct SpellOtherImmunityMask
{
    uint8 v = 0;
    bool HasFlag(SpellOtherImmunity f) const { return (v & uint8(f)) != 0; }
};

struct SpellInfo
{
    uint32 cu = 0;
    uint32 a8 = 0;
    bool HasAttribute(SpellCustomAttributes a) const { return (cu & a) != 0; }
    bool HasAttribute(SpellAttr8 a) const { return (a8 & a) != 0; }
};

class Unit;
class GameObject;
class Corpse;

class WorldObject : public Position
{
public:
    float reach = 0.0f;
    bool rel = true;
    bool isUnit = true;
    struct { struct { Position pos; } transport; } m_movementInfo;

    virtual ~WorldObject() = default;
    virtual float GetCombatReach() const { return reach; }
    WorldObject* GetTransport() const { return nullptr; }
    bool IsInMap(WorldObject const*) const { return true; }
    bool InSamePhase(WorldObject const*) const { return true; }
    bool IsUnit() const { return isUnit; }
    Unit* ToUnit();
    Unit const* ToUnit() const;
    GameObject* ToGameObject() { return nullptr; }
    Corpse* ToCorpse() { return nullptr; }

    bool _IsWithinDist(WorldObject const* obj, float dist2compare, bool is3D, bool incOwnRadius = true, bool incTargetRadius = true) const;
    float GetDistance(WorldObject const* obj) const;
    float GetDistance(Position const& pos) const;
    float GetDistance(float x, float y, float z) const;
    float GetDistance2d(WorldObject const* obj) const;
    float GetDistance2d(float x, float y) const;
    bool IsWithinDist3d(float x, float y, float z, float dist) const;
    bool IsWithinDist3d(Position const* pos, float dist) const;
    bool IsWithinDist2d(float x, float y, float dist) const;
    bool IsWithinDist2d(Position const* pos, float dist) const;
    bool IsWithinDist(WorldObject const* obj, float dist2compare, bool is3D = true, bool incOwnRadius = true, bool incTargetRadius = true) const;
    bool GetDistanceOrder(WorldObject const* obj1, WorldObject const* obj2, bool is3D = true) const;
    bool IsInRange(WorldObject const* obj, float minRange, float maxRange, bool is3D = true) const;
    bool IsInRange2d(Position const* pos, float minRange, float maxRange) const;
    bool IsInRange3d(Position const* pos, float minRange, float maxRange) const;
    bool IsInBetween(Position const& pos1, Position const& pos2, float size = 0) const;
};

class Unit : public WorldObject
{
public:
    float bounding = 0.0f;
    SpellOtherImmunityMask immune;
    float GetBoundingRadius() const { return bounding; }
    bool IsWithinBoundaryRadius(const Unit* obj) const;
    SpellOtherImmunityMask GetSpellOtherImmunityMask() const { return immune; }
};

class GameObject : public WorldObject
{
public:
    bool IsInRange(float, float, float, float) const { std::abort(); }
};

Unit* WorldObject::ToUnit() { return isUnit ? static_cast<Unit*>(this) : nullptr; }
Unit const* WorldObject::ToUnit() const { return isUnit ? static_cast<Unit const*>(this) : nullptr; }

#include "tc_geom_decls.inc"

namespace Trinity
{
    // cut point: Spell.cpp:9294-9396
    WorldObjectSpellTargetCheck::WorldObjectSpellTargetCheck(WorldObject* caster, WorldObject* referer, SpellInfo const* spellInfo,
        SpellTargetCheckTypes selectionType, ConditionContainer const* condList, SpellTargetObjectTypes objectType) : _caster(caster), _referer(referer), _spellInfo(spellInfo),
        _targetSelectionType(selectionType), _condSrcInfo(nullptr), _condList(condList), _objectType(objectType) { }
    WorldObjectSpellTargetCheck::~WorldObjectSpellTargetCheck() { }
    bool WorldObjectSpellTargetCheck::operator()(WorldObject* target) const { return target->rel; }
}

#include "tc_geom_bodies.inc"

static float F(std::istream& in)
{
    std::string s;
    in >> s;
    return std::strtof(s.c_str(), nullptr);
}

static int I(std::istream& in)
{
    int v;
    in >> v;
    return v;
}

static void out(float v) { std::printf(" %a", double(v)); }

static void place(WorldObject& o, std::istream& in)
{
    float x = F(in), y = F(in), z = F(in);
    o.Relocate(x, y, z);
}

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd == "N")
        {
            std::printf("N"); out(Position::NormalizeOrientation(F(in)));
        }
        else if (cmd == "D")
        {
            std::printf("D"); out(DegToRad(F(in)));
        }
        else if (cmd == "A")   // x y z o arc tx ty tz
        {
            Position p; float x = F(in), y = F(in), z = F(in), o = F(in); p.Relocate(x, y, z, o);
            float arc = F(in);
            Position t; float tx = F(in), ty = F(in), tz = F(in); t.Relocate(tx, ty, tz);
            std::printf("A %d", int(p.HasInArc(arc, &t)));
            out(p.GetRelativeAngle(&t)); out(p.GetAbsoluteAngle(&t));
        }
        else if (cmd == "L")   // x y z o tx ty tz size width
        {
            Position p; float x = F(in), y = F(in), z = F(in), o = F(in); p.Relocate(x, y, z, o);
            Position t; float tx = F(in), ty = F(in), tz = F(in); t.Relocate(tx, ty, tz);
            float size = F(in), width = F(in);
            std::printf("L %d", int(p.HasInLine(&t, size, width)));
        }
        else if (cmd == "R")   // tx ty tz treach cx cy cz creach min max is3d
        {
            Unit t, c; place(t, in); t.reach = F(in); place(c, in); c.reach = F(in);
            float mn = F(in), mx = F(in); int is3d = I(in);
            std::printf("R %d %d %d", int(t.IsInRange2d(&c, mn, mx)), int(t.IsInRange3d(&c, mn, mx)), int(t.IsInRange(&c, mn, mx, is3d != 0)));
        }
        else if (cmd == "G")   // ax ay az areach bx by bz breach dist
        {
            Unit a, b; place(a, in); a.reach = F(in); place(b, in); b.reach = F(in);
            float d = F(in);
            Position const& bp = b;
            std::printf("G"); out(a.GetDistance(&b)); out(a.GetDistance(bp)); out(a.GetDistance2d(&b));
            out(a.GetExactDist(&b)); out(a.GetExactDist2d(&b)); out(a.GetExactDistSq(&b));
            std::printf(" %d %d %d %d", int(a.IsWithinDist3d(&b, d)), int(a.IsWithinDist2d(&b, d)),
                int(a.IsWithinDist(&b, d, true)), int(a.IsWithinDist(&b, d, false)));
        }
        else if (cmd == "O")   // ref a b is3d
        {
            Unit r, a, b; place(r, in); place(a, in); place(b, in); int is3d = I(in);
            std::printf("O %d", int(r.GetDistanceOrder(&a, &b, is3d != 0)));
        }
        else if (cmd == "B")   // t treach p1 p2 size
        {
            Unit t; place(t, in); t.reach = F(in);
            Position p1, p2; float a = F(in), b = F(in), c = F(in); p1.Relocate(a, b, c);
            a = F(in); b = F(in); c = F(in); p2.Relocate(a, b, c);
            float size = F(in);
            std::printf("B %d", int(t.IsInBetween(p1, p2, size)));
        }
        else if (cmd == "W")   // a b bbounding
        {
            Unit a, b; place(a, in); place(b, in); b.bounding = F(in);
            std::printf("W %d", int(a.IsWithinBoundaryRadius(&b)));
        }
        else if (cmd == "AREA")   // t treach immune rel  c  min max canhit reason
        {
            Unit t; place(t, in); t.reach = F(in); t.immune.v = uint8(I(in)); t.rel = I(in) != 0;
            Unit caster; Position c; float x = F(in), y = F(in), z = F(in); c.Relocate(x, y, z);
            SpellRange r{ F(in), 0.0f }; r.Max = F(in);
            SpellInfo si; si.a8 = I(in) ? SPELL_ATTR8_CAN_HIT_AOE_UNTARGETABLE : 0;
            int reason = I(in);
            Trinity::WorldObjectSpellAreaTargetCheck check(r, &c, &caster, &caster, &si, TARGET_CHECK_DEFAULT, nullptr,
                TARGET_OBJECT_TYPE_UNIT, reason ? Trinity::WorldObjectSpellAreaTargetSearchReason::Chain : Trinity::WorldObjectSpellAreaTargetSearchReason::Area);
            std::printf("AREA %d", int(check(&t)));
        }
        else if (cmd == "CONE")   // c o creach coneDeg width min max cu  t treach tbounding immune rel
        {
            Unit caster; place(caster, in); caster.SetOrientation(F(in)); caster.reach = F(in);
            float coneDeg = F(in), width = F(in);
            SpellRange r{ F(in), 0.0f }; r.Max = F(in);
            SpellInfo si; si.cu = uint32(I(in));
            Unit t; place(t, in); t.reach = F(in); t.bounding = F(in); t.immune.v = uint8(I(in)); t.rel = I(in) != 0;
            Trinity::WorldObjectSpellConeTargetCheck check(caster, DegToRad(coneDeg), width ? width : caster.GetCombatReach(), r, &caster, &si,
                TARGET_CHECK_DEFAULT, nullptr, TARGET_OBJECT_TYPE_UNIT);
            std::printf("CONE %d", int(check(&t)));
        }
        else if (cmd == "LINE")   // c o creach hasdst d do width min max  t treach rel
        {
            Unit caster; place(caster, in); caster.SetOrientation(F(in)); caster.reach = F(in);
            int hasDst = I(in);
            Position d; float x = F(in), y = F(in), z = F(in), o = F(in); d.Relocate(x, y, z, o);
            float width = F(in);
            SpellRange r{ F(in), 0.0f }; r.Max = F(in);
            Unit t; place(t, in); t.reach = F(in); t.rel = I(in) != 0;
            SpellInfo si;
            Position const* dst = hasDst == 2 ? static_cast<Position const*>(&caster) : (hasDst ? &d : nullptr);
            Trinity::WorldObjectSpellLineTargetCheck check(&caster, dst, width ? width : caster.GetCombatReach(), r, &caster, &si,
                TARGET_CHECK_DEFAULT, nullptr, TARGET_OBJECT_TYPE_UNIT);
            std::printf("LINE %d", int(check(&t)));
            out(check._position.GetOrientation());
        }
        else if (cmd == "TRAJ")   // c o range src t treach rel
        {
            Unit caster; place(caster, in); caster.SetOrientation(F(in));
            float range = F(in);
            Position s; float x = F(in), y = F(in), z = F(in); s.Relocate(x, y, z);
            Unit t; place(t, in); t.reach = F(in); t.rel = I(in) != 0;
            SpellInfo si;
            Trinity::WorldObjectSpellTrajTargetCheck check(range, &s, &caster, &si, TARGET_CHECK_DEFAULT, nullptr, TARGET_OBJECT_TYPE_NONE);
            std::printf("TRAJ %d", int(check(&t)));
        }
        else if (cmd == "NEAR")   // range c creach n [t treach rel]*
        {
            float range = F(in);
            Unit caster; place(caster, in); caster.reach = F(in);
            int n = I(in);
            std::vector<std::unique_ptr<Unit>> ts;
            for (int i = 0; i < n; ++i)
            {
                auto t = std::make_unique<Unit>(); place(*t, in); t->reach = F(in); t->rel = I(in) != 0;
                ts.push_back(std::move(t));
            }
            SpellInfo si;
            Trinity::WorldObjectSpellNearbyTargetCheck check(range, &caster, &si, TARGET_CHECK_DEFAULT, nullptr, TARGET_OBJECT_TYPE_UNIT);
            int found = -1;
            for (int i = 0; i < n; ++i)
                if (check(ts[i].get()))
                    found = i;   // SearcherLastObjectResult::Insert replaces
            std::printf("NEAR %d", found);
        }
        else if (cmd == "SORT")   // asc ref n [p]*
        {
            int asc = I(in);
            Unit ref; place(ref, in);
            int n = I(in);
            std::vector<std::unique_ptr<Unit>> ts;
            std::list<WorldObject*> lst;
            for (int i = 0; i < n; ++i)
            {
                auto t = std::make_unique<Unit>(); place(*t, in); t->reach = float(i);
                lst.push_back(t.get());
                ts.push_back(std::move(t));
            }
            lst.sort(Trinity::ObjectDistanceOrderPred(&ref, asc != 0));
            std::printf("SORT");
            for (WorldObject* w : lst)
                std::printf(" %d", int(w->reach));
        }
        else if (cmd == "PEQ")
        {
            Position a, b; float x = F(in), y = F(in), z = F(in), o = F(in); a.Relocate(x, y, z, o);
            x = F(in); y = F(in); z = F(in); o = F(in); b.Relocate(x, y, z, o);
            std::printf("PEQ %d", int(a == b));
        }
        else
        {
            std::printf("ERR %s", cmd.c_str());
        }
        std::printf("\n");
        std::fflush(stdout);
    }
    return 0;
}
