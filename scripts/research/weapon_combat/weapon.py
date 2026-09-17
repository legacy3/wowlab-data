"""B1 -- the prepared weapon model (Trinity ``7f3d43b``).

What a player's equipment fixes *before* any swing: per attack type
(``BASE_ATTACK``/``OFF_ATTACK``/``RANGED_ATTACK``) the weapon damage range,
the base attack time, the attack-power multiplier, the normalized speed, the
dual-wield / two-hand / ranged state, and the attack-power term of
``Player::CalculateMinMaxDamage``.  Item scaling (min/max damage, delay, DPS)
is *reused* from the gearing archaeology (:class:`gearing.resolver.GearResolver`)
and never re-derived here.

Arithmetic is reproduced in C++ ``float`` (binary32) using the correctly
rounded helpers of :mod:`procs.chance`; ``tools/tc_weapon_probe`` checks the
claim against Trinity's own extracted text.

Mirrors (all ``file:line`` at ``7f3d43b``):

* ``Player::GetAttackBySlot``            Player.cpp:9649
* ``Player::GetWeaponForAttack``         Player.cpp:9582
* ``ItemTemplate::IsRangedWeapon``       ItemTemplate.h:940
* ``Player::_ApplyWeaponDamage``         Player.cpp:8135  (weapon damage, base attack time, weapon AP)
* ``Player::SetRegularAttackTime``       Player.cpp:5387
* ``Player::InitDataForForm``            Player.cpp:23384 (CombatRoundTime overrides attack time)
* ``Unit::GetAPMultiplier``              Unit.cpp:11051
* ``Unit::GetTotalAttackPowerValue``     Unit.cpp:9919
* ``Unit::GetWeaponDamageRange``         Unit.cpp:9949
* ``Player::CalculateMinMaxDamage``      StatSystem.cpp:427
* ``Unit::CalculateDamage``              Unit.cpp:2501   (uint32 bounds handed to urand)
* ``Unit::UpdateDamagePctDoneMods``      Unit.cpp:9781   (off-hand 0.5 factor)
* ``Player::CanEquipItem`` gates         Player.cpp:10856-10877
* ``Player::IsTwoHandUsed``              Player.cpp:13189
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from procs.chance import add, div, f32, lit, mul, round_f32

from . import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT, SourceError

TC = TRINITY_COMMIT[:7]

# ---------------------------------------------------------------------------
# constants (every one cited)
# ---------------------------------------------------------------------------

BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK = 0, 1, 2                 # SharedDefines.h:7427 WeaponAttackType
ATTACK_NAMES = {BASE_ATTACK: "BASE_ATTACK", OFF_ATTACK: "OFF_ATTACK", RANGED_ATTACK: "RANGED_ATTACK"}
ATTACK_BY_NAME = {"mh": BASE_ATTACK, "oh": OFF_ATTACK, "ranged": RANGED_ATTACK,
                  "BASE_ATTACK": BASE_ATTACK, "OFF_ATTACK": OFF_ATTACK, "RANGED_ATTACK": RANGED_ATTACK}

BASE_MINDAMAGE = lit("1.0")          # UnitDefines.h:33  `#define BASE_MINDAMAGE 1.0f`
BASE_MAXDAMAGE = lit("2.0")          # UnitDefines.h:34  `#define BASE_MAXDAMAGE 2.0f`
BASE_ATTACK_TIME = 2000              # UnitDefines.h:35  `#define BASE_ATTACK_TIME 2000` (ms, uint32)
AP_DIVISOR = lit("3.5")              # StatSystem.cpp:447 `GetTotalAttackPowerValue(attType, false) / 3.5f`
AP_MULTIPLIER_FLOOR = lit("0.25")    # StatSystem.cpp:445 `std::max(GetAPMultiplier(attType, normalized), 0.25f)`
NO_WEAPON_AP_MULTIPLIER = lit("2.0")  # Unit.cpp:11058  `if (!weapon) return 2.0f;`
WEAPON_AP_PER_DPS = lit("6.0")       # Player.cpp:8163 `int32(proto->GetDPS(itemLevel) * 6.0f)`
OFFHAND_PCT_FACTOR = lit("0.5")      # Unit.cpp:9793   `factor = 0.5f;  // off hand has 50% penalty`
MS_PER_S = lit("1000.0")
MAX_LEVEL_MIDNIGHT = 90              # SharedDefines.h:135-136 GetMaxLevelForExpansion(EXPANSION_MIDNIGHT)

#: Unit.cpp:11063-11085 -- normalized speeds per ItemSubclassWeapon (ItemTemplate.h:517-537)
NORMALIZED_SPEED_BY_SUBCLASS: dict[int, float] = {
    1: lit("3.3"), 5: lit("3.3"), 6: lit("3.3"), 8: lit("3.3"), 10: lit("3.3"), 20: lit("3.3"),  # AXE2 MACE2 POLEARM SWORD2 STAFF FISHING_POLE
    0: lit("2.4"), 4: lit("2.4"), 7: lit("2.4"), 9: lit("2.4"), 11: lit("2.4"), 12: lit("2.4"), 13: lit("2.4"),  # AXE MACE SWORD WARGLAIVES EXOTIC EXOTIC2 FIST
    15: lit("1.7"),   # DAGGER
    16: lit("2.0"),   # THROWN
}
NORMALIZED_SPEED_SOURCE = "Unit.cpp:11063-11085 (default: weapon delay / 1000.0f, Unit.cpp:11087)"

SUBCLASS_NAMES = {
    0: "AXE", 1: "AXE2", 2: "BOW", 3: "GUN", 4: "MACE", 5: "MACE2", 6: "POLEARM", 7: "SWORD", 8: "SWORD2",
    9: "WARGLAIVES", 10: "STAFF", 11: "EXOTIC", 12: "EXOTIC2", 13: "FIST_WEAPON", 14: "MISCELLANEOUS",
    15: "DAGGER", 16: "THROWN", 17: "SPEAR", 18: "CROSSBOW", 19: "WAND", 20: "FISHING_POLE",
}
RANGED_SUBCLASSES = {2, 3, 18, 19}   # ItemTemplate.h:946-950 IsRangedWeapon: BOW GUN CROSSBOW WAND

INVTYPE_WEAPON, INVTYPE_SHIELD, INVTYPE_RANGED, INVTYPE_2HWEAPON = 13, 14, 15, 17
INVTYPE_WEAPONMAINHAND, INVTYPE_WEAPONOFFHAND, INVTYPE_HOLDABLE, INVTYPE_RANGEDRIGHT = 21, 22, 23, 26
ITEM_CLASS_WEAPON, ITEM_CLASS_ARMOR, ITEM_SUBCLASS_ARMOR_SHIELD, ITEM_SUBCLASS_WEAPON_POLEARM = 2, 4, 6, 6
ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD = 0x00080000   # ItemTemplate.h:281 (ItemSparse.Flags_2)

#: SpellAuraDefines.h:762-793; Unit.cpp:9539 IsInFeralForm
FERAL_FORMS = {1: "FORM_CAT_FORM", 5: "FORM_BEAR_FORM", 8: "FORM_DIRE_BEAR_FORM", 16: "FORM_GHOST_WOLF"}

SPELL_EFFECT_DUAL_WIELD, SPELL_EFFECT_TITAN_GRIP = 40, 155

#: ItemSparse.DamageType -> SpellSchoolMask, Player.cpp:8186 `SpellSchoolMask(1 << weapon->GetTemplate()->GetDamageType())`
SCHOOL_NAMES = {0: "NORMAL", 1: "HOLY", 2: "FIRE", 3: "NATURE", 4: "FROST", 5: "SHADOW", 6: "ARCANE"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wowlab_data_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parents[3], check=True).stdout.strip()
    except Exception:  # pragma: no cover
        return "unknown"


def provenance(generator: str, **extra: Any) -> dict[str, Any]:
    block = {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT,
             "generator": generator, "wowlab_data_commit": wowlab_data_commit()}
    block.update(extra)
    return block


# ---------------------------------------------------------------------------
# pure mirrors (binary32)
# ---------------------------------------------------------------------------

def sub(a: float, b: float) -> float:
    return add(a, -b)


def attack_by_slot(slot: str, inventory_type: int) -> int | None:
    """Mirrors ``Player::GetAttackBySlot`` (Player.cpp:9649-9657)."""
    if slot == "main_hand":
        return BASE_ATTACK if inventory_type not in (INVTYPE_RANGED, INVTYPE_RANGEDRIGHT) else RANGED_ATTACK
    if slot == "off_hand":
        return OFF_ATTACK
    return None


def is_ranged_weapon(class_id: int, subclass: int) -> bool:
    """Mirrors ``ItemTemplate::IsRangedWeapon`` (ItemTemplate.h:940-953)."""
    return class_id == ITEM_CLASS_WEAPON and subclass in RANGED_SUBCLASSES


def ap_multiplier(*, is_player: bool, feral: bool, base_attack_time_ms: int,
                  weapon: tuple[int, int] | None, normalized: bool) -> tuple[float, str]:
    """Mirrors ``Unit::GetAPMultiplier`` (Unit.cpp:11051-11088).

    ``weapon`` is ``(delay_ms, subclass)`` of ``GetWeaponForAttack(attType, true)``
    or ``None``.  Returns ``(value, branch)``; ``uint32 / 1000.0f`` converts the
    integer to ``float`` first (exact below 2**24 ms).
    """
    if not is_player or (feral and not normalized):
        return div(f32(base_attack_time_ms), MS_PER_S), "GetBaseAttackTime(attType) / 1000.0f (Unit.cpp:11054)"
    if weapon is None:
        return NO_WEAPON_AP_MULTIPLIER, "no weapon -> 2.0f (Unit.cpp:11058)"
    delay_ms, subclass = weapon
    if not normalized:
        return div(f32(delay_ms), MS_PER_S), "weapon->GetTemplate()->GetDelay() / 1000.0f (Unit.cpp:11061)"
    if subclass in NORMALIZED_SPEED_BY_SUBCLASS:
        return NORMALIZED_SPEED_BY_SUBCLASS[subclass], f"normalized table for subclass {subclass} ({NORMALIZED_SPEED_SOURCE})"
    return div(f32(delay_ms), MS_PER_S), "normalized default: delay / 1000.0f (Unit.cpp:11087)"


def total_attack_power(*, att_type: int, include_weapon: bool, ap: int, mod_pos: int, mod_neg: int,
                       multiplier: float, weapon_ap_mh: int = 0, weapon_ap_oh: int = 0,
                       weapon_ap_ranged: int = 0) -> float:
    """Mirrors ``Unit::GetTotalAttackPowerValue`` (Unit.cpp:9919-9947).

    ``AttackPower + ModPos + ModNeg`` is an **int32 sum** converted to float once
    (UpdateFields.h:401-403 are ``UpdateField<int32>``); the weapon AP joins as
    float; the off-hand halves *after* adding its weapon AP and only when
    ``includeWeapon``; the multiplier applies last as ``ap * (1.0f + mult)``.
    """
    value = f32(int(ap) + int(mod_pos) + int(mod_neg))
    if include_weapon:
        if att_type == RANGED_ATTACK or att_type == BASE_ATTACK:
            value = add(value, f32(max(int(weapon_ap_mh), int(weapon_ap_ranged))))
        else:
            value = add(value, f32(int(weapon_ap_oh)))
            value = div(value, f32(2))
    if value < 0:
        return 0.0
    return mul(value, add(lit("1.0"), f32(multiplier)))


@dataclass(frozen=True)
class MinMaxInputs:
    """Everything ``Player::CalculateMinMaxDamage`` reads, as the probe sees it."""
    att_type: int
    normalized: bool
    add_total_pct: bool
    ap: int = 0
    ap_mod_pos: int = 0
    ap_mod_neg: int = 0
    ap_multiplier: float = 0.0
    flat_base: float = 0.0          # UNIT_MOD_DAMAGE_* BASE_VALUE  (never set for players: no Player/StatSystem setter)
    base_pct: float = 1.0           # UNIT_MOD_DAMAGE_* BASE_PCT    (no aura handler writes it)
    total_value: float = 0.0        # TOTAL_VALUE: SPELL_AURA_MOD_DAMAGE_DONE(physical) + enchant damage (Player.cpp:4921-4973)
    total_pct: float = 1.0          # TOTAL_PCT: Unit::UpdateDamagePctDoneMods (Unit.cpp:9781-9819)
    weapon_min: float = 0.0         # m_weaponDamage[attType][MINDAMAGE]
    weapon_max: float = 0.0
    have_offhand_weapon: bool = False
    versatility_rating_pct: float = 0.0   # Player::GetRatingBonusValue(CR_VERSATILITY_DAMAGE_DONE)
    versatility_aura: int = 0             # GetTotalAuraModifier(SPELL_AURA_MOD_VERSATILITY)
    form_combat_round_time: int = 0       # SpellShapeshiftForm.CombatRoundTime (int16), 0 = none/absent
    can_use_attack_type: bool = True      # Unit::CanUseAttackType (disarm flags)
    feral: bool = False
    is_player: bool = True
    base_attack_time_ms: int = BASE_ATTACK_TIME
    weapon: tuple[int, int] | None = None  # (delay_ms, subclass) of GetWeaponForAttack(attType, true)


def calculate_min_max(inp: MinMaxInputs) -> dict[str, Any]:
    """Mirrors ``Player::CalculateMinMaxDamage`` (StatSystem.cpp:427-482) in binary32.

    Returns every intermediate.  ``min``/``max`` are the ``float&`` outputs.
    """
    apmod, apmod_branch = ap_multiplier(is_player=inp.is_player, feral=inp.feral,
                                        base_attack_time_ms=inp.base_attack_time_ms,
                                        weapon=inp.weapon, normalized=inp.normalized)
    attack_power_mod = max(apmod, AP_MULTIPLIER_FLOOR)                                   # :445
    ap_total = total_attack_power(att_type=inp.att_type, include_weapon=False, ap=inp.ap,
                                  mod_pos=inp.ap_mod_pos, mod_neg=inp.ap_mod_neg,
                                  multiplier=inp.ap_multiplier)                          # :447 includeWeapon=false
    ap_term = mul(div(ap_total, AP_DIVISOR), attack_power_mod)                           # :447
    base_value = add(f32(inp.flat_base), ap_term)                                        # :447
    base_pct = f32(inp.base_pct)                                                         # :448
    total_value = f32(inp.total_value)                                                   # :449
    total_pct = f32(inp.total_pct) if inp.add_total_pct else lit("1.0")                  # :450
    # GetWeaponDamageRange (Unit.cpp:9949-9955): off-hand without weapon reads 0.0f
    if inp.att_type == OFF_ATTACK and not inp.have_offhand_weapon:
        weapon_min, weapon_max = 0.0, 0.0
    else:
        weapon_min, weapon_max = f32(inp.weapon_min), f32(inp.weapon_max)                # :452-453
    versa = lit("1.0")                                                                   # :455
    versa_pct = add(f32(inp.versatility_rating_pct), f32(inp.versatility_aura))          # :457 float + float(int32)
    versa = add(versa, div(mul(versa, versa_pct), lit("100.0")))                         # AddPct<float,float> Util.h:85/72
    branch = "plain"
    if inp.form_combat_round_time:                                                       # :459-464
        crt = f32(inp.form_combat_round_time)
        weapon_min = div(div(mul(weapon_min, crt), MS_PER_S), attack_power_mod)
        weapon_max = div(div(mul(weapon_max, crt), MS_PER_S), attack_power_mod)
        branch = "shapeshift CombatRoundTime"
    elif not inp.can_use_attack_type:                                                    # :465-476
        if inp.att_type != BASE_ATTACK:
            return {"min": 0.0, "max": 0.0, "branch": "disarmed non-main-hand -> 0/0 (StatSystem.cpp:468-473)",
                    "attack_power_mod": attack_power_mod, "ap_total_no_weapon": ap_total, "ap_term": ap_term}
        weapon_min, weapon_max = BASE_MINDAMAGE, BASE_MAXDAMAGE
        branch = "disarmed main hand -> BASE_MINDAMAGE/BASE_MAXDAMAGE (StatSystem.cpp:474-475)"
    lo = mul(mul(add(mul(add(weapon_min, base_value), base_pct), total_value), total_pct), versa)   # :478
    hi = mul(mul(add(mul(add(weapon_max, base_value), base_pct), total_value), total_pct), versa)   # :479
    return {
        "min": lo, "max": hi, "branch": branch,
        "ap_multiplier": apmod, "ap_multiplier_branch": apmod_branch,
        "attack_power_mod": attack_power_mod, "ap_total_no_weapon": ap_total, "ap_term": ap_term,
        "base_value": base_value, "base_pct": base_pct, "total_value": total_value, "total_pct": total_pct,
        "weapon_min_used": weapon_min, "weapon_max_used": weapon_max, "versatility_mod": versa,
    }


def urand_bounds(min_damage: float, max_damage: float) -> tuple[int, int]:
    """Mirrors the tail of ``Unit::CalculateDamage`` (Unit.cpp:2544-2550): clamp at
    0, swap if inverted, then ``urand(uint32(min), uint32(max))`` -- truncation."""
    lo = max(0.0, min_damage)
    hi = max(0.0, max_damage)
    if lo > hi:
        lo, hi = hi, lo
    return int(math.trunc(lo)), int(math.trunc(hi))


def weapon_attack_power(dps: float) -> int:
    """Mirrors Player.cpp:8163 ``int32(proto->GetDPS(itemLevel) * 6.0f)``."""
    return int(math.trunc(mul(f32(dps), WEAPON_AP_PER_DPS)))


# ---------------------------------------------------------------------------
# dual-wield / Titan's Grip capability from data
# ---------------------------------------------------------------------------

def capability_by_spec(scope: Any, catalog: Any) -> dict[str, Any]:
    """Which current specs reach a ``SPELL_EFFECT_DUAL_WIELD`` (40) / ``SPELL_EFFECT_TITAN_GRIP``
    (155) spell (``Spell::EffectDualWield`` SpellEffects.cpp:2237, ``Spell::EffectTitanGrip``
    SpellEffects.cpp:4954), using the shared current-player scope."""
    out: dict[str, Any] = {"dual_wield": {}, "titan_grip": {}}
    for spec, spells in sorted(scope.specs_reach.items()):
        for spell in sorted(spells):
            info = catalog.get(spell)
            if info is None:
                continue
            for eff in info.effects:
                if eff.effect == SPELL_EFFECT_DUAL_WIELD:
                    out["dual_wield"].setdefault(str(spec), []).append({"spell": spell, "name": info.name})
                elif eff.effect == SPELL_EFFECT_TITAN_GRIP:
                    out["titan_grip"].setdefault(str(spec), []).append({
                        "spell": spell, "name": info.name, "penalty_spell": eff.misc0,
                        "equipped_item_class": info.equipped_item_class,
                        "equipped_item_subclass_mask": info.equipped_item_subclass_mask})
    return out


def learned_only_skill_line_spells(tables: Any, spells: Any) -> dict[int, list[int]]:
    """Spells whose every ``SkillLineAbility`` row has ``AcquireMethod`` 0 (``Learned``).

    ``Player::LearnSkillRewardedSpells`` skips AcquireMethod 0 (``default: continue``,
    Player.cpp:25404-25417; DBCEnums.h:2362), so such a spell reaches a character only as
    learned-spell character state (trainer, quest or a ``SPELL_EFFECT_LEARN_SPELL`` caster),
    never automatically.  ``dummy_semantics.scope.Scope(include_class_skills=True)`` does not
    filter on AcquireMethod, so its class-skill-line reach over-approximates capability.
    Returns ``{spell: [SkillLineAbility.ID, ...]}``.
    """
    wanted = {int(x) for x in spells}
    rows: dict[int, list[tuple[int, int]]] = {}
    for sla in tables("SkillLineAbility").rows:
        spell = int(sla["Spell"])
        if spell in wanted:
            rows.setdefault(spell, []).append((int(sla["ID"]), int(sla["AcquireMethod"])))
    return {spell: sorted(i for i, _ in r) for spell, r in sorted(rows.items())
            if r and all(m == 0 for _, m in r)}


def _learned_spell_ids(identity: dict[str, Any]) -> set[int]:
    """Explicitly learned spells supplied by a fixture identity (``learned_spells``: ints or {spell_id})."""
    out: set[int] = set()
    for e in identity.get("learned_spells") or []:
        if isinstance(e, dict):
            e = e.get("spell_id", e.get("spell"))
        try:
            out.add(int(e))
        except (TypeError, ValueError):
            continue
    return out


PLAYER_CLASS_IDS = tuple(range(1, 14))      # SharedDefines.h:153-171 (WARRIOR..EVOKER; 14/15 are not player classes)
SKILL_ACQUIRE_AUTOMATIC = {1: "AutomaticSkillRank", 2: "AutomaticCharLevel", 4: "LearnedOrAutomaticCharLevel"}  # DBCEnums.h:2360-2367


def default_skill_grants(tables: Any, *, level: int = MAX_LEVEL_MIDNIGHT) -> dict[str, Any]:
    """DUAL_WIELD / TITAN_GRIP spells a character learns through its *default skills*.

    Path at ``7f3d43b`` (not modelled by ``dummy_semantics.scope.Scope``, whose extended
    mode follows only SkillLine CategoryID 7 class lines):

    * ``ObjectMgr`` loads every ``SkillRaceClassInfo`` row with ``Availability == 1`` into
      ``PlayerInfo::skills`` for each matching race/class (ClassMask 0/-1 = all) -- ObjectMgr.cpp:4061-4070;
    * ``Player::LearnDefaultSkills`` -> ``LearnDefaultSkill`` for rows with ``MinLevel <= level``
      -- Player.cpp:25259-25275;
    * ``Player::LearnSkillRewardedSpells`` learns each ``SkillLineAbility`` of the skill with
      AcquireMethod 1/2 (4 only when its conditions hold), ``RaceMask`` / ``ClassMask`` (0 = all)
      matching and ``max(SpellLevel, BaseLevel) <= level`` -- Player.cpp:25391-25440.

    Returns ``{class_id: [grant, ...]}`` keyed by str(class) plus the rows used.
    """
    effects = tables("SpellEffect").group("SpellID")
    names = tables("SpellName").by("ID")
    levels: dict[int, int] = {}
    for row in tables("SpellLevels").rows:
        if int(row.get("DifficultyID", 0) or 0) == 0:
            levels[int(row["SpellID"])] = max(int(row["SpellLevel"] or 0), int(row["BaseLevel"] or 0))
    kind_of: dict[int, str] = {}
    for spell, rows in effects.items():
        for e in rows:
            if int(e["Effect"]) == SPELL_EFFECT_DUAL_WIELD:
                kind_of[int(spell)] = "dual_wield"
            elif int(e["Effect"]) == SPELL_EFFECT_TITAN_GRIP:
                kind_of.setdefault(int(spell), "titan_grip")
    srci = tables("SkillRaceClassInfo").group("SkillID")
    out: dict[str, list[dict[str, Any]]] = {}
    for sla in tables("SkillLineAbility").rows:
        spell = int(sla["Spell"])
        if spell not in kind_of:
            continue
        method = int(sla["AcquireMethod"])
        if method not in SKILL_ACQUIRE_AUTOMATIC:
            continue
        req = levels.get(spell, 0)
        if req > level:
            continue
        sla_mask = int(sla["ClassMask"])
        for rc in srci.get(int(sla["SkillLine"]), []):
            if int(rc["Availability"]) != 1 or int(rc["MinLevel"]) > level:
                continue
            rc_mask = int(rc["ClassMask"])
            for cls in PLAYER_CLASS_IDS:
                bit = 1 << (cls - 1)
                if rc_mask not in (0, -1) and not rc_mask & bit:
                    continue
                if sla_mask and not sla_mask & bit:
                    continue
                race_masks = [int(rc.get("RaceMasks_0", -1)), int(rc.get("RaceMasks_1", -1))]
                out.setdefault(str(cls), []).append({
                    "spell": spell, "name": names.get(spell, {}).get("Name_lang", ""), "kind": kind_of[spell],
                    "skill_line": int(sla["SkillLine"]), "skill_line_ability": int(sla["ID"]),
                    "skill_race_class_info": int(rc["ID"]), "acquire_method": SKILL_ACQUIRE_AUTOMATIC[method],
                    "conditional": method == 4, "required_level": req,
                    "race_masks": race_masks, "race_restricted": race_masks != [-1, -1],
                    "coordinates": ["ObjectMgr.cpp:4061-4070", "Player.cpp:25259-25275", "Player.cpp:25391-25440"],
                    "evidence_class": "trinity-consumer"})
    for cls in out:
        out[cls].sort(key=lambda g: (g["spell"], g["skill_race_class_info"]))
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


# ---------------------------------------------------------------------------
# prepared weapon set
# ---------------------------------------------------------------------------

@dataclass
class WeaponItem:
    item_id: int
    name: str
    class_id: int
    subclass: int
    subclass_name: str
    inventory_type: int
    item_level: int
    context: int
    delay_ms: int
    dmg_variance: float
    dps: float
    min_damage_f32: float
    max_damage_f32: float
    min_damage_gearing: float
    max_damage_gearing: float
    weapon_attack_power: int
    damage_type: int
    school_mask: int
    always_allow_dual_wield: bool
    is_ranged_weapon: bool
    provenance: list[str] = field(default_factory=list)


@dataclass
class PreparedHand:
    attack_type: int
    attack_type_name: str
    weapon: WeaponItem | None
    weapon_min: float
    weapon_max: float
    base_attack_time_ms: int
    attack_time_source: str
    ap_multiplier: float
    ap_multiplier_branch: str
    normalized_speed: float
    normalized_speed_branch: str
    ap_term_per_point: float
    min_max: dict[str, Any]
    urand_bounds: tuple[int, int]
    school_mask: int
    notes: list[str] = field(default_factory=list)


def load_weapon_item(resolver: Any, item_id: int, *, player_level: int, context: int,
                     bonus_list_ids: tuple[int, ...] = ()) -> WeaponItem:
    from gearing.resolver import Variant
    variant = Variant(label=f"context {context}", context=context)
    resolved = resolver.resolve(item_id, variant, player_level=player_level,
                                extra_bonus_lists=bonus_list_ids)
    proto = resolver.items.get(item_id)
    sparse = proto.sparse
    flags2 = int(sparse.get("Flags_2", 0) or 0)
    damage_type = int(sparse.get("DamageType", 0) or 0)
    weapon = resolved.weapon
    if weapon is None:
        min_g = max_g = dps = 0.0
        delay = int(proto.delay)
        variance = 0.0
    else:
        min_g, max_g, dps = weapon["min_damage"], weapon["max_damage"], weapon["dps"]
        delay = int(weapon["speed_ms"])
        variance = float(weapon["dmg_variance"])
    return WeaponItem(
        item_id=item_id, name=resolved.name, class_id=proto.class_id, subclass=proto.subclass_id,
        subclass_name=SUBCLASS_NAMES.get(proto.subclass_id, str(proto.subclass_id)) if proto.class_id == ITEM_CLASS_WEAPON else f"armor/{proto.subclass_id}",
        inventory_type=proto.inventory_type, item_level=resolved.effective_item_level, context=context,
        delay_ms=delay, dmg_variance=variance, dps=f32(dps),
        min_damage_f32=f32(min_g), max_damage_f32=f32(max_g),
        min_damage_gearing=min_g, max_damage_gearing=max_g,
        weapon_attack_power=weapon_attack_power(dps) if weapon else 0,
        damage_type=damage_type, school_mask=1 << damage_type,
        always_allow_dual_wield=bool(flags2 & ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD),
        is_ranged_weapon=is_ranged_weapon(proto.class_id, proto.subclass_id),
        provenance=[
            f"gearing.resolver.GearResolver.resolve({item_id}, context={context}, player_level={player_level}) -> "
            f"effective item level {resolved.effective_item_level}",
            "min/max/dps: ItemTemplate::GetDamage via gearing.scaling (double); rounded to binary32 here at the "
            "SetBaseWeaponDamage boundary (Unit.h:1573 `float m_weaponDamage[MAX_ATTACK][2]`, Player.cpp:8149-8156)",
            "weapon attack power: Player.cpp:8163 int32(GetDPS * 6.0f)",
        ],
    )


@dataclass
class EquipState:
    main_hand: WeaponItem | None
    off_hand: WeaponItem | None
    dual_wield: bool
    titan_grip: bool
    titan_grip_subclass_mask: int
    equip_errors: list[str]
    two_hand_used: bool
    dual_wielding: bool
    ranged_main_hand: bool


def check_equip(main_hand: WeaponItem | None, off_hand: WeaponItem | None, *, dual_wield: bool,
                titan_grip: bool, titan_grip_subclass_mask: int) -> EquipState:
    """The ``Player::CanEquipItem`` gates that decide whether the pair is legal
    (Player.cpp:10850-10877) plus ``IsTwoHandUsed`` (Player.cpp:13189-13198)."""
    errors: list[str] = []

    def can_titan_grip(item: WeaponItem) -> bool:      # Player.cpp:13125-13147
        if not titan_grip:
            return False
        if item.class_id != ITEM_CLASS_WEAPON:
            return False
        return not titan_grip_subclass_mask or bool(titan_grip_subclass_mask & (1 << item.subclass))

    two_hand_used = False
    if main_hand is not None:
        it = main_hand.inventory_type
        two_hand_used = ((it == INVTYPE_2HWEAPON and not can_titan_grip(main_hand)) or it == INVTYPE_RANGED
                         or (it == INVTYPE_RANGEDRIGHT and main_hand.class_id == ITEM_CLASS_WEAPON and main_hand.subclass != 19))
    if off_hand is not None:
        it = off_hand.inventory_type
        if it == INVTYPE_WEAPON and off_hand.subclass == ITEM_SUBCLASS_WEAPON_POLEARM and off_hand.class_id == ITEM_CLASS_WEAPON:
            errors.append("EQUIP_ERR_WRONG_SLOT: polearm in off hand (Player.cpp:10852-10853)")
        elif it == INVTYPE_WEAPON and not dual_wield:
            errors.append("EQUIP_ERR_2HSKILLNOTFOUND: INVTYPE_WEAPON off hand needs CanDualWield (Player.cpp:10856-10857)")
        elif it == INVTYPE_WEAPONOFFHAND and not dual_wield and not off_hand.always_allow_dual_wield:
            errors.append("EQUIP_ERR_2HSKILLNOTFOUND: INVTYPE_WEAPONOFFHAND needs CanDualWield or ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD (Player.cpp:10861-10862)")
        elif it == INVTYPE_2HWEAPON and (not dual_wield or not can_titan_grip(off_hand)):
            errors.append("EQUIP_ERR_2HSKILLNOTFOUND: two-hand off hand needs CanDualWield and CanTitanGrip (Player.cpp:10866-10867)")
        if not errors and two_hand_used:
            # Player.cpp:10870-10871 -- applies to ANY off-hand item (shield/holdable included)
            errors.append("EQUIP_ERR_2HANDED_EQUIPPED: main hand IsTwoHandUsed (Player.cpp:10870-10871)")
    dual_wielding = (main_hand is not None and off_hand is not None and off_hand.class_id == ITEM_CLASS_WEAPON
                     and not errors)
    return EquipState(main_hand, off_hand, dual_wield, titan_grip, titan_grip_subclass_mask, errors,
                      two_hand_used, dual_wielding, main_hand is not None and main_hand.is_ranged_weapon)


def prepare_hands(state: EquipState, *, level: int, form_id: int | None, form_combat_round_time: int,
                  ap: int, ap_mod_pos: int, ap_mod_neg: int, ap_multiplier_value: float,
                  versatility_pct: float, mods: dict[str, Any]) -> list[PreparedHand]:
    """Per attack type: what ``_ApplyWeaponDamage``/``SetRegularAttackTime``/``InitDataForForm``
    leave in ``m_weaponDamage``/``m_baseAttackSpeed``, then ``CalculateMinMaxDamage``."""
    feral = form_id in FERAL_FORMS
    hands: list[PreparedHand] = []
    mh, oh = state.main_hand, state.off_hand
    for att in (BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK):
        notes: list[str] = []
        # GetWeaponForAttack(att, true): Player.cpp:9582-9609
        if att == OFF_ATTACK:
            item = oh if (oh is not None and oh.class_id == ITEM_CLASS_WEAPON and not oh.is_ranged_weapon) else None
        else:
            item = mh if (mh is not None and mh.class_id == ITEM_CLASS_WEAPON and mh.is_ranged_weapon == (att == RANGED_ATTACK)) else None
        if att == OFF_ATTACK and oh is not None and item is None:
            notes.append("off-hand slot holds a non-weapon (shield/holdable): GetWeaponForAttack(OFF_ATTACK) is null (Player.cpp:9597)")
        # m_weaponDamage: Player.cpp:8135-8157 (_ApplyWeaponDamage) -- keyed by GetAttackBySlot of the slot
        if item is not None:
            wmin, wmax = item.min_damage_f32, item.max_damage_f32
            weapon_source = f"item {item.item_id} via _ApplyWeaponDamage (Player.cpp:8146-8157)"
        else:
            wmin, wmax = BASE_MINDAMAGE, BASE_MAXDAMAGE
            weapon_source = "no weapon: Unit ctor BASE_MINDAMAGE/BASE_MAXDAMAGE (Unit.cpp:355-356)"
        # base attack time: Player.cpp:5387-5401 / 8160-8161 / 23388-23396
        if form_combat_round_time and att != RANGED_ATTACK:
            bat = form_combat_round_time
            bat_src = f"SpellShapeshiftForm.CombatRoundTime={form_combat_round_time} (Player.cpp:23391-23392)"
        elif item is not None and item.delay_ms:
            bat = item.delay_ms
            bat_src = f"ItemSparse.ItemDelay={item.delay_ms} (Player.cpp:5396, 8161)"
        else:
            bat = BASE_ATTACK_TIME
            bat_src = "BASE_ATTACK_TIME 2000 (Player.cpp:5399)"
        weapon_tuple = (item.delay_ms, item.subclass) if item is not None else None
        apm, apm_branch = ap_multiplier(is_player=True, feral=feral, base_attack_time_ms=bat, weapon=weapon_tuple, normalized=False)
        nspeed, nspeed_branch = ap_multiplier(is_player=True, feral=feral, base_attack_time_ms=bat, weapon=weapon_tuple, normalized=True)
        total_pct = f32(mods.get("total_pct", 1.0))
        if att == OFF_ATTACK:
            total_pct = mul(OFFHAND_PCT_FACTOR, total_pct)   # Unit.cpp:9793 then :9805 product
        inp = MinMaxInputs(
            att_type=att, normalized=False, add_total_pct=True,
            ap=ap, ap_mod_pos=ap_mod_pos, ap_mod_neg=ap_mod_neg, ap_multiplier=ap_multiplier_value,
            flat_base=0.0, base_pct=1.0, total_value=float(mods.get("total_value", 0.0)), total_pct=total_pct,
            weapon_min=wmin, weapon_max=wmax,
            have_offhand_weapon=(oh is not None and oh.class_id == ITEM_CLASS_WEAPON and not oh.is_ranged_weapon),
            versatility_rating_pct=versatility_pct, versatility_aura=int(mods.get("versatility_aura", 0)),
            # StatSystem.cpp:458-464: the CombatRoundTime branch applies to every attType, feral or not
            form_combat_round_time=form_combat_round_time,
            can_use_attack_type=True, feral=feral, is_player=True,
            base_attack_time_ms=bat, weapon=weapon_tuple)
        mm = calculate_min_max(inp)
        per_point = mul(div(lit("1.0"), AP_DIVISOR), max(apm, AP_MULTIPLIER_FLOOR))
        if att == OFF_ATTACK and item is None:
            notes.append("UpdateAttackPowerAndDamage only refreshes OFF_ATTACK when an off-hand weapon is equipped "
                         "(StatSystem.cpp:410-412); MinOffHandDamage stays at its Player::Create value 0.0f (Player.cpp:2400)")
        if att == RANGED_ATTACK and form_combat_round_time:
            notes.append("InitDataForForm sets RANGED_ATTACK base time to BASE_ATTACK_TIME (Player.cpp:23393); "
                         "the CombatRoundTime branch of CalculateMinMaxDamage still applies to every attType (StatSystem.cpp:459)")
        hands.append(PreparedHand(
            attack_type=att, attack_type_name=ATTACK_NAMES[att], weapon=item, weapon_min=wmin, weapon_max=wmax,
            base_attack_time_ms=bat, attack_time_source=bat_src, ap_multiplier=apm, ap_multiplier_branch=apm_branch,
            normalized_speed=nspeed, normalized_speed_branch=nspeed_branch, ap_term_per_point=per_point,
            min_max=mm, urand_bounds=urand_bounds(mm["min"], mm["max"]),
            school_mask=item.school_mask if item is not None else 1,
            notes=notes + [weapon_source]))
    return hands


# ---------------------------------------------------------------------------
# sources corpus
# ---------------------------------------------------------------------------

def weapon_sources_rows() -> list[dict[str, Any]]:
    """The field/consumer table of the report (section 1 and 2)."""
    tc = "trinity-consumer"
    return [
        {"field": "ItemSparse.ItemDelay", "source_type": "uint16 (DB2)", "consumer": "ItemTemplate::GetDelay -> Player::_ApplyWeaponDamage SetBaseAttackTime; Unit::GetAPMultiplier / 1000.0f", "intermediate_type": "uint32 -> float", "units": "ms", "coordinates": ["Player.cpp:8160-8161", "Unit.cpp:11061"], "evidence_class": tc},
        {"field": "ItemSparse.DmgVariance / ItemDamage*<ilvl>.Quality[q]", "source_type": "float (DB2 / GameTable)", "consumer": "ItemTemplate::GetDamage -> Player::SetBaseWeaponDamage(float m_weaponDamage[attType][MINDAMAGE/MAXDAMAGE])", "intermediate_type": "float", "rounding": "max: floor(x+0.5f); min: none (gearing archaeology s5)", "units": "damage", "coordinates": ["Player.cpp:8146-8157", "Unit.h:1573", "Unit.h:1940"], "evidence_class": tc},
        {"field": "ItemTemplate::GetDPS", "source_type": "float", "consumer": "int32(dps * 6.0f) -> UnitData::MainHand/OffHand/RangedWeaponAttackPower", "intermediate_type": "float -> int32 (truncation)", "units": "attack power", "coordinates": ["Player.cpp:8163-8176"], "evidence_class": tc, "note": "excluded from white-swing damage: CalculateMinMaxDamage calls GetTotalAttackPowerValue(attType, false) (StatSystem.cpp:447); included in GetTotalAttackPowerValue(attType) used by SpellDamageBonusDone AP coefficients (Unit.cpp:6873)"},
        {"field": "Item.SubclassID (weapon)", "source_type": "uint8", "consumer": "Unit::GetAPMultiplier normalized table; ItemTemplate::IsRangedWeapon; CanTitanGrip subclass mask", "intermediate_type": "float constants 3.3f/2.4f/1.7f/2.0f", "units": "seconds", "coordinates": ["Unit.cpp:11063-11088", "ItemTemplate.h:940-953", "Player.cpp:13125-13147"], "evidence_class": tc},
        {"field": "Item.InventoryType", "source_type": "uint8", "consumer": "Player::GetAttackBySlot (MAINHAND: RANGED/RANGEDRIGHT -> RANGED_ATTACK else BASE_ATTACK; OFFHAND -> OFF_ATTACK); IsTwoHandUsed; CanEquipItem gates", "coordinates": ["Player.cpp:9649-9657", "Player.cpp:13189-13198", "Player.cpp:10850-10877"], "evidence_class": tc},
        {"field": "ItemSparse.DamageType", "source_type": "uint8", "consumer": "Player::GetMeleeDamageSchoolMask = SpellSchoolMask(1 << DamageType)", "coordinates": ["Player.cpp:8183-8189"], "evidence_class": tc},
        {"field": "ItemSparse.Flags_2 & 0x80000 (ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD)", "source_type": "int32 bit", "consumer": "off-hand equip gate and OFF_ATTACK refresh", "coordinates": ["Player.cpp:10861", "StatSystem.cpp:411"], "evidence_class": tc},
        {"field": "SpellShapeshiftForm.CombatRoundTime", "source_type": "int16 (DB2)", "consumer": "Player::InitDataForForm -> SetBaseAttackTime(BASE/OFF); _ApplyWeaponDamage skips attack time; CalculateMinMaxDamage weapon * CRT / 1000.0f / attackPowerMod", "intermediate_type": "int16 -> uint32 / float", "units": "ms", "coordinates": ["Player.cpp:23388-23393", "Player.cpp:8160", "StatSystem.cpp:459-464"], "evidence_class": tc},
        {"field": "SpellShapeshiftForm.Flags & 0x20", "source_type": "int32 bit", "consumer": "Player::UpdateAttackPowerAndDamage adds AGI * AttackPowerPerStrength", "coordinates": ["StatSystem.cpp:362-365"], "evidence_class": tc},
        {"field": "ShapeshiftForm in {CAT 1, BEAR 5, DIRE_BEAR 8, GHOST_WOLF 16}", "source_type": "enum", "consumer": "Unit::IsInFeralForm -> GetAPMultiplier uses base attack time; CalculateDamage adds off-hand range", "coordinates": ["Unit.cpp:9539-9543", "Unit.cpp:11053-11054", "Unit.cpp:2509-2516", "Unit.cpp:2528-2532"], "evidence_class": tc},
        {"field": "ChrClasses.AttackPowerPerStrength / AttackPowerPerAgility / RangedAttackPowerPerAgility", "source_type": "float (DB2)", "consumer": "Player::UpdateAttackPowerAndDamage -> SetStatFlatModifier(UNIT_MOD_ATTACK_POWER[_RANGED], BASE_VALUE)", "intermediate_type": "float", "coordinates": ["StatSystem.cpp:356-373"], "evidence_class": tc, "note": "already covered by character-stat-pipeline-archaeology.md s5; reused, not re-derived"},
        {"field": "UnitData.AttackPower / AttackPowerModPos / AttackPowerModNeg / AttackPowerMultiplier (+Ranged*)", "source_type": "int32 / int32 / int32 / float", "consumer": "Unit::GetTotalAttackPowerValue: float ap = int32 sum; (+weapon AP; OFF: /2) ; ap * (1.0f + mult)", "intermediate_type": "int32 sum -> float", "coordinates": ["Unit.cpp:9919-9947", "UpdateFields.h:401-413", "StatSystem.cpp:379-393"], "evidence_class": tc},
        {"field": "UNIT_MOD_DAMAGE_{MAINHAND,OFFHAND,RANGED} BASE_VALUE / BASE_PCT", "source_type": "float modifier", "consumer": "CalculateMinMaxDamage baseValue / basePct", "coordinates": ["StatSystem.cpp:447-448"], "evidence_class": "structural-inference", "note": "no Player/StatSystem setter for the damage BASE_VALUE/BASE_PCT: players see 0.0f / 1.0f"},
        {"field": "UNIT_MOD_DAMAGE_* TOTAL_VALUE", "source_type": "float modifier", "consumer": "Unit::UpdateDamageDoneMods: SPELL_AURA_MOD_DAMAGE_DONE (physical, CheckAttackFitToAuraRequirement) + Player: ITEM_ENCHANTMENT_TYPE_DAMAGE EffectScalingPoints (+TOTEM for shaman * delay/1000)", "coordinates": ["Unit.cpp:9745-9773", "Player.cpp:4921-4973"], "evidence_class": tc},
        {"field": "UNIT_MOD_DAMAGE_* TOTAL_PCT", "source_type": "float modifier", "consumer": "Unit::UpdateDamagePctDoneMods: factor (OFF_ATTACK 0.5f) * prod SPELL_AURA_MOD_DAMAGE_PERCENT_DONE(physical) * (OFF) prod SPELL_AURA_MOD_OFFHAND_DAMAGE_PCT", "intermediate_type": "float product", "coordinates": ["Unit.cpp:9781-9819"], "evidence_class": tc},
        {"field": "CR_VERSATILITY_DAMAGE_DONE rating + SPELL_AURA_MOD_VERSATILITY", "source_type": "float pct + int32", "consumer": "CalculateMinMaxDamage AddPct(versaDmgMod, rating + float(aura))", "coordinates": ["StatSystem.cpp:455-457"], "evidence_class": tc, "note": "versatility is folded into the prepared min/max, not into MeleeDamageBonusDone"},
        {"field": "UNIT_FLAG_DISARMED / UNIT_FLAG2_DISARM_OFFHAND / _RANGED", "source_type": "unit flags", "consumer": "Unit::CanUseAttackType -> BASE_MINDAMAGE/BASE_MAXDAMAGE or 0/0", "coordinates": ["Unit.cpp:2605-2618", "StatSystem.cpp:465-476"], "evidence_class": tc},
        {"field": "SPELL_EFFECT_DUAL_WIELD (40) / SPELL_EFFECT_TITAN_GRIP (155)", "source_type": "spell effect", "consumer": "Spell::EffectDualWield -> SetCanDualWield(true); Spell::EffectTitanGrip -> SetCanTitanGrip(true, MiscValue penalty, EquippedItemClass, SubClassMask); unlearn resets", "coordinates": ["SpellEffects.cpp:2237-2243", "SpellEffects.cpp:4954-4961", "Player.cpp:3344-3357"], "evidence_class": tc},
        {"field": "GetMaxLevelForExpansion(EXPANSION_MIDNIGHT) = 90", "source_type": "constexpr", "consumer": "CalcArmorReducedDamage item-level diminishing gate", "coordinates": ["SharedDefines.h:109-140", "Unit.cpp:1758-1760"], "evidence_class": tc},
        {"field": "ExpectedStat.ArmorConstant (Lvl, ExpansionID=-2)", "source_type": "float (DB2)", "consumer": "DB2Manager::EvaluateExpectedStat(ArmorConstant, attackerLevel, -2, 0, class, 0) -- class mod only for creature attackers", "coordinates": ["Unit.cpp:1755", "DB2Stores.cpp EvaluateExpectedStat", "DB2Stores.cpp:1319"], "evidence_class": "db2-fact", "value_level_90": 3430.0},
        {"field": "GameTable ItemLevelByLevel.txt", "source_type": "GameTable float", "consumer": "itemLevelDelta = AvgItemLevel[EquippedBase] - ItemLevelByLevel[90].ItemLevel -> GlobalCurve 18 (CurveID 27400)", "coordinates": ["Unit.cpp:1762-1764", "GameTables.cpp:127"], "evidence_class": "unresolved", "note": "ItemLevelByLevel.txt is absent from data/tables (14 GameTables present); ASSERT_NOTNULL in Trinity"},
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_context() -> tuple[Any, Any]:
    from gearing.resolver import GearResolver
    from gearing.tables import DEFAULT_TABLES, Tables
    tables = Tables(DEFAULT_TABLES)
    return tables, GearResolver(tables)


def _form_crt(tables: Any, form_id: int | None) -> tuple[int, str]:
    if form_id is None:
        return 0, "no form"
    row = tables("SpellShapeshiftForm").lookup(form_id)
    if row is None:
        raise SourceError(f"SpellShapeshiftForm {form_id} is not in the snapshot")
    return int(row["CombatRoundTime"]), f"SpellShapeshiftForm {form_id} ({row['Name_lang']}) CombatRoundTime={row['CombatRoundTime']}"


def _sources_capability() -> dict[str, Any] | None:
    path = CORPORA / "weapon-sources.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    cap = data.get("capability_by_spec")
    if cap is not None:
        cap = dict(cap, learned_only=data.get("capability_by_spec_learned_only") or {"dual_wield": {}, "titan_grip": {}})
    return cap


def _dual_wield_from_capability(cap: dict[str, Any], spec_key: str, learned: set[int]) -> tuple[bool, str]:
    """Automatic grants decide; an AcquireMethod-0 grant counts only when supplied as learned."""
    if spec_key in cap["dual_wield"]:
        return True, f"weapon-sources.json capability_by_spec.dual_wield[{spec_key}]"
    rows = (cap.get("learned_only") or {}).get("dual_wield", {}).get(spec_key, [])
    hit = sorted(r["spell"] for r in rows if r["spell"] in learned)
    if hit:
        return True, f"learned spell {hit} (weapon-sources.json capability_by_spec_learned_only.dual_wield[{spec_key}])"
    if rows:
        return False, (f"no automatic grant; learned-only grant {[r['spell'] for r in rows]} not supplied as learned "
                       "(character state, fail closed)")
    return False, f"no grant for spec {spec_key} in weapon-sources.json"


def hand_to_dict(hand: PreparedHand) -> dict[str, Any]:
    d = asdict(hand)
    d["urand_bounds"] = list(hand.urand_bounds)
    return d


def cmd_weapon(args: argparse.Namespace) -> int:
    tables, resolver = _load_context()
    raw = json.loads(Path(args.loadout).read_text(encoding="utf-8"))
    entries = raw["items"] if isinstance(raw, dict) else raw
    level = int(args.level)
    mh: WeaponItem | None = None
    oh: WeaponItem | None = None
    for e in entries:
        item = load_weapon_item(resolver, int(e["item_id"]), player_level=level,
                                context=int(e.get("context", args.context)), bonus_list_ids=tuple(e.get("bonus_list_ids", ())))
        slot = e.get("slot")
        if slot is None:
            if item.class_id == ITEM_CLASS_WEAPON and mh is None and item.inventory_type != INVTYPE_WEAPONOFFHAND:
                slot = "main_hand"
            elif item.inventory_type in (INVTYPE_WEAPON, INVTYPE_WEAPONOFFHAND, INVTYPE_SHIELD, INVTYPE_HOLDABLE, INVTYPE_2HWEAPON) and oh is None:
                slot = "off_hand"
            else:
                continue
        if slot == "main_hand":
            mh = item
        elif slot == "off_hand":
            oh = item
        else:
            raise SourceError(f"unknown slot {slot!r} (main_hand|off_hand)")
    cap = _sources_capability()
    spec_key = str(args.spec)
    if args.dual_wield is None:
        if cap is None:
            print(json.dumps({"unresolved": "dual-wield capability: pass --dual-wield/--no-dual-wield or generate weapon-sources.json"}, indent=1))
            return 2
        learned = {int(x) for x in (args.learned_spell or [])}
        dual_wield, dw_src = _dual_wield_from_capability(cap, spec_key, learned)
    else:
        dual_wield, dw_src = bool(args.dual_wield), "command line"
    tg_mask = 0
    if args.titan_grip is None:
        titan_grip = bool(cap and spec_key in cap["titan_grip"])
        if titan_grip:
            tg_mask = cap["titan_grip"][spec_key][0]["equipped_item_subclass_mask"]
        tg_src = f"weapon-sources.json capability_by_spec.titan_grip[{spec_key}]"
    else:
        titan_grip, tg_src = bool(args.titan_grip), "command line"
        tg_mask = int(args.titan_grip_mask)
    state = check_equip(mh, oh, dual_wield=dual_wield, titan_grip=titan_grip, titan_grip_subclass_mask=tg_mask)
    crt, crt_src = _form_crt(tables, args.form)
    mods = json.loads(args.mods) if args.mods else {}
    hands = prepare_hands(state, level=level, form_id=args.form, form_combat_round_time=crt,
                          ap=args.ap, ap_mod_pos=args.ap_mod_pos, ap_mod_neg=args.ap_mod_neg,
                          ap_multiplier_value=args.ap_mult, versatility_pct=args.versatility_pct, mods=mods)
    out = {
        "provenance": provenance("weapon_combat.py weapon " + " ".join(sys.argv[2:])),
        "inputs": {"class": args.class_id, "spec": args.spec, "level": level, "form": args.form, "form_combat_round_time": crt_src,
                   "attack_power": {"AttackPower": args.ap, "AttackPowerModPos": args.ap_mod_pos, "AttackPowerModNeg": args.ap_mod_neg,
                                    "AttackPowerMultiplier": args.ap_mult, "source": "supplied (prepared AP is Track C/charstats scope)"},
                   "versatility_pct": args.versatility_pct, "mods": mods},
        "capability": {"dual_wield": dual_wield, "dual_wield_source": dw_src, "titan_grip": titan_grip,
                       "titan_grip_source": tg_src, "titan_grip_subclass_mask": tg_mask},
        "equip_state": {"two_hand_used": state.two_hand_used, "dual_wielding": state.dual_wielding,
                        "ranged_main_hand": state.ranged_main_hand, "equip_errors": state.equip_errors},
        "hands": [hand_to_dict(h) for h in hands],
    }
    if state.equip_errors:
        out["unresolved"] = "the pair fails Player::CanEquipItem; hands are reported for the main hand only as Trinity would never equip the off hand"
    print(json.dumps(out, indent=1))
    return 1 if state.equip_errors else 0


def cmd_sources(args: argparse.Namespace) -> int:
    from dummy_semantics.loaders import Bundle
    from dummy_semantics.scope import Scope
    bundle = Bundle()
    scope = Scope(bundle)
    scope_ext = Scope(bundle, include_class_skills=True)
    cap_default = capability_by_spec(scope, bundle.catalog)
    cap_ext = capability_by_spec(scope_ext, bundle.catalog)
    from gearing.tables import DEFAULT_TABLES, Tables
    snapshot_tables = Tables(DEFAULT_TABLES)
    ext_spells = {r["spell"] for key in cap_ext for rows in cap_ext[key].values() for r in rows}
    learned_only = learned_only_skill_line_spells(snapshot_tables, ext_spells)
    cap: dict[str, dict[str, list[dict[str, Any]]]] = {"dual_wield": {}, "titan_grip": {}}
    cap_learned: dict[str, dict[str, list[dict[str, Any]]]] = {"dual_wield": {}, "titan_grip": {}}
    for key in cap:
        for spec, rows in cap_ext[key].items():
            for r in rows:
                if any(r["spell"] == d["spell"] for d in cap_default[key].get(spec, [])):
                    cap[key].setdefault(spec, []).append({**r, "scope": "default"})
                elif r["spell"] in learned_only:
                    # AcquireMethod 0 on a class skill line: character state, not a capability grant
                    cap_learned[key].setdefault(spec, []).append({
                        **r, "scope": "learned_spell_state", "acquire_method": "Learned",
                        "skill_line_ability": learned_only[r["spell"]],
                        "coordinates": ["Player.cpp:25404-25417", "DBCEnums.h:2362"]})
                else:
                    cap[key].setdefault(spec, []).append({**r, "scope": "class_skill_lines"})
    skill_grants = default_skill_grants(snapshot_tables)
    for spec, cls in sorted(scope.roots.class_of_spec.items()):
        for g in skill_grants.get(str(cls), []):
            rows = cap[g["kind"]].setdefault(str(spec), [])
            if any(r["spell"] == g["spell"] and r["scope"] == "default_skill" for r in rows):
                continue
            rows.append({"spell": g["spell"], "name": g["name"], "scope": "default_skill",
                         "skill_line": g["skill_line"], "skill_race_class_info": g["skill_race_class_info"],
                         "race_restricted": g["race_restricted"]})
    cap = {k: dict(sorted(v.items(), key=lambda kv: int(kv[0]))) for k, v in cap.items()}
    cap_learned = {k: dict(sorted(v.items(), key=lambda kv: int(kv[0]))) for k, v in cap_learned.items()}
    out = {
        "provenance": provenance("python3 weapon_combat.py sources"),
        "constants": {
            "BASE_MINDAMAGE": 1.0, "BASE_MAXDAMAGE": 2.0, "BASE_ATTACK_TIME_ms": BASE_ATTACK_TIME,
            "AP_DIVISOR": 3.5, "AP_MULTIPLIER_FLOOR": 0.25, "NO_WEAPON_AP_MULTIPLIER": 2.0,
            "WEAPON_AP_PER_DPS": 6.0, "OFFHAND_PCT_FACTOR": 0.5, "MAX_LEVEL": MAX_LEVEL_MIDNIGHT,
            "normalized_speed_by_subclass": {SUBCLASS_NAMES[k]: v for k, v in sorted(NORMALIZED_SPEED_BY_SUBCLASS.items())},
            "types": "all float literals are C++ `float`; BASE_ATTACK_TIME is uint32 ms; CombatRoundTime int16",
        },
        "sources": weapon_sources_rows(),
        "capability_by_spec": cap,
        "capability_by_spec_default_scope": cap_default,
        "capability_by_spec_learned_only": cap_learned,
        "capability_learned_only_note": "grants reachable only through a class skill line whose every SkillLineAbility row has AcquireMethod 0 "
                                        "(Learned): LearnSkillRewardedSpells never learns them (Player.cpp:25404-25417), so they are learned-spell "
                                        "character state (e.g. 296087 Dual Wield, taught only by 296088 'Learn Dual Wield'; no automatic source in "
                                        "the snapshot). They are NOT counted by `weapon`, the Track C hook or the witnesses unless the caller "
                                        "supplies the spell as learned (--learned-spell / identity.learned_spells). "
                                        "See character-preparation-closure.md lead reconciliation.",
        "capability_note": "specs reaching SPELL_EFFECT_DUAL_WIELD / SPELL_EFFECT_TITAN_GRIP; each grant is tagged with the path that reaches it: `default` (class trees + spec spells + current gear, dummy_semantics.scope.Scope), `class_skill_lines` (only the extended Scope(include_class_skills=True): SkillLine CategoryID 7 class lines with an automatic AcquireMethod; AcquireMethod-0 grants are moved to capability_by_spec_learned_only) or `default_skill` (Trinity's default-skill path, see default_skill_grants: SkillRaceClassInfo Availability 1 -> LearnDefaultSkills -> LearnSkillRewardedSpells; e.g. 674 Dual Wield on SkillLine 118 (CategoryID 6, so outside both Scope modes) for classes 3/4/12). Specs absent from all three have no grant and `weapon` reports EQUIP_ERR_2HSKILLNOTFOUND for a one-hand off-hand weapon",
        "default_skill_grants_by_class": skill_grants,
        "scope_summary": {"default": {"reachable": len(scope.reach), "specs": len(scope.roots.class_of_spec)},
                          "class_skill_lines": {"reachable": len(scope_ext.reach)}},
    }
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "weapon-sources.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} sha256={sha256_file(path)} dual_wield_specs={sorted(cap['dual_wield'])} titan_grip_specs={sorted(cap['titan_grip'])}")
    return 0


# ---------------------------------------------------------------------------
# Track C hook (character_prep.compiler WEAPON_HOOK)
# ---------------------------------------------------------------------------

_HOOK_CACHE: dict[str, Any] = {}


def _item_from_facts(proto: Any, facts: dict[str, Any], level: int) -> WeaponItem:
    sparse = proto.sparse
    flags2 = int(sparse.get("Flags_2", 0) or 0)
    damage_type = int(sparse.get("DamageType", 0) or 0)
    dps = float(facts.get("dps") or 0.0)
    mn, mx = float(facts.get("min_damage") or 0.0), float(facts.get("max_damage") or 0.0)
    return WeaponItem(
        item_id=int(facts["item_id"]), name=str(facts.get("name", "")), class_id=proto.class_id, subclass=proto.subclass_id,
        subclass_name=SUBCLASS_NAMES.get(proto.subclass_id, str(proto.subclass_id)), inventory_type=proto.inventory_type,
        item_level=int(facts.get("effective_item_level", 0) or 0), context=-1,
        delay_ms=int(facts.get("speed_ms") or proto.delay), dmg_variance=float(facts.get("dmg_variance") or 0.0),
        dps=f32(dps), min_damage_f32=f32(mn), max_damage_f32=f32(mx), min_damage_gearing=mn, max_damage_gearing=mx,
        weapon_attack_power=weapon_attack_power(dps), damage_type=damage_type, school_mask=1 << damage_type,
        always_allow_dual_wield=bool(flags2 & ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD),
        is_ranged_weapon=is_ranged_weapon(proto.class_id, proto.subclass_id),
        provenance=["character_prep gear facts (gearing.resolver weapon dict) rounded to binary32 at SetBaseWeaponDamage"])


def CHARACTER_PREP_HOOK(context: dict[str, Any]) -> dict[str, Any]:  # noqa: N802 - name fixed by character_prep
    """Prepared weapon state for a compiled fixture (Track C join).

    ``context['section']['slots']`` carries the gearing weapon facts per slot
    (MAINHAND/OFFHAND); identity gives class/spec/level.  Attack power is not a
    fixture input at this pass, so the min/max are reported at AP = 0 together
    with the exact per-AP-point coefficient; AP itself is ``unresolved``.
    """
    identity = context.get("identity") or {}
    level = int(identity.get("level", MAX_LEVEL_MIDNIGHT))
    spec = int((identity.get("spec") or {}).get("spec_id", 0))
    slots = (context.get("section") or {}).get("slots") or {}
    if "tables" not in _HOOK_CACHE:
        from gearing.resolver import GearResolver
        from gearing.tables import DEFAULT_TABLES, Tables
        _HOOK_CACHE["tables"] = Tables(DEFAULT_TABLES)
        _HOOK_CACHE["resolver"] = GearResolver(_HOOK_CACHE["tables"])
    resolver = _HOOK_CACHE["resolver"]
    items: dict[str, WeaponItem | None] = {"MAINHAND": None, "OFFHAND": None}
    for slot in items:
        facts = slots.get(slot)
        if facts:
            items[slot] = _item_from_facts(resolver.items.get(int(facts["item_id"])), facts, level)
    cap = _sources_capability()
    if cap is None:
        return {"evidence_class": "unresolved", "unresolved": "weapon-sources.json absent: dual-wield/Titan's Grip capability unknown"}
    key = str(spec)
    dual_wield = _dual_wield_from_capability(cap, key, _learned_spell_ids(identity))[0]
    titan_grip = key in cap["titan_grip"]
    tg_mask = cap["titan_grip"][key][0]["equipped_item_subclass_mask"] if titan_grip else 0
    state = check_equip(items["MAINHAND"], items["OFFHAND"], dual_wield=dual_wield, titan_grip=titan_grip,
                        titan_grip_subclass_mask=tg_mask)
    hands = prepare_hands(state, level=level, form_id=None, form_combat_round_time=0, ap=0, ap_mod_pos=0, ap_mod_neg=0,
                          ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
    return {
        "evidence_class": "trinity-probe" if not state.equip_errors else "unresolved",
        "coordinates": ["StatSystem.cpp:427-480", "Unit.cpp:11051-11088", "Player.cpp:8135-8181", "Player.cpp:10850-10871"],
        "capability": {"dual_wield": dual_wield, "titan_grip": titan_grip, "titan_grip_subclass_mask": tg_mask,
                       "source": "weapon-sources.json capability_by_spec"},
        "equip_state": {"two_hand_used": state.two_hand_used, "dual_wielding": state.dual_wielding,
                        "ranged_main_hand": state.ranged_main_hand, "equip_errors": state.equip_errors},
        "hands": [{"attack_type": h.attack_type_name, "weapon_item": h.weapon.item_id if h.weapon else None,
                   "base_attack_time_ms": h.base_attack_time_ms, "ap_multiplier": h.ap_multiplier,
                   "normalized_speed": h.normalized_speed, "ap_term_per_point": h.ap_term_per_point,
                   "min_at_ap0": h.min_max["min"], "max_at_ap0": h.min_max["max"], "urand_bounds_at_ap0": list(h.urand_bounds),
                   "total_pct": h.min_max["total_pct"], "school_mask": h.school_mask} for h in hands],
        "unresolved": ["attack power: not a fixture input; min/max at AP=0 (Track C/F owns prepared AP)",
                       "forms and aura-modified stages (TOTAL_VALUE/TOTAL_PCT/versatility) are not applied by the hook"],
    }


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("weapon", help="prepared MH/OH/ranged parameters for a loadout (B1)")
    p.add_argument("--loadout", required=True, help="gearing loadout JSON (items[].item_id, optional slot main_hand|off_hand, context)")
    p.add_argument("--class", dest="class_id", type=int, required=True)
    p.add_argument("--spec", type=int, required=True)
    p.add_argument("--level", type=int, required=True)
    p.add_argument("--form", type=int, default=None, help="SpellShapeshiftForm ID")
    p.add_argument("--context", type=int, default=3, help="default ItemContext for entries without one (3 = Raid_Normal)")
    p.add_argument("--ap", type=int, default=0, help="UnitData::AttackPower (supplied)")
    p.add_argument("--ap-mod-pos", type=int, default=0)
    p.add_argument("--ap-mod-neg", type=int, default=0)
    p.add_argument("--ap-mult", type=float, default=0.0, help="UnitData::AttackPowerMultiplier")
    p.add_argument("--versatility-pct", type=float, default=0.0, help="GetRatingBonusValue(CR_VERSATILITY_DAMAGE_DONE)")
    p.add_argument("--mods", default=None, help='JSON {"total_value":..,"total_pct":..,"versatility_aura":..}')
    p.add_argument("--dual-wield", dest="dual_wield", action="store_true", default=None)
    p.add_argument("--no-dual-wield", dest="dual_wield", action="store_false")
    p.add_argument("--learned-spell", dest="learned_spell", type=int, action="append", default=None,
                   help="spell ID the character has learned (character state; e.g. 296087 for an Arms/Protection dual wielder)")
    p.add_argument("--titan-grip", dest="titan_grip", action="store_true", default=None)
    p.add_argument("--titan-grip-mask", type=int, default=0)
    p.set_defaults(func=cmd_weapon)

    s = subparsers.add_parser("sources", help="write weapon-sources.json (field/consumer table + DW/TG capability by spec)")
    s.set_defaults(func=cmd_sources)
