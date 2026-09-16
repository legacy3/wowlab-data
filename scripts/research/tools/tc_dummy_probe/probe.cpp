// Differential probe for the Dummy-semantics oracles.  Compiles the extracted
// TrinityCore helper bodies (tc_dummy_bodies.inc) against a minimal SpellInfo
// shape and prints deterministic JSON cases.  Nothing here is a server.
#include <cstdint>
#include <cstdio>
#include <algorithm>
#include <vector>

typedef std::uint8_t uint8; typedef std::uint16_t uint16; typedef std::uint32_t uint32; typedef std::int32_t int32; typedef std::uint64_t uint64;

enum ComparisionType { COMP_TYPE_EQ = 0, COMP_TYPE_HIGH, COMP_TYPE_LOW, COMP_TYPE_HIGH_EQ, COMP_TYPE_LOW_EQ, COMP_TYPE_MAX };
enum SpellEffIndex : uint8 { EFFECT_0 = 0, EFFECT_ALL = 0xFE, EFFECT_FIRST_FOUND = 0xFF };
#define MAX_SPELL_EFFECTS 32
#define SPELL_EFFECT_ANY 0xFFFF
#define SPELL_AURA_ANY 0xFFFF
#include <cstdlib>
#define ABORT() std::abort()

struct SpellEffectInfo { uint32 EffectIndex; uint32 Effect; uint32 ApplyAuraName; };
struct SpellInfo
{
    std::vector<SpellEffectInfo> effects;
    std::vector<SpellEffectInfo> const& GetEffects() const { return effects; }
    SpellEffectInfo const& GetEffect(SpellEffIndex i) const { return effects[i]; }
};

struct EffectHook
{
    uint8 _effIndex;
    virtual bool CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const = 0;
    uint32 GetAffectedEffectsMask(SpellInfo const* spellInfo) const;
    bool IsEffectAffected(SpellInfo const* spellInfo, uint8 effIndex) const;
};
struct SpellEffectBase : EffectHook { uint16 _effName; bool CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const override; };
struct AuraEffectBase : EffectHook { uint16 _auraType; bool CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const override; };

#include "tc_dummy_bodies.inc"

int main()
{
    std::printf("{\"calculate_pct\":[");
    int32 bases[] = {0, 1, 7, 33, 100, 1234, 65535, 999999, 2147483647, -5, -100};
    int32 pcts[] = {0, 1, 3, 7, 10, 15, 30, 33, 50, 66, 75, 100, 101, 150, 200, 333, -20};
    bool first = true;
    for (int32 b : bases) for (int32 p : pcts)
    {
        int32 i = CalculatePct(b, p);
        float f = CalculatePct(float(b), p);
        uint64 u = b >= 0 ? CalculatePct(uint64(b), p) : 0;
        int32 add = b; AddPct(add, p);
        std::printf("%s{\"base\":%d,\"pct\":%d,\"int\":%d,\"float\":%.9g,\"uint64\":%llu,\"addpct\":%d}", first ? "" : ",", b, p, i, f, (unsigned long long)u, add);
        first = false;
    }
    std::printf("],\"compare_values\":[");
    first = true;
    float vals[] = {0.f, 1.f, 49.999f, 50.f, 50.001f, 100.f};
    for (int t = 0; t < COMP_TYPE_MAX; ++t) for (float v : vals)
    {
        std::printf("%s{\"type\":%d,\"value\":%.6g,\"ref\":50,\"result\":%s}", first ? "" : ",", t, v, CompareValues((ComparisionType)t, v, 50.f) ? "true" : "false");
        first = false;
    }
    std::printf("],\"affected_mask\":[");
    first = true;
    // synthetic spell: effects 0..4 with (Effect, Aura)
    SpellInfo info; info.effects = { {0, 6, 4}, {1, 3, 0}, {2, 6, 42}, {3, 77, 0}, {4, 6, 4} };
    uint8 indices[] = {0, 1, 2, 3, 4, 5, EFFECT_ALL, EFFECT_FIRST_FOUND};
    uint16 effNames[] = {3, 6, 77, 2, SPELL_EFFECT_ANY};
    uint16 auraNames[] = {4, 42, 0, 23, SPELL_AURA_ANY};
    for (uint8 idx : indices)
    {
        for (uint16 en : effNames)
        {
            SpellEffectBase h; h._effIndex = idx; h._effName = en;
            std::printf("%s{\"kind\":\"spell\",\"index\":%u,\"name\":%u,\"mask\":%u}", first ? "" : ",", idx, en, h.GetAffectedEffectsMask(&info));
            first = false;
        }
        for (uint16 an : auraNames)
        {
            AuraEffectBase h; h._effIndex = idx; h._auraType = an;
            std::printf("%s{\"kind\":\"aura\",\"index\":%u,\"name\":%u,\"mask\":%u}", first ? "" : ",", idx, an, h.GetAffectedEffectsMask(&info));
        }
    }
    std::printf("]}\n");
    return 0;
}
