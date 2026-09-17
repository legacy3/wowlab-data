"""B5 -- special weapon attacks (``Spell::EffectWeaponDmg``) in current player scope.

Classifies every reachable spell that carries one of the four weapon effects
(``SPELL_EFFECT_WEAPON_DAMAGE`` 58, ``_NOSCHOOL`` 17, ``NORMALIZED_WEAPON_DMG``
121, ``WEAPON_PERCENT_DAMAGE`` 31; ``Spell::EffectWeaponDmg``
SpellEffects.cpp:2812-2949), pins how the attack type is chosen
(``SpellInfo::GetAttackType`` SpellInfo.cpp:1931-1955), what equipment the
spell requires (``SpellInfo::EquippedItem*`` and
``Item::IsFitToSpellRequirements`` Item.cpp), whether the spell is queued on
the next swing (``SpellInfo::IsNextMeleeSwingSpell`` SpellInfo.cpp:1899-1902),
and the AP-only path (``Unit::SpellDamageBonusDone`` Unit.cpp:6851-6874 with
``SpellEffect.BonusCoefficientFromAP``).

Scope: ``dummy_semantics.scope.Scope`` (class trees + spec spells + current
gear/sets/gems/enchants, ``include_class_skills=False``), reused unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from typing import Any

from procs.enums import attr, effect_name

from . import CORPORA
from .weapon import ATTACK_NAMES, BASE_ATTACK, OFF_ATTACK, RANGED_ATTACK, provenance, sha256_file

WEAPON_EFFECTS = {58: "WEAPON_DAMAGE", 17: "WEAPON_DAMAGE_NOSCHOOL", 121: "NORMALIZED_WEAPON_DMG", 31: "WEAPON_PERCENT_DAMAGE"}
SPELL_EFFECT_SCHOOL_DAMAGE = 2
SPELL_AURA_PERIODIC_DAMAGE = 3

SPELL_DAMAGE_CLASS = {0: "NONE", 1: "MAGIC", 2: "MELEE", 3: "RANGED"}   # SharedDefines.h:3143-3146
SPELLFAMILY_HUNTER = 9                                                   # SharedDefines.h:7064
ITEM_SUBCLASS_MASK_WEAPON_RANGED = (1 << 2) | (1 << 3) | (1 << 18)       # ItemTemplate.h:540-542
HUNTER_EXCLUDED_FLAG_BIT = 32 + 28                                       # SpellFamilyFlags[1] & 0x10000000 (SpellInfo.cpp:1906)

ATTR3_REQUIRES_OFF_HAND = attr("SPELL_ATTR3_REQUIRES_OFF_HAND_WEAPON")
ATTR3_REQUIRES_MAIN_HAND = attr("SPELL_ATTR3_REQUIRES_MAIN_HAND_WEAPON")
ATTR0_USES_RANGED_SLOT = attr("SPELL_ATTR0_USES_RANGED_SLOT")
ATTR2_AUTO_REPEAT = attr("SPELL_ATTR2_AUTO_REPEAT")
ATTR0_ON_NEXT_SWING = attr("SPELL_ATTR0_ON_NEXT_SWING")
ATTR0_ON_NEXT_SWING_NO_DAMAGE = attr("SPELL_ATTR0_ON_NEXT_SWING_NO_DAMAGE")
ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS = attr("SPELL_ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS")
ATTR3_NO_AVOIDANCE = attr("SPELL_ATTR3_NO_AVOIDANCE")


def is_ranged_weapon_spell(info: Any) -> bool:
    """Mirrors ``SpellInfo::IsRangedWeaponSpell`` (SpellInfo.cpp:1904-1909)."""
    return ((info.family == SPELLFAMILY_HUNTER and not (info.family_flags >> HUNTER_EXCLUDED_FLAG_BIT) & 1)
            or bool(info.equipped_item_subclass_mask & ITEM_SUBCLASS_MASK_WEAPON_RANGED)
            or info.has_attr(ATTR0_USES_RANGED_SLOT))


def attack_type(info: Any) -> tuple[int, str]:
    """Mirrors ``SpellInfo::GetAttackType`` (SpellInfo.cpp:1931-1955)."""
    if info.dmg_class == 2:
        if info.has_attr(ATTR3_REQUIRES_OFF_HAND):
            return OFF_ATTACK, "DmgClass MELEE + SPELL_ATTR3_REQUIRES_OFF_HAND_WEAPON (SpellInfo.cpp:1937-1938)"
        return BASE_ATTACK, "DmgClass MELEE (SpellInfo.cpp:1940)"
    if info.dmg_class == 3:
        if is_ranged_weapon_spell(info):
            return RANGED_ATTACK, "DmgClass RANGED + IsRangedWeaponSpell (SpellInfo.cpp:1943)"
        return BASE_ATTACK, "DmgClass RANGED but not IsRangedWeaponSpell (SpellInfo.cpp:1943)"
    if info.has_attr(ATTR2_AUTO_REPEAT):
        return RANGED_ATTACK, "auto-repeat (wand) (SpellInfo.cpp:1947-1948)"
    return BASE_ATTACK, f"DmgClass {SPELL_DAMAGE_CLASS.get(info.dmg_class)} default (SpellInfo.cpp:1950)"


def ap_coefficient_attack_type(info: Any) -> int:
    """The lambda in ``Unit::SpellDamageBonusDone`` (Unit.cpp:6861-6870) -- note it differs
    from ``GetAttackType``: REQUIRES_MAIN_HAND overrides REQUIRES_OFF_HAND there."""
    if is_ranged_weapon_spell(info) and info.dmg_class != 2:
        return RANGED_ATTACK
    if info.has_attr(ATTR3_REQUIRES_OFF_HAND) and not info.has_attr(ATTR3_REQUIRES_MAIN_HAND):
        return OFF_ATTACK
    return BASE_ATTACK


def load_effect_columns(source: Any) -> dict[int, dict[str, Any]]:
    cols = ("ID", "BonusCoefficientFromAP", "EffectBasePointsF", "Coefficient", "EffectBonusCoefficient", "EffectMechanic", "Effect", "EffectAura")
    out: dict[int, dict[str, Any]] = {}
    for row in source.iter_dicts("SpellEffect", cols):
        out[int(row["ID"])] = {"BonusCoefficientFromAP": float(row["BonusCoefficientFromAP"]),
                               "EffectBasePointsF": float(row["EffectBasePointsF"]),
                               "Coefficient": float(row["Coefficient"]),
                               "EffectBonusCoefficient": float(row["EffectBonusCoefficient"])}
    return out


def classify(bundle: Any, scope: Any) -> dict[str, Any]:
    catalog = bundle.catalog
    cols = load_effect_columns(bundle.source)
    spec_of: dict[int, set[int]] = defaultdict(set)
    for spec, spells in scope.specs_reach.items():
        for s in spells:
            spec_of[s].add(spec)
    spec_names = scope.roots.spec_names
    weapon_spells: list[dict[str, Any]] = []
    ap_only: list[dict[str, Any]] = []
    next_swing: list[dict[str, Any]] = []
    for spell in sorted(scope.reach):
        info = catalog.get(spell)
        if info is None:
            continue
        w_effects = [e for e in info.effects if e.effect in WEAPON_EFFECTS]
        ns = info.has_attr(ATTR0_ON_NEXT_SWING) or info.has_attr(ATTR0_ON_NEXT_SWING_NO_DAMAGE)
        if ns:
            next_swing.append({"spell": spell, "name": info.name, "specs": sorted(spec_of.get(spell, ())),
                               "weapon_effects": [WEAPON_EFFECTS[e.effect] for e in w_effects]})
        ap_effects = []
        for e in info.effects:
            c = cols.get(e.row_id)
            if c and c["BonusCoefficientFromAP"] > 0.0 and e.effect not in WEAPON_EFFECTS:
                ap_effects.append({"index": e.index, "effect": e.effect, "effect_name": effect_name(e.effect), "aura": e.aura,
                                   "bonus_coefficient_from_ap": c["BonusCoefficientFromAP"], "base_points_f": c["EffectBasePointsF"]})
        if ap_effects:
            ap_only.append({"spell": spell, "name": info.name, "dmg_class": SPELL_DAMAGE_CLASS.get(info.dmg_class),
                            "ap_attack_type": ATTACK_NAMES[ap_coefficient_attack_type(info)],
                            "specs": sorted(spec_of.get(spell, ())), "effects": ap_effects,
                            "has_weapon_effect": bool(w_effects)})
        if not w_effects:
            continue
        att, reason = attack_type(info)
        addpct = (not info.has_attr(ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS)) and bool(info.school_mask & 1)
        effects = []
        for e in info.effects:
            c = cols.get(e.row_id, {})
            effects.append({"index": e.index, "effect": e.effect, "effect_name": effect_name(e.effect),
                            "is_weapon_effect": e.effect in WEAPON_EFFECTS,
                            "base_points_f": c.get("EffectBasePointsF"), "scaling_coefficient": c.get("Coefficient"),
                            "bonus_coefficient_from_ap": c.get("BonusCoefficientFromAP"), "mechanic": e.mechanic})
        divergences = []
        if not addpct:
            divergences.append("addPctMods=false: TOTAL_PCT is skipped in CalculateDamage and school ModDamageDonePercent applies in MeleeDamageBonusDone instead (SpellEffects.cpp:2883-2884, Unit.cpp:8062-8079)")
        if att == OFF_ATTACK:
            if addpct:
                divergences.append("OFF_ATTACK: off-hand weapon range; UNIT_MOD_DAMAGE_OFFHAND TOTAL_PCT (incl. the 0.5 factor) via UnitData::MinOffHandDamage (SpellEffects.cpp:2891, Unit.cpp:2535-2537)")
            else:
                divergences.append("OFF_ATTACK with addPctMods=false: CalculateDamage recomputes with totalPct=1, so the 0.5 off-hand factor (it lives in TOTAL_PCT, Unit.cpp:9793) is NOT applied (SpellEffects.cpp:2908, StatSystem.cpp:450)")
        if att == RANGED_ATTACK:
            divergences.append("RANGED_ATTACK: UNIT_MOD_DAMAGE_RANGED and RangedAttackPower fields")
        if any(e.effect == 121 for e in w_effects):
            divergences.append("normalized: GetAPMultiplier(attType, true) subclass table and CalculateMinMaxDamage recomputed (Unit.cpp:2506-2516)")
        order = [e.effect for e in sorted(w_effects, key=lambda e: e.index)]
        n_fixed = sum(1 for x in order if x in (58, 17, 121))
        n_pct = sum(1 for x in order if x == 31)
        if n_fixed > 1 or n_pct > 1:
            divergences.append(f"multiple weapon effects ({n_fixed} fixed, {n_pct} pct): the second loop re-adds the summed fixed_bonus / re-multiplies the product pct once per effect (SpellEffects.cpp:2911-2935)")
        if n_fixed and n_pct and order.index(31) < min(order.index(x) for x in order if x in (58, 17, 121)):
            fixed_bp = [cols.get(e.row_id, {}).get("EffectBasePointsF") for e in w_effects if e.effect in (58, 17, 121)]
            inert = all(v == 0.0 for v in fixed_bp)
            divergences.append("percent effect precedes the fixed effect: (roll * pct) + fixed (SpellEffects.cpp:2911-2935 index order)"
                               + ("; numerically inert while the fixed EffectBasePointsF is 0 (only `normalized` matters)" if inert else ""))
        if info.has_attr(ATTR3_NO_AVOIDANCE):
            divergences.append("SPELL_ATTR3_NO_AVOIDANCE: MeleeSpellHitResult returns SPELL_MISS_NONE (Unit.cpp:2622-2624)")
        weapon_spells.append({
            "spell": spell, "name": info.name, "family": info.family, "dmg_class": SPELL_DAMAGE_CLASS.get(info.dmg_class),
            "attack_type": ATTACK_NAMES[att], "attack_type_reason": reason,
            "school_mask": info.school_mask, "add_pct_mods": addpct,
            "ignore_caster_damage_modifiers": info.has_attr(ATTR6_IGNORE_CASTER_DAMAGE_MODIFIERS),
            "next_swing": ns,
            "equipped_item_class": info.equipped_item_class,
            "equipped_item_subclass_mask": info.equipped_item_subclass_mask,
            "equipped_item_inventory_type_mask": info.equipped_item_inventory_type_mask,
            "weapon_effects": [WEAPON_EFFECTS[e.effect] for e in w_effects],
            "weapon_effect_order": [f"{e.index}:{e.effect}" for e in sorted(w_effects, key=lambda e: e.index)],
            "fixed_effect_count": n_fixed, "pct_effect_count": n_pct,
            "effect_values_for_oracle": [[e.effect, cols.get(e.row_id, {}).get("EffectBasePointsF")] for e in sorted(w_effects, key=lambda e: e.index)],
            "effects": effects,
            "specs": sorted(spec_of.get(spell, ())), "spec_names": [spec_names.get(s, str(s)) for s in sorted(spec_of.get(spell, ()))],
            "depth": scope.depth.get(spell), "root_kinds": sorted(scope.roots.by_spell.get(spell, ())),
            "shares_primitive": "Unit::CalculateDamage + MeleeDamageBonusDone/Taken (SpellEffects.cpp:2908, 2947-2948)",
            "divergences": divergences,
        })
    by_effect: dict[str, int] = defaultdict(int)
    by_spec_effect: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_attack: dict[str, int] = defaultdict(int)
    by_req: dict[str, int] = defaultdict(int)
    for w in weapon_spells:
        for e in set(w["weapon_effects"]):
            by_effect[e] += 1
            for s in w["specs"]:
                by_spec_effect[f"{s} {spec_names.get(s, '')}"][e] += 1
        by_attack[w["attack_type"]] += 1
        by_req[f"class={w['equipped_item_class']} subclass_mask={w['equipped_item_subclass_mask']} invtype_mask={w['equipped_item_inventory_type_mask']}"] += 1
    return {
        "counts": {
            "reachable_spells": len(scope.reach),
            "weapon_effect_spells": len(weapon_spells),
            "by_weapon_effect": dict(sorted(by_effect.items())),
            "by_attack_type": dict(sorted(by_attack.items())),
            "by_spec_and_effect": {k: dict(sorted(v.items())) for k, v in sorted(by_spec_effect.items())},
            "by_equipped_item_requirement": dict(sorted(by_req.items(), key=lambda kv: -kv[1])),
            "add_pct_mods_false": sum(1 for w in weapon_spells if not w["add_pct_mods"]),
            "multi_fixed_or_multi_pct": sum(1 for w in weapon_spells if w["fixed_effect_count"] > 1 or w["pct_effect_count"] > 1),
            "fixed_and_pct": sum(1 for w in weapon_spells if w["fixed_effect_count"] and w["pct_effect_count"]),
            "offhand_without_pct_mods": sum(1 for w in weapon_spells if w["attack_type"] == "OFF_ATTACK" and not w["add_pct_mods"]),
            "next_swing_spells_in_scope": len(next_swing),
            "next_swing_with_weapon_effect": sum(1 for n in next_swing if n["weapon_effects"]),
            "ap_coefficient_spells": len(ap_only),
            "ap_coefficient_without_weapon_effect": sum(1 for a in ap_only if not a["has_weapon_effect"]),
        },
        "weapon_spells": weapon_spells,
        "next_swing": next_swing,
        "ap_coefficient_spells": ap_only,
    }


def cmd_special(args: argparse.Namespace) -> int:
    from dummy_semantics.loaders import Bundle
    from dummy_semantics.scope import Scope
    bundle = Bundle()
    scope = Scope(bundle)
    data = classify(bundle, scope)
    scope_ext = Scope(bundle, include_class_skills=True)
    data_ext = classify(bundle, scope_ext)
    default_ids = {w["spell"] for w in data["weapon_spells"]}
    for w in data_ext["weapon_spells"]:
        w["scope"] = "default" if w["spell"] in default_ids else "class_skill_lines"
    if args.spec is not None or args.spell is not None:
        data = data_ext
    if args.spell is not None:
        hit = [w for w in data["weapon_spells"] if w["spell"] == args.spell]
        print(json.dumps(hit or {"unresolved": f"spell {args.spell} has no reachable weapon effect in scope"}, indent=1))
        return 0 if hit else 2
    if args.spec is not None:
        rows = [w for w in data["weapon_spells"] if args.spec in w["specs"]]
        print(json.dumps({"spec": args.spec, "count": len(rows), "spells": rows}, indent=1))
        return 0
    snapshot_wide: dict[str, int] = defaultdict(int)
    snapshot_spells: set[int] = set()
    per_spell: dict[int, list[int]] = defaultdict(list)
    for row in bundle.source.iter_dicts("SpellEffect", ("SpellID", "Effect", "DifficultyID")):
        eff = int(row["Effect"])
        if eff in WEAPON_EFFECTS:
            snapshot_wide[WEAPON_EFFECTS[eff]] += 1
            snapshot_spells.add(int(row["SpellID"]))
            if int(row["DifficultyID"]) == 0:
                per_spell[int(row["SpellID"])].append(eff)
    snapshot_multi = sorted(s for s, effs in per_spell.items()
                            if sum(1 for e in effs if e != 31) > 1 or effs.count(31) > 1)
    skew_added = {s for s in snapshot_spells if bundle.skew.is_newer_than_trinity(s)}
    out = {"provenance": provenance("python3 weapon_combat.py special"),
           "context": {"snapshot_wide_weapon_effect_rows": dict(sorted(snapshot_wide.items())),
                       "snapshot_wide_weapon_effect_spells": len(snapshot_spells),
                       "weapon_effect_spells_newer_than_trinity_build": len(skew_added),
                       "snapshot_wide_multi_fixed_or_multi_pct_spells": len(snapshot_multi),
                       "snapshot_wide_multi_fixed_or_multi_pct_sample": snapshot_multi[:25],
                       "multi_note": "DifficultyID 0 rows; ignores per-target effect masks (effects with different implicit targets are not in the same EffectMask)",
                       "note": "snapshot-wide rows are dominated by creature/NPC spells; the census below is the current-player scope only"},
           "default_scope": {"counts": data["counts"], "weapon_spells": data["weapon_spells"], "next_swing": data["next_swing"]},
           "class_skill_lines_scope": {"counts": data_ext["counts"], "weapon_spells": data_ext["weapon_spells"], "next_swing": data_ext["next_swing"],
                                       "note": "Scope(include_class_skills=True): adds SkillLineAbility class lines (baseline class abilities such as 1752 Sinister Strike are learned there, not through SpecializationSpells)"},
           "ap_coefficient_spells_default_scope": data["ap_coefficient_spells"],
           "ap_coefficient_count_class_skill_lines_scope": data_ext["counts"]["ap_coefficient_spells"],
           "consumer": {"Spell::EffectWeaponDmg": "SpellEffects.cpp:2812-2949", "SpellInfo::GetAttackType": "SpellInfo.cpp:1931-1955",
                        "SpellInfo::IsRangedWeaponSpell": "SpellInfo.cpp:1904-1909", "SpellInfo::IsNextMeleeSwingSpell": "SpellInfo.cpp:1899-1902",
                        "Spell::GetCurrentContainer -> CURRENT_MELEE_SPELL": "Spell.cpp:8157-8160", "Unit::AttackerStateUpdate casts CURRENT_MELEE_SPELL": "Unit.cpp:2292-2293",
                        "Item::IsFitToSpellRequirements": "Item.cpp (EquippedItemClass/SubClassMask; InventoryTypeMask only for enchant spells)",
                        "AP coefficient path": "Unit.cpp:6851-6874 DoneTotal += int32(stack * BonusCoefficientFromAP * (attackerBonus + GetTotalAttackPowerValue(attType)))",
                        "SpellEffectValue": "double (SpellDefines.h:490); Spell::m_damage int32 (Spell.h:818)"},
           "scope": "dummy_semantics.scope.Scope(include_class_skills=False) and Scope(include_class_skills=True)"}
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "special-attacks.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} sha256={sha256_file(path)} default={json.dumps(data['counts'])} class_skill_lines={json.dumps(data_ext['counts'])}")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("special", help="special weapon attack census in current player scope (B5)")
    p.add_argument("--spec", type=int, default=None)
    p.add_argument("--spell", type=int, default=None)
    p.set_defaults(func=cmd_special)
