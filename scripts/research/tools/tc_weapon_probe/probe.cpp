// Differential probe for the weapon_combat research package.
//
// The arithmetic in tc_weapon_bodies.inc is pulled verbatim out of the sibling
// TrinityCore checkout by extract.py (whole bodies, or documented cuts).  This
// file supplies only the class scaffolding those bodies need (stub members
// that return probe-supplied state) and a line based stdin protocol.
//
// Build:  make
// Protocol (one request per line, one reply per line; floats are printed %a):
//   apmult <isPlayer> <feral> <baseAttackTimeMs> <hasWeapon> <delayMs> <subclass> <normalized>
//       -> Unit::GetAPMultiplier                                           [extracted]
//   minmax <attType> <normalized> <addTotalPct> <ap> <apModPos> <apModNeg> <apMult>
//          <flatBase> <basePct> <totalValue> <totalPct> <wmin> <wmax> <haveOff>
//          <versaRatingPct> <versaAura> <formPresent> <combatRoundTime> <canUse>
//          <feral> <isPlayer> <baseAttackTimeMs> <hasWeapon> <delayMs> <subclass>
//       -> "<min> <max>" from Player::CalculateMinMaxDamage                 [extracted]
//          (also leaves that state in place for `calcdamage`)
//   calcdamage <attType> <normalized> <addTotalPct> <feral> <udMin> <udMax> <udOhMin> <udOhMax> <udRMin> <udRMax>
//       -> "<urandMin> <urandMax>" the uint32 bounds Unit::CalculateDamage hands to urand [extracted]
//   apvalue <attType> <includeWeapon> <ap> <apModPos> <apModNeg> <apMult> <mhWeaponAp> <ohWeaponAp> <rWeaponAp>
//       -> Unit::GetTotalAttackPowerValue                                   [extracted]
//   arpcap <armor> <arpPct> <victimLevel>
//       -> armor after the CR_ARMOR_PENETRATION cap block                    [cut]
//   armor <damage> <armor> <armorConstant> <attackerLevel> <attackerIsPlayer> <avgItemLevel> <itemLevelByLevel> <curveValue> <curvePresent>
//       -> uint32 from the CalcArmorReducedDamage tail                        [cut]
//   outcome <outcome> <damage> <attackerLevel> <victimLevel> <critAuraMult> <victimIsPlayer> <shieldBlock> <armorConstant> <blockCrit> <attackerCritBlockMult>
//       -> "<Damage> <CleanDamage> <Blocked> <OriginalDamage>"               [cut]
//   blockpct <shieldBlock> <armorConstant>
//       -> Player::GetBlockPercent                                           [extracted]
//   pct <u32 base> <float pct>          -> CalculatePct<uint32,float>        [extracted template]
//   addpctf <float base> <float pct>    -> AddPct<float,float>               [extracted template]
//   bonus <damage> <flat> <mod>         -> int32 tail of MeleeDamageBonusDone  [re-typed]
//   taken <damage> <flat> <mod> <versaTakenPct> <versaAura>
//       -> int32 tail of MeleeDamageBonusTaken (versatility AddPct + reduction rewrite + final) [re-typed]
//   special <weaponDamage> <fixedBonus> <pctValue> <weaponTotalPct> <addPctMods> <hasPctEffect>
//       -> "<rounded double> <int32 handed to MeleeDamageBonusDone>"        [re-typed, single-effect shorthand]
//   wdmg <attType> <attr6IgnoreCasterMods> <schoolMask> <udMin> <udMax> <ap> <totalPct> <wmin> <wmax>
//        <delayMs> <subclass> <n> (<effect> <value>){n}
//       -> "<weaponDamage %a> <normalized> <addPctMods> <urandMin> <urandMax>"
//          Spell::EffectWeaponDmg loops + Unit::CalculateDamage (+ CalculateMinMaxDamage when
//          normalized or !addPctMods)                                        [cut + extracted]

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <limits>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

using uint8 = std::uint8_t;
using uint16 = std::uint16_t;
using uint32 = std::uint32_t;
using uint64 = std::uint64_t;
using int8 = std::int8_t;
using int16 = std::int16_t;
using int32 = std::int32_t;
using int64 = std::int64_t;
template <typename T> using Optional = std::optional<T>;
#define ASSERT_NOTNULL(x) (x)
template <typename E> constexpr auto AsUnderlyingType(E e) { return static_cast<std::underlying_type_t<E>>(e); }

struct Unit;
using ProcFlagsInit = uint32;   // stub for CalcDamageInfo::ProcAttacker/ProcVictim (unused here)

#include "tc_weapon_decls.inc"

// ---------------------------------------------------------------------------
// stubs: everything the extracted bodies touch that is not arithmetic
// ---------------------------------------------------------------------------
enum TypeID { TYPEID_UNIT = 3, TYPEID_PLAYER = 4 };
enum class AvgItemLevelCategory : int32 { EquippedBase = 1 };
enum class GlobalCurve : int32 { ArmorItemLevelDiminishing = 18 };
enum WorldIntConfigs { CONFIG_EXPANSION };

struct ItemTemplate
{
    uint32 delay = 0;
    uint32 subclass = 0;
    uint32 GetDelay() const { return delay; }
    uint32 GetSubClass() const { return subclass; }
};
struct Item
{
    ItemTemplate tmpl;
    ItemTemplate const* GetTemplate() const { return &tmpl; }
};

struct SpellShapeshiftFormEntry { int16 CombatRoundTime = 0; };
struct ShapeshiftStore
{
    bool present = false;
    SpellShapeshiftFormEntry entry;
    SpellShapeshiftFormEntry const* LookupEntry(uint32) const { return present ? &entry : nullptr; }
} sSpellShapeshiftFormStore;

struct DB2ManagerStub
{
    float armorConstant = 0.0f;
    float curveValue = 1.0f;
    uint32 curveId = 0;
    float EvaluateExpectedStat(ExpectedStatType, uint32, int32, uint32, Classes, int32) const { return armorConstant; }
    uint32 GetGlobalCurveId(GlobalCurve) const { return curveId; }
    float GetCurveValueAt(uint32, float) const { return curveValue; }
} sDB2Manager;

struct WorldStub
{
    uint32 expansion = EXPANSION_MIDNIGHT;
    uint32 getIntConfig(WorldIntConfigs) const { return expansion; }
} g_world;
WorldStub* sWorld = &g_world;

struct GtItemLevelByLevelEntry { float ItemLevel = 0.0f; };
struct ItemLevelByLevelTableStub
{
    GtItemLevelByLevelEntry row;
    GtItemLevelByLevelEntry const* GetRow(uint32) const { return &row; }
} sItemLevelByLevelTable;

static uint32 g_urandMin = 0, g_urandMax = 0;
static uint32 urand(uint32 min, uint32 max)
{
    g_urandMin = min;
    g_urandMax = max;
    return min;
}

struct UnitData
{
    float MinDamage = 0, MaxDamage = 0, MinOffHandDamage = 0, MaxOffHandDamage = 0, MinRangedDamage = 0, MaxRangedDamage = 0;
    int32 AttackPower = 0, AttackPowerModPos = 0, AttackPowerModNeg = 0;
    float AttackPowerMultiplier = 0;
    int32 RangedAttackPower = 0, RangedAttackPowerModPos = 0, RangedAttackPowerModNeg = 0;
    float RangedAttackPowerMultiplier = 0;
    int32 MainHandWeaponAttackPower = 0, OffHandWeaponAttackPower = 0, RangedWeaponAttackPower = 0;
};
struct PlayerData { float AvgItemLevel[4] = {0, 0, 0, 0}; };
struct UFInt32 { int32 v = 0; int32 operator*() const { return v; } operator int32() const { return v; } };  // stub: UpdateField<int32> supports unary *
struct ActivePlayerData { UFInt32 ShieldBlock; };

struct Player;

struct Unit
{
    UnitData data;
    UnitData const* m_unitData = &data;
    float m_weaponDamage[MAX_ATTACK][2] = {{0, 0}, {0, 0}, {0, 0}};
    uint32 m_baseAttackSpeed[MAX_ATTACK] = {0, 0, 0};

    // probe state
    bool isPlayer = true;
    bool feral = false;
    bool formPresent = false;
    bool haveOff = false;
    bool canUse[MAX_ATTACK] = {true, true, true};
    // [attType][UnitModifierFlatType]; TOTAL_VALUE == 2, so the array must span MODIFIER_TYPE_FLAT_END
    float flat[MAX_ATTACK][MODIFIER_TYPE_FLAT_END] = {{0, 0, 0}, {0, 0, 0}, {0, 0, 0}};
    float pct[MAX_ATTACK][MODIFIER_TYPE_PCT_END] = {{1, 1}, {1, 1}, {1, 1}};    // [attType][BASE_PCT, TOTAL_PCT]
    float versaRating = 0.0f;
    int32 versaAura = 0;
    uint8 level = 90;
    float critAuraMult = 1.0f;
    bool blockCrit = false;
    float critBlockMult = 1.0f;
    bool hasWeapon[MAX_ATTACK] = {false, false, false};
    Item weapons[MAX_ATTACK];
    Player const* ownerPlayer = nullptr;
    uint8 unitClass = CLASS_NONE;

    virtual ~Unit() = default;
    int GetTypeId() const { return isPlayer ? TYPEID_PLAYER : TYPEID_UNIT; }
    bool IsInFeralForm() const { return feral; }
    uint32 GetBaseAttackTime(WeaponAttackType att) const { return m_baseAttackSpeed[att]; }
    Player* ToPlayer();
    Player const* ToPlayer() const;
    bool haveOffhandWeapon() const { return haveOff; }
    uint32 GetShapeshiftForm() const { return formPresent ? 1u : 0u; }
    bool CanUseAttackType(uint8 attacktype) const { return canUse[attacktype]; }
    float GetFlatModifierValue(UnitMods unitMod, UnitModifierFlatType type) const { return flat[unitMod - UNIT_MOD_DAMAGE_MAINHAND][type]; }
    float GetPctModifierValue(UnitMods unitMod, UnitModifierPctType type) const { return pct[unitMod - UNIT_MOD_DAMAGE_MAINHAND][type]; }
    int32 GetTotalAuraModifier(AuraType) const { return versaAura; }
    float GetTotalAuraMultiplier(AuraType) const { return critBlockMult; }
    float GetTotalAuraMultiplierByMiscMask(AuraType, uint32) const { return critAuraMult; }
    uint8 GetLevel() const { return level; }
    uint8 GetLevelForTarget(Unit const*) const { return level; }
    virtual float GetBlockPercent(uint8 /*attackerLevel*/) const { return 30.0f; }
    bool IsBlockCritical() const { return blockCrit; }
    Player const* GetCharmerOrOwnerPlayerOrPlayerItself() const { return ownerPlayer; }
    uint8 GetClass() const { return unitClass; }
    void rebind() { m_unitData = &data; }

    float GetAPMultiplier(WeaponAttackType attType, bool normalized) const;
    float GetTotalAttackPowerValue(WeaponAttackType attType, bool includeWeapon = true) const;
    float GetWeaponDamageRange(WeaponAttackType attType, WeaponDamageRange type) const;
    uint32 CalculateDamage(WeaponAttackType attType, bool normalized, bool addTotalPct) const;
    virtual void CalculateMinMaxDamage(WeaponAttackType attType, bool normalized, bool addTotalPct, float& minDamage, float& maxDamage) const = 0;
    void ApplyMeleeOutcome(Unit* victim, CalcDamageInfo* damageInfo) const;
};

struct Player : Unit
{
    PlayerData pdata;
    PlayerData const* m_playerData = &pdata;
    ActivePlayerData apdata;
    ActivePlayerData const* m_activePlayerData = &apdata;

    void reset() { *this = Player(); m_unitData = &data; m_playerData = &pdata; m_activePlayerData = &apdata; }
    Item* GetWeaponForAttack(WeaponAttackType attackType, bool /*useable*/) const
    {
        return hasWeapon[attackType] ? const_cast<Item*>(&weapons[attackType]) : nullptr;
    }
    float GetRatingBonusValue(CombatRating) const { return versaRating; }
    void CalculateMinMaxDamage(WeaponAttackType attType, bool normalized, bool addTotalPct, float& minDamage, float& maxDamage) const override;
    float GetBlockPercent(uint8 attackerLevel) const override;
};

struct SpellEffectInfo
{
    uint32 EffectIndex = 0;
    SpellEffects Effect = SPELL_EFFECT_NONE;
    Mechanics Mechanic = MECHANIC_NONE;
    SpellEffectValue value = 0;   // probe-supplied Spell::CalculateDamage result
};
struct SpellInfoStub
{
    uint32 attr6 = 0;
    std::vector<SpellEffectInfo> effects;
    std::vector<SpellEffectInfo> const& GetEffects() const { return effects; }
    bool HasAttribute(SpellAttr6 a) const { return (attr6 & a) != 0; }
};
struct Spell
{
    SpellInfoStub const* m_spellInfo = nullptr;
    SpellSchoolMask m_spellSchoolMask = SPELL_SCHOOL_MASK_NORMAL;
    WeaponAttackType m_attackType = BASE_ATTACK;
    Unit* unitTarget = nullptr;
    SpellEffectValue CalculateDamage(SpellEffectInfo const& info, Unit const* /*target*/) const { return info.value; }
    SpellEffectValue WeaponDmgCut(Unit* unitCaster, uint32 effectMask, bool& outNormalized, bool& outAddPctMods);
};

Player* Unit::ToPlayer() { return static_cast<Player*>(this); }
Player const* Unit::ToPlayer() const { return static_cast<Player const*>(this); }

#include "tc_weapon_bodies.inc"

// ---------------------------------------------------------------------------
static void print_f(float v) { std::printf("%a", static_cast<double>(v)); }

static Player g_attacker;
static Player g_victim;

static void read_weapon_state(std::istringstream& in, WeaponAttackType attType)
{
    int feral = 0, isPlayer = 1, hasWeapon = 0;
    uint32 baseAttackTime = 0, delay = 0, subclass = 0;
    in >> feral >> isPlayer >> baseAttackTime >> hasWeapon >> delay >> subclass;
    g_attacker.feral = feral != 0;
    g_attacker.isPlayer = isPlayer != 0;
    for (int i = 0; i < MAX_ATTACK; ++i)
        g_attacker.m_baseAttackSpeed[i] = baseAttackTime;
    g_attacker.hasWeapon[attType] = hasWeapon != 0;
    g_attacker.weapons[attType].tmpl.delay = delay;
    g_attacker.weapons[attType].tmpl.subclass = subclass;
}

int main()
{
    std::string line;
    while (std::getline(std::cin, line))
    {
        std::istringstream in(line);
        std::string command;
        if (!(in >> command))
            continue;

        if (command == "apmult")
        {
            int isPlayer = 1, feral = 0, hasWeapon = 0, normalized = 0;
            uint32 baseAttackTime = 0, delay = 0, subclass = 0;
            in >> isPlayer >> feral >> baseAttackTime >> hasWeapon >> delay >> subclass >> normalized;
            g_attacker.reset();
            g_attacker.isPlayer = isPlayer != 0;
            g_attacker.feral = feral != 0;
            for (int i = 0; i < MAX_ATTACK; ++i)
                g_attacker.m_baseAttackSpeed[i] = baseAttackTime;
            g_attacker.hasWeapon[BASE_ATTACK] = hasWeapon != 0;
            g_attacker.weapons[BASE_ATTACK].tmpl.delay = delay;
            g_attacker.weapons[BASE_ATTACK].tmpl.subclass = subclass;
            print_f(g_attacker.GetAPMultiplier(BASE_ATTACK, normalized != 0));
            std::printf("\n");
        }
        else if (command == "minmax")
        {
            int attType = 0, normalized = 0, addTotalPct = 1, haveOff = 0, formPresent = 0, canUse = 1;
            int32 ap = 0, apModPos = 0, apModNeg = 0, versaAura = 0;
            float apMult = 0, flatBase = 0, basePct = 1, totalValue = 0, totalPct = 1, wmin = 0, wmax = 0, versaRating = 0;
            int crt = 0;
            in >> attType >> normalized >> addTotalPct >> ap >> apModPos >> apModNeg >> apMult
               >> flatBase >> basePct >> totalValue >> totalPct >> wmin >> wmax >> haveOff
               >> versaRating >> versaAura >> formPresent >> crt >> canUse;
            g_attacker.reset();
            WeaponAttackType att = WeaponAttackType(attType);
            read_weapon_state(in, att);
            g_attacker.data.AttackPower = ap;
            g_attacker.data.AttackPowerModPos = apModPos;
            g_attacker.data.AttackPowerModNeg = apModNeg;
            g_attacker.data.AttackPowerMultiplier = apMult;
            g_attacker.data.RangedAttackPower = ap;
            g_attacker.data.RangedAttackPowerModPos = apModPos;
            g_attacker.data.RangedAttackPowerModNeg = apModNeg;
            g_attacker.data.RangedAttackPowerMultiplier = apMult;
            g_attacker.flat[attType][BASE_VALUE] = flatBase;
            g_attacker.flat[attType][TOTAL_VALUE] = totalValue;
            g_attacker.pct[attType][BASE_PCT] = basePct;
            g_attacker.pct[attType][TOTAL_PCT] = totalPct;
            g_attacker.m_weaponDamage[attType][MINDAMAGE] = wmin;
            g_attacker.m_weaponDamage[attType][MAXDAMAGE] = wmax;
            g_attacker.haveOff = haveOff != 0;
            g_attacker.versaRating = versaRating;
            g_attacker.versaAura = versaAura;
            g_attacker.formPresent = formPresent != 0;
            sSpellShapeshiftFormStore.present = formPresent != 0;
            sSpellShapeshiftFormStore.entry.CombatRoundTime = int16(crt);
            g_attacker.canUse[attType] = canUse != 0;
            float minDamage = 0.0f, maxDamage = 0.0f;
            g_attacker.CalculateMinMaxDamage(att, normalized != 0, addTotalPct != 0, minDamage, maxDamage);
            print_f(minDamage); std::printf(" "); print_f(maxDamage); std::printf("\n");
        }
        else if (command == "calcdamage")
        {
            int attType = 0, normalized = 0, addTotalPct = 1, feral = 0;
            in >> attType >> normalized >> addTotalPct >> feral;
            in >> g_attacker.data.MinDamage >> g_attacker.data.MaxDamage >> g_attacker.data.MinOffHandDamage
               >> g_attacker.data.MaxOffHandDamage >> g_attacker.data.MinRangedDamage >> g_attacker.data.MaxRangedDamage;
            g_attacker.feral = feral != 0;
            g_urandMin = g_urandMax = 0;
            g_attacker.CalculateDamage(WeaponAttackType(attType), normalized != 0, addTotalPct != 0);
            std::printf("%u %u\n", g_urandMin, g_urandMax);
        }
        else if (command == "apvalue")
        {
            int attType = 0, includeWeapon = 1;
            int32 ap = 0, apModPos = 0, apModNeg = 0, mh = 0, oh = 0, r = 0;
            float apMult = 0;
            in >> attType >> includeWeapon >> ap >> apModPos >> apModNeg >> apMult >> mh >> oh >> r;
            Player p;
            p.reset();
            p.data.AttackPower = p.data.RangedAttackPower = ap;
            p.data.AttackPowerModPos = p.data.RangedAttackPowerModPos = apModPos;
            p.data.AttackPowerModNeg = p.data.RangedAttackPowerModNeg = apModNeg;
            p.data.AttackPowerMultiplier = p.data.RangedAttackPowerMultiplier = apMult;
            p.data.MainHandWeaponAttackPower = mh;
            p.data.OffHandWeaponAttackPower = oh;
            p.data.RangedWeaponAttackPower = r;
            print_f(p.GetTotalAttackPowerValue(WeaponAttackType(attType), includeWeapon != 0));
            std::printf("\n");
        }
        else if (command == "arpcap")
        {
            float armor = 0, arpPct = 0;
            int victimLevel = 0;
            in >> armor >> arpPct >> victimLevel;
            g_victim.reset();
            g_victim.level = uint8(victimLevel);
            print_f(CalcArmorReducedDamage_ArpCap(&g_attacker, &g_victim, armor, arpPct));
            std::printf("\n");
        }
        else if (command == "armor")
        {
            uint32 damage = 0;
            float armor = 0, armorConstant = 0, avgItemLevel = 0, itemLevelByLevel = 0, curveValue = 1;
            int attackerLevel = 0, attackerIsPlayer = 1, curvePresent = 1;
            in >> damage >> armor >> armorConstant >> attackerLevel >> attackerIsPlayer >> avgItemLevel >> itemLevelByLevel >> curveValue >> curvePresent;
            g_attacker.reset();
            g_attacker.level = uint8(attackerLevel);
            g_attacker.isPlayer = attackerIsPlayer != 0;
            g_attacker.ownerPlayer = attackerIsPlayer ? &g_attacker : nullptr;
            g_attacker.pdata.AvgItemLevel[1] = avgItemLevel;
            sDB2Manager.armorConstant = armorConstant;
            sDB2Manager.curveValue = curveValue;
            sDB2Manager.curveId = curvePresent ? 27400u : 0u;
            sItemLevelByLevelTable.row.ItemLevel = itemLevelByLevel;
            g_victim.reset();
            std::printf("%u\n", CalcArmorReducedDamage_Tail(&g_attacker, &g_victim, damage, uint8(attackerLevel), armor));
        }
        else if (command == "outcome")
        {
            int outcome = 0, attackerLevel = 90, victimLevel = 90, victimIsPlayer = 0, blockCrit = 0;
            uint32 damage = 0;
            int32 shieldBlock = 0;
            float critAuraMult = 1, armorConstant = 0, critBlockMult = 1;
            in >> outcome >> damage >> attackerLevel >> victimLevel >> critAuraMult >> victimIsPlayer >> shieldBlock >> armorConstant >> blockCrit >> critBlockMult;
            g_attacker.reset();
            g_attacker.level = uint8(attackerLevel);
            g_attacker.critAuraMult = critAuraMult;
            g_attacker.critBlockMult = critBlockMult;   // Unit.cpp:1463 reads the ATTACKER's (this) aura multiplier
            g_victim.reset();
            g_victim.level = uint8(victimLevel);
            g_victim.isPlayer = victimIsPlayer != 0;
            g_victim.apdata.ShieldBlock.v = shieldBlock;
            g_victim.blockCrit = blockCrit != 0;
            g_victim.critBlockMult = critBlockMult;
            sDB2Manager.armorConstant = armorConstant;
            CalcDamageInfo info{};
            info.Attacker = &g_attacker;
            info.Target = &g_victim;
            info.Damage = damage;
            info.HitOutCome = MeleeHitOutcome(outcome);
            // the base class GetBlockPercent (30.0f) is what a creature victim answers; a player
            // victim answers Player::GetBlockPercent -- emulate the dynamic dispatch explicitly
            Unit* victim = &g_victim;
            if (!g_victim.isPlayer)
            {
                struct CreatureLike : Unit
                {
                    void CalculateMinMaxDamage(WeaponAttackType, bool, bool, float& a, float& b) const override { a = b = 0; }
                };
                static CreatureLike creature;
                creature = CreatureLike();
                creature.rebind();
                creature.level = uint8(victimLevel);
                creature.blockCrit = blockCrit != 0;
                creature.critBlockMult = critBlockMult;
                victim = &creature;
                info.Target = victim;
            }
            g_attacker.ApplyMeleeOutcome(victim, &info);
            std::printf("%u %u %u %u\n", info.Damage, info.CleanDamage, info.Blocked, info.OriginalDamage);
        }
        else if (command == "blockpct")
        {
            int32 shieldBlock = 0;
            float armorConstant = 0;
            in >> shieldBlock >> armorConstant;
            g_victim.reset();
            g_victim.apdata.ShieldBlock.v = shieldBlock;
            sDB2Manager.armorConstant = armorConstant;
            print_f(g_victim.GetBlockPercent(90));
            std::printf("\n");
        }
        else if (command == "pct")
        {
            uint32 base = 0;
            float pct = 0;
            in >> base >> pct;
            std::printf("%u\n", CalculatePct(base, pct));
        }
        else if (command == "addpctf")
        {
            float base = 0, pct = 0;
            in >> base >> pct;
            AddPct(base, pct);
            print_f(base);
            std::printf("\n");
        }
        else if (command == "bonus")
        {
            // [re-typed] Unit::MeleeDamageBonusDone, final two statements
            int32 damage = 0, DoneFlatBenefit = 0;
            float DoneTotalMod = 1.0f;
            in >> damage >> DoneFlatBenefit >> DoneTotalMod;
            float damageF = float(damage + DoneFlatBenefit) * DoneTotalMod;
            std::printf("%d\n", int32(std::max(damageF, 0.0f)));
        }
        else if (command == "taken")
        {
            // [re-typed] Unit::MeleeDamageBonusTaken, versatility block + final two statements
            int32 pdamage = 0, TakenFlatBenefit = 0, versaAura = 0;
            float TakenTotalMod = 1.0f, versaTakenRating = 0.0f;
            in >> pdamage >> TakenFlatBenefit >> TakenTotalMod >> versaTakenRating >> versaAura;
            float versaBonus = versaAura / 2.0f;
            AddPct(TakenTotalMod, -(versaTakenRating + versaBonus));
            if (TakenTotalMod < 1.0f)   // "Sanctified Wrath" block with no SPELL_AURA_MOD_IGNORE_TARGET_RESIST auras
            {
                float damageReduction = 1.0f - TakenTotalMod;
                TakenTotalMod = 1.0f - damageReduction;
            }
            float tmpDamage = float(pdamage + TakenFlatBenefit) * TakenTotalMod;
            std::printf("%d\n", int32(std::max(tmpDamage, 0.0f)));
        }
        else if (command == "special")
        {
            // [re-typed] Spell::EffectWeaponDmg arithmetic after the effect loop
            double weaponDamage = 0, fixed_bonus = 0, pctValue = 0;
            float weapon_total_pct = 1.0f;
            int addPctMods = 1, hasPctEffect = 0;
            in >> weaponDamage >> fixed_bonus >> pctValue >> weapon_total_pct >> addPctMods >> hasPctEffect;
            float weaponDamagePercentMod = 1.0f;
            if (hasPctEffect)
                ApplyPct(weaponDamagePercentMod, pctValue);
            if (addPctMods && fixed_bonus)
                fixed_bonus = fixed_bonus * weapon_total_pct;
            weaponDamage += fixed_bonus;
            if (hasPctEffect)
                weaponDamage = weaponDamage * weaponDamagePercentMod;
            weaponDamage = std::max(std::round(weaponDamage), 0.0);
            int32 asInt = int32(weaponDamage);
            std::printf("%.17g %d\n", weaponDamage, asInt);
        }
        else if (command == "wdmg")
        {
            int attType = 0, n = 0;
            uint32 attr6Ignore = 0, schoolMask = 1, delay = 0, subclass = 0;
            float udMin = 0, udMax = 0, totalPct = 1, wmin = 0, wmax = 0;
            int32 ap = 0;
            in >> attType >> attr6Ignore >> schoolMask >> udMin >> udMax >> ap >> totalPct >> wmin >> wmax >> delay >> subclass >> n;
            static SpellInfoStub info;
            info = SpellInfoStub();
            info.attr6 = attr6Ignore ? uint32(SPELL_ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS) : 0u;
            uint32 effectMask = 0;
            for (int i = 0; i < n; ++i)
            {
                int effect = 0;
                double value = 0;
                in >> effect >> value;
                SpellEffectInfo e;
                e.EffectIndex = uint32(i);
                e.Effect = SpellEffects(effect);
                e.value = value;
                info.effects.push_back(e);
                effectMask |= 1u << i;
            }
            g_attacker.reset();
            WeaponAttackType att = WeaponAttackType(attType);
            g_attacker.isPlayer = true;
            for (int i = 0; i < MAX_ATTACK; ++i)
                g_attacker.m_baseAttackSpeed[i] = delay;
            g_attacker.hasWeapon[att] = true;
            g_attacker.weapons[att].tmpl.delay = delay;
            g_attacker.weapons[att].tmpl.subclass = subclass;
            g_attacker.haveOff = true;
            g_attacker.data.AttackPower = ap;
            g_attacker.data.RangedAttackPower = ap;
            g_attacker.pct[attType][TOTAL_PCT] = totalPct;
            g_attacker.m_weaponDamage[attType][MINDAMAGE] = wmin;
            g_attacker.m_weaponDamage[attType][MAXDAMAGE] = wmax;
            g_attacker.data.MinDamage = g_attacker.data.MinOffHandDamage = g_attacker.data.MinRangedDamage = udMin;
            g_attacker.data.MaxDamage = g_attacker.data.MaxOffHandDamage = g_attacker.data.MaxRangedDamage = udMax;
            sSpellShapeshiftFormStore.present = false;
            Spell spell;
            spell.m_spellInfo = &info;
            spell.m_spellSchoolMask = SpellSchoolMask(schoolMask);
            spell.m_attackType = att;
            spell.unitTarget = &g_victim;
            bool normalized = false, addPctMods = false;
            g_urandMin = g_urandMax = 0;
            SpellEffectValue weaponDamage = spell.WeaponDmgCut(&g_attacker, effectMask, normalized, addPctMods);
            std::printf("%a %d %d %u %u\n", weaponDamage, int(normalized), int(addPctMods), g_urandMin, g_urandMax);
        }
        else
            std::printf("unknown\n");
        std::fflush(stdout);
    }
    return 0;
}
