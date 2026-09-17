"""Witnesses (weapon/damage view): prepared weapon parameters for real current items.

One witness per requested spec shape (dual-wield 1H, Titan's Grip 2H+2H, 2H,
fist, form, ranged bow/gun, caster staff, shield).  Items come from
``docs/research/gearing-corpora/current-gear-corpus.json`` (raid section,
``Raid_Normal`` context 3) and are resolved through the gearing pipeline.

Attack power at level 90 is **unresolved** here: ``player_classlevelstats``
stops at level 80 (world-db-corpora/player-base-stats.json) and prepared AP is
Track C/F scope, so every witness reports the AP=0 min/max plus the exact
per-AP-point coefficient of the prepared formula.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from . import CORPORA, ROOT
from .damage import (ARMOR_CONSTANT_LEVEL_90, ArmorInputs, DoneMods, OutcomeInputs, TakenMods,
                     player_block_percent, white_swing)
from .weapon import (ATTACK_NAMES, BASE_ATTACK, FERAL_FORMS, OFF_ATTACK, RANGED_ATTACK, WeaponItem,
                     check_equip, hand_to_dict, load_weapon_item, prepare_hands, provenance, sha256_file)

GEAR_CORPUS = ROOT / "docs" / "research" / "gearing-corpora" / "current-gear-corpus.json"
RAID_NORMAL = 3
LEVEL = 90
CREATURE_ARMOR_LEVEL_90 = 1470.0     # ExpectedStat ID 475 (Lvl 90, ExpansionID -2).CreatureArmor -- db2-fact, pre creature_template multipliers (Track A/B)

#: (spec, class, label, main hand, off hand, form)
WITNESSES: list[tuple[int, int, str, int, int | None, int | None]] = [
    (72, 1, "Fury Warrior (Titan's Grip 2H + 2H)", 268214, 268213, None),
    (71, 1, "Arms Warrior (2H sword)", 268214, None, None),
    (260, 4, "Outlaw Rogue (1H sword + 1H axe)", 268202, 268208, None),
    (259, 4, "Assassination Rogue (dagger + dagger)", 268204, 268264, None),
    (263, 7, "Enhancement Shaman (1H axe + 1H mace)", 268208, 268206, None),
    (251, 6, "Frost Death Knight (2H axe)", 268213, None, None),
    (251, 6, "Frost Death Knight (1H axe + 1H mace)", 268208, 268206, None),
    (269, 10, "Windwalker Monk (fist + fist)", 270930, 270930, None),
    (103, 11, "Feral Druid (staff, Cat Form)", 268199, None, 1),
    (104, 11, "Guardian Druid (staff, Bear Form)", 268199, None, 5),
    (253, 3, "Beast Mastery Hunter (bow)", 268207, None, None),
    (254, 3, "Marksmanship Hunter (gun)", 268200, None, None),
    (70, 2, "Retribution Paladin (2H mace)", 268198, None, None),
    (64, 8, "Frost Mage (caster staff, prepared but unused)", 268205, None, None),
    (73, 1, "Protection Warrior (MH axe + shield)", 268209, 268262, None),
]


def corpus_contexts() -> dict[int, list[str]]:
    data = json.loads(GEAR_CORPUS.read_text(encoding="utf-8"))
    out: dict[int, list[str]] = {}
    for section in ("raid", "mythic_plus"):
        for item in data[section]["items"]:
            out[item["item_id"]] = [f"{section}: " + p for p in item["provenance"]]
    return out


def build(tables: Any, resolver: Any, capability: dict[str, Any] | None) -> list[dict[str, Any]]:
    contexts = corpus_contexts()
    shape_rows = tables("SpellShapeshiftForm").by("ID")
    out: list[dict[str, Any]] = []
    for spec, class_id, label, mh_id, oh_id, form in WITNESSES:
        mh = load_weapon_item(resolver, mh_id, player_level=LEVEL, context=RAID_NORMAL)
        oh = load_weapon_item(resolver, oh_id, player_level=LEVEL, context=RAID_NORMAL) if oh_id else None
        cap_dw = cap_tg = None
        tg_mask = 0
        if capability is not None:
            cap_dw = str(spec) in capability["dual_wield"]
            cap_tg = str(spec) in capability["titan_grip"]
            if cap_tg:
                tg_mask = capability["titan_grip"][str(spec)][0]["equipped_item_subclass_mask"]
        state = check_equip(mh, oh, dual_wield=bool(cap_dw), titan_grip=bool(cap_tg), titan_grip_subclass_mask=tg_mask)
        crt = int(shape_rows[form]["CombatRoundTime"]) if form else 0
        hands = prepare_hands(state, level=LEVEL, form_id=form, form_combat_round_time=crt, ap=0, ap_mod_pos=0,
                              ap_mod_neg=0, ap_multiplier_value=0.0, versatility_pct=0.0, mods={})
        witness: dict[str, Any] = {
            "spec": spec, "class": class_id, "label": label, "level": LEVEL, "form": form,
            "form_name": FERAL_FORMS.get(form) if form else None, "form_combat_round_time": crt,
            "items": [{"slot": "main_hand", **_item_view(mh, contexts)}] + ([{"slot": "off_hand", **_item_view(oh, contexts)}] if oh else []),
            "capability": {"dual_wield": cap_dw, "titan_grip": cap_tg, "titan_grip_subclass_mask": tg_mask,
                           "learned_only_dual_wield": [r["spell"] for r in ((capability or {}).get("learned_only") or {}).get("dual_wield", {}).get(str(spec), [])],
                           "source": "weapon-sources.json capability_by_spec" if capability is not None else "unresolved (weapon-sources.json absent)"},
            "equip_state": {"two_hand_used": state.two_hand_used, "dual_wielding": state.dual_wielding,
                            "ranged_main_hand": state.ranged_main_hand, "equip_errors": state.equip_errors},
            "attack_power": {"value": None, "status": "unresolved", "reason": "player_classlevelstats covers levels <= 80 only (world-db-corpora/player-base-stats.json); prepared AP at 90 is Track C/F scope",
                             "reopen": "a level-90 base-stat source or a supplied UnitData::AttackPower"},
            "hands": [hand_to_dict(h) for h in hands],
        }
        # damage view: main hand, roll = lower urand bound, normal hit vs a level-90 creature
        main = hands[BASE_ATTACK] if not state.ranged_main_hand else hands[RANGED_ATTACK]
        roll = main.urand_bounds[0]
        dmg = white_swing(roll=roll, att_type=main.attack_type, ap_multiplier_value=main.ap_multiplier, school_mask=main.school_mask,
                          done=DoneMods(), taken=TakenMods(), armor=ArmorInputs(victim_armor=int(CREATURE_ARMOR_LEVEL_90)),
                          outcome=OutcomeInputs(outcome="normal"))
        dmg_assumed = white_swing(roll=roll, att_type=main.attack_type, ap_multiplier_value=main.ap_multiplier, school_mask=main.school_mask,
                                  done=DoneMods(), taken=TakenMods(),
                                  armor=ArmorInputs(victim_armor=int(CREATURE_ARMOR_LEVEL_90), attacker_avg_item_level=0.0,
                                                    item_level_by_level=0.0, diminishing_curve_value=1.0),
                                  outcome=OutcomeInputs(outcome="normal"))
        witness["damage_view"] = {
            "hand": ATTACK_NAMES[main.attack_type], "roll": roll, "outcome": "normal", "victim_armor": CREATURE_ARMOR_LEVEL_90,
            "victim_armor_source": "ExpectedStat.CreatureArmor Lvl 90 (db2-fact; creature_template multipliers are Track A/B scope)",
            "armor_constant": ARMOR_CONSTANT_LEVEL_90,
            "result": dmg.get("damage"), "status": "unresolved" if "unresolved" in dmg else "ok",
            "unresolved": dmg.get("unresolved"),
            "conditional_result_if_curve_factor_is_1": dmg_assumed.get("damage"),
            "conditional_note": "Curve 27400 answers 1.0 for itemLevelDelta <= 17 (CurvePoint OrderIndex 0-2); the delta needs the absent ItemLevelByLevel GameTable",
        }
        if oh is not None and oh.class_id == 4 and oh.subclass == 6:
            shield_block = int(_trunc(_f32mul(oh_armor(resolver, oh_id), 2.5)))
            witness["shield"] = {"item": oh_id, "armor": oh_armor(resolver, oh_id), "ShieldBlock": shield_block,
                                 "ShieldBlock_source": "Player.cpp:8127 int32(armor * 2.5f)",
                                 "block_fraction_as_victim": player_block_percent(shield_block, ARMOR_CONSTANT_LEVEL_90),
                                 "note": "Player::GetBlockPercent returns this FRACTION and CalculateMeleeDamage feeds it to CalculatePct as if percent (Unit.cpp:1459, Player.cpp:26822-26831)"}
        out.append(witness)
    return out


def oh_armor(resolver: Any, item_id: int) -> int:
    from gearing.resolver import Variant
    return int(resolver.resolve(item_id, Variant(label="ctx", context=RAID_NORMAL), player_level=LEVEL).armor)


def _f32mul(a: float, b: float) -> float:
    from procs.chance import f32, lit, mul
    return mul(f32(a), lit(str(b)))


def _trunc(x: float) -> int:
    import math
    return int(math.trunc(x))


def _item_view(item: WeaponItem, contexts: dict[int, list[str]]) -> dict[str, Any]:
    d = {k: v for k, v in item.__dict__.items() if k != "provenance"}
    d["provenance"] = item.provenance + contexts.get(item.item_id, ["not in current-gear-corpus.json"])
    return d


def cmd_witnesses(args: argparse.Namespace) -> int:
    from gearing.resolver import GearResolver
    from gearing.tables import DEFAULT_TABLES, Tables
    tables = Tables(DEFAULT_TABLES)
    resolver = GearResolver(tables)
    cap_path = CORPORA / "weapon-sources.json"
    from .weapon import _sources_capability
    capability = _sources_capability() if cap_path.exists() else None
    witnesses = build(tables, resolver, capability)
    out = {"provenance": provenance("python3 weapon_combat.py witnesses", gear_corpus=str(GEAR_CORPUS.relative_to(ROOT)),
                                    gear_corpus_sha256=sha256_file(GEAR_CORPUS)),
           "level": LEVEL, "context": RAID_NORMAL, "witness_count": len(witnesses), "witnesses": witnesses}
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "witnesses-c.json"
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} sha256={sha256_file(path)} witnesses={len(witnesses)}")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    from .damage import cmd_arithmetic
    from .special import cmd_special
    from .weapon import cmd_sources
    rc = 0
    for fn in (cmd_sources, cmd_arithmetic, cmd_special, cmd_witnesses):
        rc |= int(fn(args) or 0)
    return rc


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("witnesses", help="write witnesses-c.json (prepared weapon parameters for real current items)")
    p.set_defaults(func=cmd_witnesses)
    a = subparsers.add_parser("all-c", help="regenerate every agent-C corpus (sources, arithmetic, special, witnesses)")
    a.add_argument("--spec", type=int, default=None)
    a.add_argument("--spell", type=int, default=None)
    a.set_defaults(func=cmd_all)
