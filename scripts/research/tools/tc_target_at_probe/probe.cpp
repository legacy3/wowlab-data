// Differential probe for the AreaTrigger recipient oracle (targeting/areatriggers.py, Track I).
//
// tc_at_decls.inc / tc_at_bodies.inc are pulled verbatim out of the sibling TrinityCore
// checkout by extract.py; Position.h and G3D/g3dmath.h are included directly.  This file
// supplies only the scaffolding the bodies reference, with Trinity's member names.
//
// Cut points (semantics copied from the cited lines, not extracted):
//   UpdateFields.h:1665-1741  UF::AreaTrigger{Sphere,Box,Polygon,Cylinder,Disk,BoundedPlane}
//                             -> plain members (UpdateField<T> -> T, operator-> kept for TaggedPosition)
//   AreaTrigger.cpp:502/515   CalcCurrentScale / GetProgress -> driver values
//   AreaTrigger.cpp:717       SearchUnits -> driver candidate list filtered by the verbatim
//                             AnyUnitInObjectRangeCheck (grid visit / phase / static spawns out of scope)
//   Containers.h              Trinity::Containers::EraseIf -> std::erase_if (same semantics for vector)
//   DB2Stores                 sDB2Manager.GetCurveValueAt -> abort (MorphCurveId is always 0 here)
//   Object.h:302              GetCombatReach -> stored float (AreaTrigger: 0); GetTransport -> nullptr
//
// Protocol (one request per line, floats as C99 hex; one reply per line):
//   S <shapeType 0..6> d0..d7 nverts [vx vy]* ntverts [vx vy]* progress scale fieldFlags ax ay az ao
//     n [ux uy uz ureach alive]*
//   -> "S <maxSearchRadius> <keptIndex>*"
//   M <shapeType> d0..d7 nverts [vx vy]* ntverts [vx vy]*  -> "M <GetMaxSearchRadius>"

#include "Define.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <span>
#include <sstream>
#include <string>
#include <variant>
#include <vector>

#undef TC_GAME_API
#define TC_GAME_API
#undef TC_COMMON_API
#define TC_COMMON_API
#define ABORT_MSG(...) std::abort()

#include "Position.h"
#include <G3D/g3dmath.h>

namespace G3D { double inf() { return std::numeric_limits<double>::infinity(); } }   // g3dmath.cpp (link-time definition)

class Unit;

class WorldObject : public Position
{
public:
    float reach = 0.0f;
    struct { struct { Position pos; } transport; } m_movementInfo;

    virtual ~WorldObject() = default;
    virtual float GetCombatReach() const { return reach; }
    WorldObject* GetTransport() const { return nullptr; }

    bool _IsWithinDist(WorldObject const* obj, float dist2compare, bool is3D, bool incOwnRadius = true, bool incTargetRadius = true) const;
    bool IsWithinDist(WorldObject const* obj, float dist2compare, bool is3D = true, bool incOwnRadius = true, bool incTargetRadius = true) const;
};

class Unit : public WorldObject
{
public:
    bool alive = true;
    bool IsAlive() const { return alive; }
};

#include "tc_at_decls.inc"

enum class AreaTriggerFieldFlags : uint32 { None = 0x0, HeightIgnoresScale = 0x1 };   // AreaTrigger.h:42 (subset)

namespace Trinity::Containers
{
    template <class C, class P> void EraseIf(C& c, P p) { std::erase_if(c, p); }   // cut point
}

namespace UF
{
    template <class T> struct Field   // UpdateField<T> cut point: value + operator-> / operator*
    {
        T v{};
        operator T const&() const { return v; }
        T const* operator->() const { return &v; }
        T const& operator*() const { return v; }
    };
    struct AreaTriggerSphere { float Radius, RadiusTarget; };
    struct AreaTriggerBox { Field<TaggedPosition<Position::XYZ>> Extents, ExtentsTarget; };
    struct AreaTriggerPolygon
    {
        std::vector<TaggedPosition<Position::XY>> Vertices, VerticesTarget;
        float Height, HeightTarget;
    };
    struct AreaTriggerCylinder { float Radius, RadiusTarget, Height, HeightTarget, LocationZOffset, LocationZOffsetTarget; };
    struct AreaTriggerDisk { float InnerRadius, InnerRadiusTarget, OuterRadius, OuterRadiusTarget, Height, HeightTarget, LocationZOffset, LocationZOffsetTarget; };
    struct AreaTriggerBoundedPlane { Field<TaggedPosition<Position::XY>> Extents, ExtentsTarget; };
}

struct DB2ManagerStub { float GetCurveValueAt(uint32, float) const { std::abort(); } } sDB2Manager;

class AreaTrigger : public WorldObject
{
public:
    struct ShapeDataStub
    {
        UF::AreaTriggerPolygon polygon;
        template <class T> T const* Get() const { return &polygon; }
    };
    struct Data
    {
        uint32 MorphCurveId = 0;
        UF::Field<float> BoundsRadius2D;
        ShapeDataStub ShapeData;
    } data;
    struct DataRef { Data const* d; Data const* operator->() const { return d; } } m_areaTriggerData{ &data };

    float progressValue = 0.0f;
    float scaleValue = 1.0f;
    uint32 fieldFlags = 0;
    std::vector<Unit*> candidates;
    std::vector<Position> _polygonVertices;
    float _verticesUpdatePreviousOrientation = std::numeric_limits<float>::infinity();

    float GetProgress() const { return progressValue; }
    float CalcCurrentScale() const { return scaleValue; }
    bool HasAreaTriggerFlag(AreaTriggerFieldFlags f) const { return (fieldFlags & uint32(f)) != 0; }

    void SearchUnits(std::vector<Unit*>& targetList, float radius, bool check3D)   // cut point: AreaTrigger.cpp:717
    {
        Trinity::AnyUnitInObjectRangeCheck check(this, radius, check3D, false);
        for (Unit* u : candidates)
            if (check(u))
                targetList.push_back(u);
    }

    void SearchUnitInSphere(UF::AreaTriggerSphere const& sphere, std::vector<Unit*>& targetList);
    void SearchUnitInBox(UF::AreaTriggerBox const& box, std::vector<Unit*>& targetList);
    void SearchUnitInPolygon(UF::AreaTriggerPolygon const& polygon, std::vector<Unit*>& targetList);
    void SearchUnitInCylinder(UF::AreaTriggerCylinder const& cylinder, std::vector<Unit*>& targetList);
    void SearchUnitInDisk(UF::AreaTriggerDisk const& disk, std::vector<Unit*>& targetList);
    void SearchUnitInBoundedPlane(UF::AreaTriggerBoundedPlane const& boundedPlane, std::vector<Unit*>& targetList);
    float GetMaxSearchRadius() const;
    void UpdatePolygonVertices();
};

#include "tc_at_bodies.inc"

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

static AreaTriggerShapeInfo readShape(std::istream& in, int& type)
{
    type = I(in);
    std::array<float, MAX_AREATRIGGER_ENTITY_DATA> raw{};
    for (float& f : raw)
        f = F(in);
    AreaTriggerShapeInfo info;
    switch (type)   // AreaTriggerDataStore.cpp:249-289 (copied switch; polygon height fix-up included)
    {
        case 0: info.Data.emplace<AreaTriggerShapeInfo::Sphere>(raw); break;
        case 1: info.Data.emplace<AreaTriggerShapeInfo::Box>(raw); break;
        case 3:
        {
            AreaTriggerShapeInfo::Polygon& polygon = info.Data.emplace<AreaTriggerShapeInfo::Polygon>(raw);
            if (polygon.Height <= 0.0f)
            {
                polygon.Height = 1.0f;
                if (polygon.HeightTarget <= 0.0f)
                    polygon.HeightTarget = 1.0f;
            }
            break;
        }
        case 4: info.Data.emplace<AreaTriggerShapeInfo::Cylinder>(raw); break;
        case 5: info.Data.emplace<AreaTriggerShapeInfo::Disk>(raw); break;
        case 6: info.Data.emplace<AreaTriggerShapeInfo::BoundedPlane>(raw); break;
        default: std::abort();
    }
    std::vector<TaggedPosition<Position::XY>> verts, tverts;
    int n = I(in);
    for (int i = 0; i < n; ++i) { float x = F(in), y = F(in); verts.emplace_back(x, y); }
    int m = I(in);
    for (int i = 0; i < m; ++i) { float x = F(in), y = F(in); tverts.emplace_back(x, y); }
    if (auto* p = std::get_if<AreaTriggerShapeInfo::Polygon>(&info.Data))
    {
        p->PolygonVertices = verts;
        p->PolygonVerticesTarget = tverts;
        if (!p->PolygonVerticesTarget.empty() && p->PolygonVertices.size() != p->PolygonVerticesTarget.size())
            p->PolygonVerticesTarget.clear();
    }
    return info;
}

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string cmd;
        in >> cmd;
        if (cmd == "M")
        {
            int type;
            AreaTriggerShapeInfo info = readShape(in, type);
            std::printf("M"); out(info.GetMaxSearchRadius());
        }
        else if (cmd == "S")
        {
            int type;
            AreaTriggerShapeInfo info = readShape(in, type);
            AreaTrigger at;
            at.progressValue = F(in);
            at.scaleValue = F(in);
            at.fieldFlags = uint32(I(in));
            float ax = F(in), ay = F(in), az = F(in), ao = F(in);
            at.Relocate(ax, ay, az, ao);
            at.data.BoundsRadius2D.v = info.GetMaxSearchRadius();   // AreaTrigger.cpp:175
            int n = I(in);
            std::vector<Unit> units(n);
            for (Unit& u : units)
            {
                float x = F(in), y = F(in), z = F(in);
                u.Relocate(x, y, z);
                u.reach = F(in);
                u.alive = I(in) != 0;
            }
            for (Unit& u : units)
                at.candidates.push_back(&u);

            std::vector<Unit*> targetList;
            // AreaTrigger.cpp:979-1047 SetShape + :645-659 dispatch (copied)
            if (auto* s = std::get_if<AreaTriggerShapeInfo::Sphere>(&info.Data))
                at.SearchUnitInSphere({ s->Radius, s->RadiusTarget }, targetList);
            else if (auto* b = std::get_if<AreaTriggerShapeInfo::Box>(&info.Data))
            {
                UF::AreaTriggerBox box;
                box.Extents.v = b->Extents;
                box.ExtentsTarget.v = b->ExtentsTarget;
                at.SearchUnitInBox(box, targetList);
            }
            else if (auto* p = std::get_if<AreaTriggerShapeInfo::Polygon>(&info.Data))
            {
                UF::AreaTriggerPolygon& poly = at.data.ShapeData.polygon;
                poly.Vertices = p->PolygonVertices;
                poly.VerticesTarget = p->PolygonVerticesTarget;
                poly.Height = p->Height;
                poly.HeightTarget = p->HeightTarget;
                at.UpdatePolygonVertices();   // AreaTrigger.cpp:1102 UpdateShape
                at.SearchUnitInPolygon(poly, targetList);
            }
            else if (auto* c = std::get_if<AreaTriggerShapeInfo::Cylinder>(&info.Data))
                at.SearchUnitInCylinder({ c->Radius, c->RadiusTarget, c->Height, c->HeightTarget, c->LocationZOffset, c->LocationZOffsetTarget }, targetList);
            else if (auto* d = std::get_if<AreaTriggerShapeInfo::Disk>(&info.Data))
                at.SearchUnitInDisk({ d->InnerRadius, d->InnerRadiusTarget, d->OuterRadius, d->OuterRadiusTarget, d->Height, d->HeightTarget, d->LocationZOffset, d->LocationZOffsetTarget }, targetList);
            else if (auto* bp = std::get_if<AreaTriggerShapeInfo::BoundedPlane>(&info.Data))
            {
                UF::AreaTriggerBoundedPlane plane;
                plane.Extents.v = bp->Extents;
                plane.ExtentsTarget.v = bp->ExtentsTarget;
                at.SearchUnitInBoundedPlane(plane, targetList);
            }
            std::printf("S"); out(at.GetMaxSearchRadius());
            for (Unit* u : targetList)
                std::printf(" %d", int(u - units.data()));
        }
        else
            std::printf("?");
        std::printf("\n");
        std::fflush(stdout);
    }
    return 0;
}
