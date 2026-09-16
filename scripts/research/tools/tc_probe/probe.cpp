// Differential probe for the gearing research package.
//
// The numeric kernels in tc_extracted.inc are pulled verbatim out of the
// sibling TrinityCore checkout by extract.py.  This file only supplies the
// minimum scaffolding those bodies need, then exposes them over a line based
// stdin protocol so the Python tests can compare results.
//
// Build:  make            (or see Makefile)
// Protocol, one request per line, one reply per line:
//   curvetype <Curve.Type> <point-count>
//       -> the CurveInterpolationMode name
//   curve <mode-name> <n> <x0> <y0> ... <x(n-1)> <y(n-1)> <x>
//       -> the float result, printed with enough digits to round-trip
//   damage <dps> <delay> <variance>
//       -> "<minDamage> <maxDamage>"
//   armor <qualitymod> <total> <locationmod>
//       -> the uint32 result of ItemTemplate::GetArmor's final expression
//   round <value>
//       -> std::round of the float, as an integer

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <span>
#include <sstream>
#include <string>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

using uint32 = std::uint32_t;
using int32 = std::int32_t;

struct DBCPosition2D { float X; float Y; };
struct CurveEntry { uint32 ID; std::uint8_t Type; int32 Flags; };

enum class CurveInterpolationMode : std::uint8_t
{
    Linear = 0, Cosine = 1, CatmullRom = 2, Bezier3 = 3, Bezier4 = 4,
    Bezier = 5, Constant = 6,
};

// ItemTemplate::GetDamage's body calls these three accessors.
static float g_dps = 0.0f;
static uint32 g_delay = 0;
static float g_variance = 0.0f;
static float GetDPS(uint32) { return g_dps; }
static uint32 GetDelay() { return g_delay; }
static float GetDmgVariance() { return g_variance; }

#include "tc_extracted.inc"

static char const* ModeName(CurveInterpolationMode mode)
{
    switch (mode)
    {
        case CurveInterpolationMode::Linear: return "Linear";
        case CurveInterpolationMode::Cosine: return "Cosine";
        case CurveInterpolationMode::CatmullRom: return "CatmullRom";
        case CurveInterpolationMode::Bezier3: return "Bezier3";
        case CurveInterpolationMode::Bezier4: return "Bezier4";
        case CurveInterpolationMode::Bezier: return "Bezier";
        case CurveInterpolationMode::Constant: return "Constant";
    }
    return "Unknown";
}

static CurveInterpolationMode ParseMode(std::string const& name)
{
    if (name == "Linear") return CurveInterpolationMode::Linear;
    if (name == "Cosine") return CurveInterpolationMode::Cosine;
    if (name == "CatmullRom") return CurveInterpolationMode::CatmullRom;
    if (name == "Bezier3") return CurveInterpolationMode::Bezier3;
    if (name == "Bezier4") return CurveInterpolationMode::Bezier4;
    if (name == "Bezier") return CurveInterpolationMode::Bezier;
    return CurveInterpolationMode::Constant;
}

int main()
{
    std::string line;
    std::printf("%s", "");
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string command;
        if (!(in >> command))
            continue;

        if (command == "curvetype")
        {
            int type = 0;
            std::size_t count = 0;
            in >> type >> count;
            CurveEntry curve{0, static_cast<std::uint8_t>(type), 0};
            std::vector<DBCPosition2D> points(count);
            std::printf("%s\n", ModeName(DetermineCurveType(&curve, points)));
        }
        else if (command == "curve")
        {
            std::string mode;
            std::size_t count = 0;
            in >> mode >> count;
            std::vector<DBCPosition2D> points(count);
            for (std::size_t i = 0; i < count; ++i)
                in >> points[i].X >> points[i].Y;
            float x = 0.0f;
            in >> x;
            float value = DB2Manager_GetCurveValueAt(
                ParseMode(mode), std::span<DBCPosition2D const>(points), x);
            std::printf("%.17g\n", static_cast<double>(value));
        }
        else if (command == "damage")
        {
            in >> g_dps >> g_delay >> g_variance;
            float minDamage = 0.0f, maxDamage = 0.0f;
            ItemTemplate_GetDamage(0, minDamage, maxDamage);
            std::printf("%.17g %.17g\n", static_cast<double>(minDamage),
                        static_cast<double>(maxDamage));
        }
        else if (command == "armor")
        {
            float qualitymod = 0.0f, total = 0.0f, locationModifier = 0.0f;
            in >> qualitymod >> total >> locationModifier;
            // The final expression of ItemTemplate::GetArmor.
            std::printf("%u\n",
                        static_cast<uint32>(qualitymod * total * locationModifier + 0.5f));
        }
        else if (command == "round")
        {
            float value = 0.0f;
            in >> value;
            std::printf("%d\n", static_cast<int>(std::round(value)));
        }
        else
        {
            std::printf("ERR unknown command\n");
        }
        std::fflush(stdout);
    }
    return 0;
}
