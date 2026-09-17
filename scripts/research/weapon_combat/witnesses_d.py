"""Witnesses for swing scheduling, the attack table and proc events (part D).

Every witness names the *current* data that makes the case reachable (spell
ids from the current-player scope, item-stat census, ContentTuning rows) and
the Trinity coordinate that consumes it.  Legacy mechanics are judged by
reachability from the 12.1.0.69497 snapshot, never by presence in the code.

Scope facts below were computed with ``dummy_semantics.scope.Scope`` (class
skills excluded, the shared convention) and are embedded as constants so the
corpus regenerates without the one-minute scope build; ``witnesses-d
--rescan`` recomputes and diffs them.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from . import CORPORA, SNAPSHOT_BUILD, TRINITY_COMMIT
from .attack_table import Attacker, SpellFacts, Victim, block_percent, calculate_pct_u32, spell_table, white_table
from .swing import simulate

#: Scope census (Scope(include_class_skills=False): 4,836 reachable spells; with class skills: 8,975).
SCOPE_FACTS: dict[str, Any] = {
    "scope_reachable": 4836, "scope_with_class_skills": 8975,
    "on_next_swing": {"scope": [], "class_skill_only": [], "catalog": 189, "attr": "SPELL_ATTR0_ON_NEXT_SWING"},
    "on_next_swing_no_damage": {"scope": [], "class_skill_only": [], "catalog": 396},
    "add_extra_attacks": {"scope": [], "class_skill_only": [], "catalog": 28,
                          "catalog_sample": [3391, 15601, 15642, 32910, 38229, 65976, 78147, 82882, 129758, 163084, 204496, 314827, 429374, 465062, 465660, 465661, 1270413],
                          "note": "Windfury/Skyfury rows exist in the catalog but none is reachable from class trees, spec spells, current gear/sets/gems/enchants or class skill lines"},
    "override_autoattack_361": {"scope": [404542], "names": {"404542": "Crusading Strikes (Retribution) -> TriggerSpell 408385"}},
    "ignore_dual_wield_penalty_458": {"scope": [184362], "names": {"184362": "Enrage (Fury), reached via 383605 Frenzied Flurry"}},
    "autoattack_crit_334": {"scope": [31884, 389539, 406154, 1272781], "names": {"31884": "Avenging Wrath +20", "389539": "Sentinel +10", "406154": "Heart of the Crusader +10", "1272781": "Tiger Fang +15"}},
    "attacker_melee_crit_187": {"scope": [137008, 137028, 24858, 320380, 454788, 389590, 1258153, 1258163],
                                "names": {"137008": "Blood Death Knight -6", "137028": "Protection Paladin -6", "320380": "Thick Skin (Vengeance) -6", "1258153": "Brewmaster Monk -6", "1258163": "Protection Warrior -6", "454788": "Runic Protection -3", "389590": "Demonic Resilience -2", "24858": "Moonkin Form 0"}},
    "mod_expertise_240": {"scope": [137008, 105805, 320380, 1258153, 1258163], "value": 3, "note": "tank spec passives; UpdateExpertise -> MainhandExpertise = int32(rating bonus 0) + 3"},
    "mod_hit_chance_54": {"scope": [325153], "names": {"325153": "Exploding Keg (Brewmaster) -99 on enemies"}},
    "attacker_melee_hit_184": {"scope": [], "class_skill_only": 1},
    "auto_repeat": {"scope": [467718], "names": {"467718": "Bleak Arrows (Beast Mastery/Marksmanship) via 467749"}, "catalog": 13,
                    "auto_shot_75": {"in_scope": False, "acquisition": "SkillLineAbility 32924: SkillLine 183 GENERIC (DND) category 12, ClassMask 4 (Hunter), AcquireMethod 2 AutomaticCharLevel -> Player::LearnSkillRewardedSpells", "attributes_0": 327698, "attributes_2": 32, "casting_time_index": 1, "spell_cast_times_1": [0, 0], "interrupt_flags": 32}},
    "delay_combat_timer_attr6": {"scope": [1513, 51690, 113656, 123986, 198013, 198898, 436358, 473728, 1261193], "catalog": 45},
    "do_not_reset_combat_timers_attr2": {"scope_count": 29, "catalog": 1318},
    "reset_swing_timer_at_spell_start_attr7": {"scope_count": 0, "catalog": 658},
    "doesnt_reset_if_instant_attr6": {"scope_count": 15, "catalog": 155},
    "interrupt_flags_combat": {"scope_count": 282, "class_skill_only_count": 3604, "catalog": 76332},
    "no_avoidance_attr3": {"scope_count": 0, "catalog": 1042},
    "no_active_defense_attr0": {"scope_count": 46, "catalog": 29249},
    "no_attack_dodge_attr7": {"scope_count": 18}, "no_attack_parry_attr7": {"scope_count": 31}, "no_attack_miss_attr7": {"scope_count": 23}, "no_attack_block_attr8": {"scope_count": 15},
    "haste_auras": {"MOD_MELEE_HASTE_138": 0, "MOD_MELEE_HASTE_2_217": 0, "MOD_MELEE_HASTE_3_319": 9, "MOD_MELEE_RANGED_HASTE_192": 0, "MOD_MELEE_RANGED_HASTE_2_342": 8, "MOD_ATTACKSPEED_9": 0, "MELEE_SLOW_193": 44, "MOD_RANGED_HASTE_140": 0,
                    "examples_319": {"386196": "Berserker Stance", "390354": "Furious Blows", "391688": "Dancing Blades"}, "examples_342": {"13750": "Adrenaline Rush", "194879": "Icy Talons", "382889": "Flurry"}, "examples_193": {"10060": "Power Infusion", "31884": "Avenging Wrath"}},
    "mod_rating_189_bits_in_scope": {"8": "CR_CRIT_MELEE", "9": "CR_CRIT_RANGED", "10": "CR_CRIT_SPELL", "13": "CR_SPEED", "17": "CR_HASTE_MELEE", "18": "CR_HASTE_RANGED", "19": "CR_HASTE_SPELL", "25": "CR_MASTERY", "28": "CR_VERSATILITY_DAMAGE_DONE", "29": "CR_VERSATILITY_HEALING_DONE", "30": "CR_VERSATILITY_DAMAGE_TAKEN"},
    "item_stat_ids_current_expansion": {"expansion_id": 11, "items": 7812, "present": [3, 4, 5, 7, 24, 25, 32, 36, 40, 49, 61, 71, 72, 73, 74, 76, 77, 78, 79, 80, 81, 82],
                                        "absent_relevant": {"31": "ITEM_MOD_HIT_RATING", "37": "ITEM_MOD_EXPERTISE_RATING", "13": "ITEM_MOD_DODGE_RATING", "14": "ITEM_MOD_PARRY_RATING", "15": "ITEM_MOD_BLOCK_RATING"}},
    "block": {"MOD_BLOCK_PERCENT_51": [76671, 76857, 386011, 1258163], "MOD_BLOCK_CRIT_CHANCE_253": [76857], "MOD_CRITICAL_BLOCK_AMOUNT_638": [429638],
              "SPELL_EFFECT_BLOCK_spells": {"107": "Block: SkillLineAbility 2200, SkillLine 95 Defense (category 6), ClassMask 67 (Warrior|Paladin|Death Knight), AcquireMethod 2", "118388": "Belly Block"},
              "SPELL_EFFECT_PARRY_class_skill": [3127, 82242, 82245, 82246, 116812, 203724]},
    "dual_wield_effect": {"scope": {"674": "Frost DK", "231842": "Fury", "86629": "Enhancement", "124146": "Brewmaster/Windwalker", "1277760": "Survival"}},
    "ignore_hit_direction_288": {"scope": [212800], "names": {"212800": "Blur (Havoc)", "186265": "Aspect of the Turtle (class skill only)"}},
    "content_tuning_current_raids": {"maps": {"2987": "The Tidebound Grotto", "3004": "The Venomous Abyss"},
                                     "tuning_ids": [5850, 5856, 5857, 5858, 7359, 7360, 7362, 7363], "MinLevelSquish": 90, "MaxLevelSquish": 90, "ExpansionID": 11},
    "expected_stat_armor_constant": {"lvl_90": 3430.0, "lvl_93": 3430.0, "row_ids": [475, 478], "ExpansionID": -2},
}


def table_witness_inputs() -> list[dict[str, Any]]:
    """The witness configurations the attack-table corpus evaluates."""
    boss = Victim(is_player=False, level_for_target=93, facing_attacker=True)
    boss_behind = Victim(is_player=False, level_for_target=93, facing_attacker=False)
    dw = Attacker(is_player=True, level_for_target=90, have_offhand_weapon=True, crit_done_pct=20.0)
    dw_enrage = Attacker(is_player=True, level_for_target=90, have_offhand_weapon=True, crit_done_pct=20.0, ignore_dual_wield_penalty=True)
    two_hand = Attacker(is_player=True, level_for_target=90, have_offhand_weapon=False, crit_done_pct=20.0)
    tank = Attacker(is_player=True, level_for_target=90, have_offhand_weapon=False, crit_done_pct=20.0, expertise_mainhand=3)
    pvp_victim = Victim(is_player=True, level_for_target=90, facing_attacker=True, dodge_percentage=8.0, parry_percentage=10.0, block_percentage=30.0,
                        can_parry=True, can_block=True, has_useable_shield=True)
    npc_boss = Attacker(is_player=False, is_pet=False, is_controlled_by_player=False, level_for_target=93, have_offhand_weapon=False)
    npc_boss_dw = Attacker(is_player=False, is_pet=False, is_controlled_by_player=False, level_for_target=93, have_offhand_weapon=True)
    prot_warrior = Victim(is_player=True, level_for_target=90, facing_attacker=True, dodge_percentage=8.0, parry_percentage=15.0, block_percentage=30.0,
                          can_parry=True, can_block=True, has_useable_shield=True, attacker_melee_crit_aura=-6.0)
    return [
        {"id": "W1", "label": "dual-wield player L90 vs boss NPC L93 (front)", "attacker": dw, "victim": boss, "hands": ("base", "off")},
        {"id": "W1b", "label": "dual-wield player L90 vs boss NPC L93, behind", "attacker": dw, "victim": boss_behind, "hands": ("base", "off")},
        {"id": "W1c", "label": "Fury with Enrage (458) vs boss NPC L93", "attacker": dw_enrage, "victim": boss, "hands": ("base", "off")},
        {"id": "W2", "label": "two-hand player L90 vs boss NPC L93 (front)", "attacker": two_hand, "victim": boss, "hands": ("base",)},
        {"id": "W2b", "label": "tank spec (MOD_EXPERTISE 3) vs boss NPC L93 (front)", "attacker": tank, "victim": boss, "hands": ("base",)},
        {"id": "W3", "label": "ranged auto-shot (75 / 467718) vs boss NPC L93 (front)", "attacker": two_hand, "victim": boss,
         "spell": SpellFacts(spell_id=75, dmg_class_ranged=True)},
        {"id": "W3b", "label": "ranged auto-shot vs boss NPC L93, behind", "attacker": two_hand, "victim": boss_behind, "spell": SpellFacts(spell_id=75, dmg_class_ranged=True)},
        {"id": "W4", "label": "player L90 vs player L90 (front, shield, CanParry/CanBlock)", "attacker": two_hand, "victim": pvp_victim, "hands": ("base",)},
        {"id": "W4b", "label": "player L90 vs player L90, sitting", "attacker": two_hand, "victim": Victim(**{**asdict(pvp_victim), "stand_state": False}), "hands": ("base",)},
        {"id": "W5", "label": "boss NPC L93 attacking player L90 (front)", "attacker": npc_boss, "victim": pvp_victim, "hands": ("base",)},
        {"id": "W5b", "label": "boss NPC L93 dual-wielding attacking player L90", "attacker": npc_boss_dw, "victim": pvp_victim, "hands": ("base", "off")},
        {"id": "W6", "label": "boss NPC L93 attacking Protection Warrior L90 (187: -6 crit)", "attacker": npc_boss, "victim": prot_warrior, "hands": ("base",)},
        {"id": "W7", "label": "melee-class spell (dmg class MELEE) vs boss NPC L93 (front)", "attacker": two_hand, "victim": boss, "spell": SpellFacts(spell_id=0)},
        {"id": "W7b", "label": "melee-class spell with NO_ATTACK_DODGE|PARRY|BLOCK vs boss", "attacker": two_hand, "victim": boss,
         "spell": SpellFacts(spell_id=0, no_attack_dodge=True, no_attack_parry=True, no_attack_block=True)},
        {"id": "W8", "label": "glancing probe: player L86 vs NPC L93 (unreachable at cap; shows the constant)", "attacker": Attacker(level_for_target=86, crit_done_pct=20.0), "victim": boss, "hands": ("base",)},
        {"id": "W9", "label": "crushing probe: NPC L97 vs player L90 (eligible, arithmetically dead)", "attacker": Attacker(is_player=False, is_controlled_by_player=False, level_for_target=97), "victim": pvp_victim, "hands": ("base",)},
    ]


# ---------------------------------------------------------------------------
# live reachability census (replaces the hand-copied SCOPE_FACTS when --census)
# ---------------------------------------------------------------------------

CENSUS_ATTRS = (
    "SPELL_ATTR0_ON_NEXT_SWING", "SPELL_ATTR0_ON_NEXT_SWING_NO_DAMAGE", "SPELL_ATTR2_AUTO_REPEAT",
    "SPELL_ATTR6_DELAY_COMBAT_TIMER_DURING_CAST", "SPELL_ATTR2_DO_NOT_RESET_COMBAT_TIMERS",
    "SPELL_ATTR7_RESET_SWING_TIMER_AT_SPELL_START", "SPELL_ATTR6_DOESNT_RESET_SWING_TIMER_IF_INSTANT",
    "SPELL_ATTR3_NO_AVOIDANCE", "SPELL_ATTR0_NO_ACTIVE_DEFENSE", "SPELL_ATTR7_NO_ATTACK_DODGE",
    "SPELL_ATTR7_NO_ATTACK_PARRY", "SPELL_ATTR7_NO_ATTACK_MISS", "SPELL_ATTR8_NO_ATTACK_BLOCK",
    "SPELL_ATTR3_ALWAYS_HIT", "SPELL_ATTR3_COMPLETELY_BLOCKED", "SPELL_ATTR5_ALLOW_ACTIONS_DURING_CHANNEL",
    "SPELL_ATTR4_SUPPRESS_WEAPON_PROCS", "SPELL_ATTR0_CANCELS_AUTO_ATTACK_COMBAT",
    "SPELL_ATTR3_SUPPRESS_CASTER_PROCS", "SPELL_ATTR3_SUPPRESS_TARGET_PROCS",
)
CENSUS_AURAS = (
    "OVERRIDE_AUTOATTACK_WITH_MELEE_SPELL", "IGNORE_DUAL_WIELD_HIT_PENALTY", "MOD_AUTOATTACK_CRIT_CHANCE",
    "MOD_ATTACKER_MELEE_CRIT_CHANCE", "MOD_CRIT_CHANCE_FOR_CASTER", "MOD_CRIT_CHANCE_FOR_CASTER_PET",
    "MOD_ATTACKER_SPELL_AND_WEAPON_CRIT_CHANCE", "MOD_CRIT_CHANCE_VERSUS_TARGET_HEALTH",
    "MOD_COMBAT_RESULT_CHANCE", "MOD_ENEMY_DODGE", "IGNORE_COMBAT_RESULT", "IGNORE_HIT_DIRECTION",
    "DEFLECT_SPELLS", "MOD_BLOCK_CRIT_CHANCE", "MOD_CRITICAL_BLOCK_AMOUNT", "MOD_BLOCK_PERCENT",
    "MOD_PARRY_PERCENT", "MOD_DODGE_PERCENT", "MOD_WEAPON_CRIT_PERCENT", "MOD_CRIT_PCT", "MOD_EXPERTISE",
    "MOD_HIT_CHANCE", "MOD_ATTACKER_MELEE_HIT_CHANCE", "MOD_ATTACKER_RANGED_HIT_CHANCE",
    "MOD_MELEE_HASTE", "MOD_MELEE_HASTE_2", "MOD_MELEE_HASTE_3", "MOD_MELEE_RANGED_HASTE",
    "MOD_MELEE_RANGED_HASTE_2", "MOD_ATTACKSPEED", "MELEE_SLOW", "MOD_SPEED_SLOW_ALL", "MOD_RANGED_HASTE",
    "DISABLE_AUTOATTACK", "DISABLE_ATTACKING_EXCEPT_ABILITIES", "MOD_CRIT_DAMAGE_BONUS",
)
CENSUS_EFFECTS = ("ADD_EXTRA_ATTACKS", "PARRY", "BLOCK", "DUAL_WIELD")
NAMED_LIMIT = 40   # lists longer than this carry ids only


def census() -> dict[str, Any]:
    """Reachability of every swing/table/proc input in the current-player scope (~1 min)."""
    import csv
    from collections import Counter
    from dummy_semantics.loaders import Bundle
    from dummy_semantics.scope import Scope
    from gearing.tables import DEFAULT_TABLES
    from procs.enums import attr, aura, effect

    b = Bundle()
    scope = Scope(b, include_class_skills=False)
    scope_cs = Scope(b, include_class_skills=True)
    cat = b.catalog
    all_ids = sorted(cat.names)
    spec_names = scope.roots.spec_names
    class_names = scope.roots.class_names

    def specs_of(sid: int) -> list[str]:
        out = []
        for k in sorted(k for k, v in scope.specs_reach.items() if sid in v):
            cls = class_names.get(scope.roots.class_of_spec.get(k), "?")
            out.append(f"{spec_names.get(k, k)} {cls}")
        return out

    def listing(ids: list[int]) -> list[Any]:
        if len(ids) > NAMED_LIMIT:
            return ids
        return [{"id": s, "name": b.name(s), "specs": specs_of(s), "build_skew": bool(b.skew.is_newer_than_trinity(s))} for s in ids]

    def entry(pred) -> dict[str, Any]:
        hits = sorted(s for s in scope_cs.reach if (i := cat.get(s)) and pred(i))
        in_scope = [s for s in hits if s in scope.reach]
        return {"scope_count": len(in_scope), "scope": listing(in_scope),
                "class_skill_only": [s for s in hits if s not in scope.reach],
                "catalog_count": sum(1 for s in all_ids if (i := cat.get(s)) and pred(i))}

    out: dict[str, Any] = {"scope_reachable": scope.summary()["reachable"],
                           "scope_with_class_skills": scope_cs.summary()["reachable"],
                           "convention": "dummy_semantics.scope.Scope(include_class_skills=False); class skill lines reported separately",
                           "attrs": {}, "auras": {}, "effects": {}}
    for name in CENSUS_ATTRS:
        key = attr(name)
        out["attrs"][name] = entry(lambda i, key=key: i.has_attr(key))
    for name in CENSUS_AURAS:
        a = aura(name)
        rec = entry(lambda i, a=a: i.has_aura(a))
        rec["aura_id"] = a
        if isinstance(rec["scope"], list) and rec["scope"] and isinstance(rec["scope"][0], dict):
            for r in rec["scope"]:
                r["amounts"] = [e.base_points for e in cat.get(r["id"]).effects if e.is_aura and e.aura == a]
        out["auras"][name] = rec
    for name in CENSUS_EFFECTS:
        e_id = effect(name)
        rec = entry(lambda i, e_id=e_id: i.has_effect(e_id))
        rec["effect_id"] = e_id
        if name == "ADD_EXTRA_ATTACKS":
            rec["catalog"] = [{"id": s, "name": b.name(s), "build_skew": bool(b.skew.is_newer_than_trinity(s))}
                              for s in all_ids if (i := cat.get(s)) and i.has_effect(e_id)]
        out["effects"][name] = rec
    # MOD_RATING combat-rating bits reachable
    bits: dict[int, set[int]] = {}
    mod_rating = aura("MOD_RATING")
    for s in scope_cs.reach:
        i = cat.get(s)
        if not i:
            continue
        for e in i.effects:
            if e.is_aura and e.aura == mod_rating:
                for bit in range(32):
                    if e.misc0 & (1 << bit):
                        bits.setdefault(bit, set()).add(s)
    out["mod_rating_bits"] = {str(k): {"scope": sorted(x for x in v if x in scope.reach),
                                       "class_skill_only": sorted(x for x in v if x not in scope.reach)}
                              for k, v in sorted(bits.items())}
    out["dmg_class_in_scope"] = {str(k): v for k, v in sorted(Counter(cat.get(s).dmg_class for s in scope.reach if cat.get(s)).items())}

    def rows(table: str):
        with (DEFAULT_TABLES / f"{table}.csv").open(newline="", encoding="utf-8") as fh:
            yield from csv.DictReader(fh)

    combat = sorted({int(r["SpellID"]) for r in rows("SpellInterrupts") if int(r["InterruptFlags"]) & 0x8 and int(r["DifficultyID"]) == 0})
    out["interrupt_flags_combat"] = {"catalog_count": len(combat), "scope_count": sum(1 for s in combat if s in scope.reach),
                                     "class_skill_only_count": sum(1 for s in combat if s in scope_cs.reach and s not in scope.reach)}
    i75 = cat.get(75)
    sla = [r for r in rows("SkillLineAbility") if r["Spell"] == "75"]
    lines = {r["ID"]: r for r in rows("SkillLine") if r["ID"] in {x["SkillLine"] for x in sla}}
    out["auto_shot_75"] = {
        "name": b.name(75), "in_scope": 75 in scope.reach, "in_class_skill_scope": 75 in scope_cs.reach,
        "attributes": list(i75.attributes), "dmg_class": i75.dmg_class,
        "interrupt_rows": [{k: r[k] for k in ("SpellID", "DifficultyID", "InterruptFlags")} for r in rows("SpellInterrupts") if r["SpellID"] == "75"],
        "casting_time_index": [r["CastingTimeIndex"] for r in rows("SpellMisc") if r["SpellID"] == "75" and r["DifficultyID"] == "0"],
        "skill_line_ability": [{k: r[k] for k in ("ID", "SkillLine", "ClassMask", "AcquireMethod")} for r in sla],
        "skill_lines": [{k: lines[x["SkillLine"]][k] for k in ("ID", "DisplayName_lang", "CategoryID")} for x in sla],
        "note": "AcquireMethod 2 = AutomaticCharLevel (DBCEnums.h SkillLineAbilityAcquireMethod) -> Player::LearnSkillRewardedSpells; SkillLine category 12 is not a class skill line (Scope roots use category 7)",
    }
    isp = list(rows("ItemSparse"))
    cols = [c for c in isp[0] if c.startswith("StatModifier_bonusStat")]
    newest = max(int(r["ExpansionID"]) for r in isp)
    stat_counts: Counter[int] = Counter()
    n = 0
    for r in isp:
        if int(r["ExpansionID"]) != newest:
            continue
        n += 1
        for c in cols:
            if int(r[c]) > 0:
                stat_counts[int(r[c])] += 1
    out["item_stat_census"] = {"expansion_id": newest, "items": n, "stat_id_counts": {str(k): v for k, v in sorted(stat_counts.items())},
                               "legacy_ids_absent": {k: v for k, v in {"13": "DODGE_RATING", "14": "PARRY_RATING", "15": "BLOCK_RATING",
                                                                        "31": "HIT_RATING", "37": "EXPERTISE_RATING"}.items() if int(k) not in stat_counts}}
    return out


# ---------------------------------------------------------------------------
# glancing / crushing level census from creature_template_difficulty
# ---------------------------------------------------------------------------

def _content_tuning() -> dict[int, dict[str, int]]:
    import csv
    from gearing.tables import DEFAULT_TABLES
    out = {}
    with (DEFAULT_TABLES / "ContentTuning.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            # positional mapping to Trinity's ContentTuningEntry (DB2Structure.h:984-1004):
            # MinLevelSquish->MinLevel, MaxLevelSquish->MaxLevel, MinLevelScalingOffset->MinLevelType,
            # MaxLevelScalingOffset->MaxLevelType (structural-inference: 12.1 column names differ)
            out[int(r["ID"])] = {"min": int(r["MinLevelSquish"]), "max": int(r["MaxLevelSquish"]),
                                 "min_type": int(r["MinLevelScalingOffset"]), "max_type": int(r["MaxLevelScalingOffset"]),
                                 "expansion": int(r["ExpansionID"])}
    return out


MAX_LEVEL = 123                  # DBCEnums.h:45
EXPANSION_MAX = {0: 0, 1: 1, 2: 90, 3: 80}   # ContentTuningCalcType adjustments at CURRENT_EXPANSION Midnight (DB2Stores.cpp:2201-2216, SharedDefines.h:109-141)


def creature_level_vs_player(ct: dict[str, int] | None, delta: int, player_level: int = 90) -> int:
    """Creature::GetLevelForTarget for a player target without ScalingPlayerLevelDelta (Creature.cpp:3130-3160)."""
    if ct is None:
        # no ContentTuning row: not scalable; level from SelectLevel = RoundToInterval(0 + delta, 1, 255)
        return max(1, min(255, delta))
    lo = max(1, min(MAX_LEVEL, ct["min"] + EXPANSION_MAX.get(ct["min_type"], 0)))
    hi = max(1, min(MAX_LEVEL, ct["max"] + EXPANSION_MAX.get(ct["max_type"], 0)))
    level = max(lo, min(hi, player_level)) + delta
    return max(1, min(MAX_LEVEL + 3, level))


def level_census(ctd_path: str, player_level: int = 90) -> dict[str, Any]:
    from collections import Counter
    data = json.loads(open(ctd_path, encoding="utf-8").read())
    table = data["tables"]["creature_template_difficulty"]
    cols = table["columns"]
    ix = {c: cols.index(c) for c in ("Entry", "DifficultyID", "LevelScalingDeltaMin", "LevelScalingDeltaMax", "ContentTuningID")}
    cts = _content_tuning()
    reach_glancing: list[list[int]] = []
    missing_ct = 0
    by_level: Counter[int] = Counter()
    for row in table["rows"]:
        dmin, dmax = sorted((int(row[ix["LevelScalingDeltaMin"]]), int(row[ix["LevelScalingDeltaMax"]])))
        ctid = int(row[ix["ContentTuningID"]])
        ct = cts.get(ctid) if ctid else None
        if ctid and ct is None:
            missing_ct += 1
        top = creature_level_vs_player(ct, dmax, player_level)
        by_level[top - player_level] += 1
        if player_level + 3 < top:          # Unit.cpp:2458 (glancing) == Unit.cpp:2483 crushing gap (>= +4)
            reach_glancing.append([int(row[ix["Entry"]]), int(row[ix["DifficultyID"]]), dmin, dmax, ctid,
                                   ct["expansion"] if ct else None])
    reach_glancing.sort()
    current = [r for r in reach_glancing if r[5] == 11]
    return {
        "source": {"corpus": ctd_path.split("/")[-1], "provenance": data["provenance"],
                   "row_count": len(table["rows"])},
        "player_level": player_level,
        "model": "level = clamp(player_level, CT.MinLevel(+type adj), CT.MaxLevel(+type adj)) + LevelScalingDeltaMax (upper end of the irand range); "
                 "glancing (player attacker) and crushing eligibility (creature attacker) both need level >= player_level + 4; "
                 "ConditionalContentTuning redirects and MaxCreatureScalingLevel / ScalingPlayerLevelDelta are ignored (assumption)",
        "rows_with_content_tuning_missing_from_snapshot": missing_ct,
        "level_minus_player_histogram": {str(k): v for k, v in sorted(by_level.items())},
        "rows_reaching_plus4": len(reach_glancing),
        "entries_reaching_plus4": len({r[0] for r in reach_glancing}),
        "rows_reaching_plus4_on_expansion_11_tuning": len(current),
        "entries_reaching_plus4_on_expansion_11_tuning": sorted({r[0] for r in current}),
        "sample_rows": reach_glancing[:60],
        "columns_of_sample": ["Entry", "DifficultyID", "DeltaMin", "DeltaMax", "ContentTuningID", "CT.ExpansionID"],
    }


def build_corpus(generator: str, live_census: dict[str, Any] | None = None, levels: dict[str, Any] | None = None) -> dict[str, Any]:
    from .attack_table import _git_head, crit_damage, creature_block_percent, glancing_damage, spell_block_value, white_blocked_amount
    tables = []
    for w in table_witness_inputs():
        rec: dict[str, Any] = {"id": w["id"], "label": w["label"]}
        if "spell" in w:
            t = spell_table(w["attacker"], w["victim"], w["spell"])
            rec["probabilities"] = t.probabilities()
            rec["crit"] = t.crit
            rec["bands"] = [[b.result, b.eligible, b.chance_pct, b.chance_bp, b.lo, b.hi] for b in t.bands]
        else:
            rec["tables"] = {}
            for att in w["hands"]:
                t = white_table(w["attacker"], w["victim"], att)
                rec["tables"][att] = {"probabilities": t.probabilities(), "sitting_auto_crit": t.sitting_auto_crit, "notes": t.notes,
                                      "bands": [[b.outcome, b.eligible, b.chance_pct, b.chance_bp, b.lo, b.hi] for b in t.bands]}
        tables.append(rec)
    frac = block_percent(2000, 3430.0)
    block = {"shield_block_example": 2000, "armor_constant_lvl90": 3430.0,
             "player_GetBlockPercent": frac,
             "player_white_blocked_of_1000": white_blocked_amount(1000, frac),
             "player_spell_block_value": spell_block_value(frac),
             "creature_GetBlockPercent": creature_block_percent(),
             "creature_white_blocked_of_1000": white_blocked_amount(1000, creature_block_percent()),
             "creature_critical_block_x1_5_of_1000": white_blocked_amount(1000, creature_block_percent(), True, 1.5),
             "crit_damage_1000_multiplier_1_3": crit_damage(1000, 1.3),
             "glancing_damage_1000_86_vs_93": list(glancing_damage(1000, 86, 93)),
             "note": "Player::GetBlockPercent returns a fraction (Player.cpp:26822-26831) that CalculatePct treats as a percent "
                     "(Unit.cpp:1459) and that the spell path truncates to uint32 0 (Unit.cpp:1239); the virtual base returns 30.0f "
                     "for every non-player (Unit.h:987). Consumer fact, trinity-probe verified; not a Retail claim."}
    swings = {
        "dw_player_2600_2600_tick100": simulate(2600, 2600, 0.0, 6000, 100)["timeline"],
        "hunter_ranged_3000_tick100": [x for x in simulate(2000, None, 0.0, 7000, 100, ranged_speed=3000, melee=False)["timeline"] if x.get("swing") == "ranged" or x.get("event") == "auto_shot_prepared"],
    }
    glancing_reopen = "a current (ExpansionID 11) creature_template_difficulty row whose level vs a level-90 player is >= 94; current raid bosses are absent from the pinned TDB (build-skew)"
    legacy = {
        "glancing": {"class": "legacy-only (for current raid content: unresolved/build-skew)",
                     "condition": "attacker player or pet, victim creature non-pet, attackerLevel + 3 < victimLevel (Unit.cpp:2455-2464); no cap on (10 + 10 * diff) * 100 despite the 40% comment",
                     "current_input": "needs a creature level >= 94 against a level-90 player: ContentTuning with MaxLevel 90 plus LevelScalingDeltaMax >= 4 (Creature.cpp:3060-3075, 3130-3160). "
                                      "The nine encounters of JournalInstance 1317/1320 (maps 2987/3004) are absent from TDB 1200.26021, so their delta is unknown to Trinity.",
                     "level_census": levels if levels is not None else "not computed (run witnesses-d --levels <creature_template_difficulty corpus>)",
                     "reopen": glancing_reopen},
        "crushing": {"class": "legacy-only (dead code)", "condition": "attackerLevel >= victimLevel + 4, attacker not player-controlled, no CREATURE_FLAG_EXTRA_NO_CRUSHING_BLOWS (Unit.cpp:2481-2494)",
                     "current_input": "eligibility needs the same +4 creature as glancing; even then tmp = attackerLevel - victimLevel * 1000 - 1500 (Unit.cpp:2489) is negative, and because every earlier band already failed `roll < sum`, `roll < sum + tmp` is false for every roll: crushing is unselectable for any attackerLevel < victimLevel * 1000 + 1500",
                     "reopen": "a TrinityCore change to Unit.cpp:2489 (precedence) together with a +4 creature"},
        "hit_rating_CR_HIT_MELEE": {"class": "legacy-only", "current_input": "no ITEM_MOD_HIT_RATING (31) on any ExpansionID-11 item and no MOD_RATING aura with CR_HIT bits 5-7 in scope; m_modMeleeHitChance stays 7.5 (StatSystem.cpp:753-761, Player.cpp:171-172)",
                                    "reopen": "an item stat 31/HIT_*_RATING or a MOD_RATING(189) aura with bits 5-7 reachable in scope"},
        "expertise_rating_CR_EXPERTISE": {"class": "legacy-only (rating); trinity-consumer (flat aura 240 reachable)", "current_input": "no ITEM_MOD_EXPERTISE_RATING (37) items and no MOD_RATING bit 23; SPELL_AURA_MOD_EXPERTISE (240) amount 3 on five tank passives -> MainhandExpertise 3 -> 8.25% dodge/parry reduction (StatSystem.cpp:769-796, Player.cpp:5216-5229)",
                                          "reopen": "item stat 37 or MOD_RATING bit 23 reachable"},
        "base_expertise_7_5": {"class": "trinity-consumer (reachable, every player)", "current_input": "GetExpertiseDodgeOrParryReduction adds a flat 7.5 for BASE/OFF (Player.cpp:5218): a +3 creature's 7.5% dodge is exactly cancelled"},
        "dual_wield_miss_penalty": {"class": "trinity-consumer (reachable)", "current_input": "any unit with haveOffhandWeapon (players: a useable off-hand weapon; creatures: CanDualWield): +19% miss on both hands; net 16.5% for players after the 7.5% base hit; removed by aura 458 (184362 Enrage, Fury) while active"},
        "sitting_auto_crit": {"class": "trinity-consumer", "current_input": "player victim not in stand state (Unit.cpp:2433-2435)"},
        "on_next_swing": {"class": "legacy-only", "current_input": "0 spells with SPELL_ATTR0_ON_NEXT_SWING[_NO_DAMAGE] in scope or class skills (189 / 396 in the catalog)", "reopen": "a reachable spell with either attribute"},
        "extra_attacks": {"class": "legacy-only", "current_input": "0 spells with SPELL_EFFECT_ADD_EXTRA_ATTACKS in scope or class skills (28 in the catalog)", "reopen": "a reachable ADD_EXTRA_ATTACKS spell (e.g. a Windfury-like item effect in current gear)"},
        "weapon_skill_defense_skill": {"class": "absent from the table", "current_input": "no skill terms remain in RollMeleeOutcomeAgainst / MeleeSpellMissChance; GetMaxSkillValueForLevel (level * 5) is used only by the creature daze roll (Unit.cpp:1585-1586)"},
        "resilience_crit_taken": {"class": "legacy-only", "current_input": "no crit-taken resilience term in GetUnitCriticalChanceTaken (Unit.cpp:2909-2938); CR_RESILIENCE_PLAYER_DAMAGE only scales damage (Unit.cpp:1499-1505)"},
        "dodge_parry_block_ratings": {"class": "legacy-only (item stats)", "current_input": "no item stat 13/14/15 on ExpansionID-11 items; avoidance percentages come from the ActivePlayerData fields (charstats/Track C)"},
        "deflect": {"class": "not reachable in scope", "current_input": "0 SPELL_AURA_DEFLECT_SPELLS (287) spells in scope (24 in the catalog)"},
    }
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "generator": generator, "wowlab_data_commit": _git_head(),
                       "world_database": (levels or {}).get("source", {}).get("provenance", {}).get("base_world_database") if levels else None},
        "scope_facts_embedded": SCOPE_FACTS if live_census is None else "superseded by the live census below",
        "census": live_census if live_census is not None else "not computed (run witnesses-d --census)",
        "witnesses": tables,
        "block_percent_witness": block,
        "swing_witnesses": swings,
        "legacy_verdicts": legacy,
    }


def _cmd(args: argparse.Namespace) -> int:
    live = census() if args.census else None
    levels = level_census(args.levels) if args.levels else None
    gen = "python3 weapon_combat.py witnesses-d" + (" --census" if args.census else "") + (f" --levels {args.levels}" if args.levels else "")
    corpus = build_corpus(gen, live, levels)
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "witnesses-d.json"
    path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(corpus['witnesses'])} witnesses)")
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    from . import attack_table, proc_events, swing
    rc = 0
    for mod in (swing, attack_table, proc_events):
        rc |= int(mod._cmd_corpus(args) or 0)
    return rc | int(_cmd(argparse.Namespace(census=args.census, levels=args.levels)) or 0)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("witnesses-d", help="write docs/research/weapon-combat-corpora/witnesses-d.json")
    p.add_argument("--census", action="store_true", help="recompute the scope census live (~1 min)")
    p.add_argument("--levels", default=None, help="creature_template_difficulty corpus from tools/tdb_world_extract.py (glancing/crushing level census)")
    p.set_defaults(func=_cmd)
    a = subparsers.add_parser("all-d", help="regenerate swing.json, attack-table.json, proc-events.json, witnesses-d.json")
    a.add_argument("--census", action="store_true")
    a.add_argument("--levels", default=None)
    a.set_defaults(func=_cmd_all)
