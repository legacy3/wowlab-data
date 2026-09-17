// Implicit-target vocabulary probe.
//
// Compiles Trinity's own SpellImplicitTargetInfo / SpellEffectInfo static tables and the
// functions that read them (extracted verbatim by extract.py) against minimal stubs, and
// prints JSON for the Python differential (targeting/selectors.py, targeting/composition.py).
//
// Modes:
//   probe selectors <rand_norm>   one JSON object: constants + every raw target id 0..TOTAL_SPELL_TARGETS-1
//   probe effects                 one JSON object: SpellEffectInfo::_data for 0..TOTAL_SPELL_EFFECTS-1
//   probe explicit                reads spells on stdin, prints SpellInfo::_InitializeExplicitTargetMask results
//     input line: <spell> <hasRange> <rangeMax0> <rangeMax1> <Targets> <AttributesEx13> <nEffects>
//                 then per effect: <effect> <targetA> <targetB> <effectAttributes>
//     output line: {"spell":..,"explicit":..,"required":..}
//
// Cut points: SpellInfo / SpellEffectInfo carry only the members the extracted bodies read.
// GetMaxRange(positive) = RangeEntry ? RangeEntry->RangeMax[positive] : 0 (SpellInfo.cpp:3949, no caster).
// rand_norm() returns the driver value (Random.h:45).

#include <array>
#include <cinttypes>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

typedef int8_t int8;
typedef int16_t int16;
typedef int32_t int32;
typedef int64_t int64;
typedef uint8_t uint8;
typedef uint16_t uint16;
typedef uint32_t uint32;
typedef uint64_t uint64;
#define TC_GAME_API

#include "Utilities/EnumFlag.h"

static float g_rand_norm = 0.0f;
static float rand_norm() { return g_rand_norm; }

#include "tc_selector_decls.inc"

class SpellEffectInfo
{
public:
    using StaticData = SpellEffectStaticData;

    SpellEffects Effect = SPELL_EFFECT_NONE;
    SpellImplicitTargetInfo TargetA;
    SpellImplicitTargetInfo TargetB;
    EnumFlag<SpellEffectAttributes> EffectAttributes = SpellEffectAttributes::None;

    bool IsEffect() const;
    uint32 GetProvidedTargetMask() const;
    uint32 GetMissingTargetMask(bool srcSet = false, bool dstSet = false, uint32 mask = 0) const;
    SpellEffectImplicitTargetTypes GetImplicitTargetType() const;
    SpellTargetObjectTypes GetUsedTargetObjectType() const;

    static std::array<StaticData, TOTAL_SPELL_EFFECTS> _data;
};

class SpellInfo
{
public:
    uint32 AttributesEx13 = 0;
    uint32 Targets = 0;
    uint32 ExplicitTargetMask = 0;
    uint32 RequiredExplicitTargetMask = 0;
    bool HasRangeEntry = false;
    float RangeMax[2] = { 0.0f, 0.0f };
    std::vector<SpellEffectInfo> Effects;

    std::vector<SpellEffectInfo> const& GetEffects() const { return Effects; }
    bool HasAttribute(SpellAttr13 attribute) const { return !!(AttributesEx13 & attribute); }
    float GetMaxRange(bool positive = false) const
    {
        if (!HasRangeEntry)
            return 0.0f;
        return RangeMax[positive ? 1 : 0];
    }
    void _InitializeExplicitTargetMask();
};

#include "tc_selector_bodies.inc"

static std::string hex32(float f)
{
    uint32 bits;
    std::memcpy(&bits, &f, sizeof(bits));
    char buf[16];
    std::snprintf(buf, sizeof(buf), "0x%08" PRIX32, bits);
    return buf;
}

static void selectors(float rnd)
{
    g_rand_norm = rnd;
    std::cout << "{\"TOTAL_SPELL_TARGETS\":" << int(TOTAL_SPELL_TARGETS) << ",\"rand_norm\":\"" << hex32(rnd) << "\",\"targets\":[";
    for (uint32 t = 0; t < TOTAL_SPELL_TARGETS; ++t)
    {
        SpellImplicitTargetInfo info(t);
        if (t)
            std::cout << ",";
        std::cout << "{\"id\":" << t
                  << ",\"object\":" << int(info.GetObjectType())
                  << ",\"reference\":" << int(info.GetReferenceType())
                  << ",\"category\":" << int(info.GetSelectionCategory())
                  << ",\"check\":" << int(info.GetCheckType())
                  << ",\"direction\":" << int(info.GetDirectionType())
                  << ",\"angle\":\"" << hex32(info.CalcDirectionAngle()) << "\""
                  << ",\"is_area\":" << (info.IsArea() ? "true" : "false")
                  << ",\"target_flag_mask\":" << GetTargetFlagMask(info.GetObjectType())
                  << ",\"explicit\":[";
        for (int k = 0; k < 4; ++k)
        {
            bool src = k & 1, dst = k & 2;
            bool srcIn = src, dstIn = dst;
            uint32 mask = info.GetExplicitTargetMask(src, dst);
            if (k)
                std::cout << ",";
            std::cout << "{\"src_in\":" << srcIn << ",\"dst_in\":" << dstIn << ",\"mask\":" << mask
                      << ",\"src_out\":" << src << ",\"dst_out\":" << dst << "}";
        }
        std::cout << "]}";
    }
    std::cout << "]}\n";
}

static void effects()
{
    std::cout << "{\"TOTAL_SPELL_EFFECTS\":" << int(TOTAL_SPELL_EFFECTS) << ",\"effects\":[";
    for (uint32 e = 0; e < TOTAL_SPELL_EFFECTS; ++e)
    {
        SpellEffectInfo eff;
        eff.Effect = SpellEffects(e);
        if (e)
            std::cout << ",";
        std::cout << "{\"id\":" << e << ",\"implicit_target_type\":" << int(eff.GetImplicitTargetType())
                  << ",\"used_object\":" << int(eff.GetUsedTargetObjectType())
                  << ",\"missing_mask_no_targets\":" << eff.GetMissingTargetMask() << "}";
    }
    std::cout << "]}\n";
}

static int explicitMasks()
{
    uint32 spell, hasRange, targets, attr13, n;
    float r0, r1;
    while (std::cin >> spell >> hasRange >> r0 >> r1 >> targets >> attr13 >> n)
    {
        SpellInfo info;
        info.HasRangeEntry = hasRange != 0;
        info.RangeMax[0] = r0;
        info.RangeMax[1] = r1;
        info.Targets = targets;
        info.AttributesEx13 = attr13;
        for (uint32 i = 0; i < n; ++i)
        {
            uint32 effect, a, b, attrs;
            if (!(std::cin >> effect >> a >> b >> attrs))
                return 2;
            if (effect >= TOTAL_SPELL_EFFECTS || a >= TOTAL_SPELL_TARGETS || b >= TOTAL_SPELL_TARGETS)
            {
                std::cout << "{\"spell\":" << spell << ",\"error\":\"out-of-range\"}\n";
                info.Effects.clear();
                n = 0;
                break;
            }
            SpellEffectInfo eff;
            eff.Effect = SpellEffects(effect);
            eff.TargetA = SpellImplicitTargetInfo(a);
            eff.TargetB = SpellImplicitTargetInfo(b);
            eff.EffectAttributes = SpellEffectAttributes(attrs);
            info.Effects.push_back(eff);
        }
        info._InitializeExplicitTargetMask();
        std::cout << "{\"spell\":" << spell << ",\"explicit\":" << info.ExplicitTargetMask
                  << ",\"required\":" << info.RequiredExplicitTargetMask << "}\n";
    }
    return 0;
}

int main(int argc, char** argv)
{
    std::string mode = argc > 1 ? argv[1] : "";
    if (mode == "selectors")
    {
        float rnd = argc > 2 ? std::strtof(argv[2], nullptr) : 0.0f;
        selectors(rnd);
        return 0;
    }
    if (mode == "effects")
    {
        effects();
        return 0;
    }
    if (mode == "explicit")
        return explicitMasks();
    std::cerr << "usage: probe selectors <rand_norm> | effects | explicit < input\n";
    return 1;
}
