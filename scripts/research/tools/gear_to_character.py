#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""End-to-end research proof: gear resolver -> character resolver.

    equipped loadout
      -> gearing.loadout.resolve_loadout        (item facts, per §gearing doc)
      -> aggregate raw stat / rating totals
      -> charstats.character.CharacterResolver  (character facts)
      -> derived naked+gear quantities

The point is to show the *information boundary* is sufficient: the character
side needs nothing from the gear side except ``{ItemModType: amount}``,
``{CombatRating: amount}``, armor, and the satisfied item-set spell roots.  It
never sees an ItemID, a bonus list, an item level or a curve.

This is not a Core integration and computes no combat result.

    python3 scripts/research/tools/gear_to_character.py \
        --base-stats scripts/research/tests/fixtures/synthetic_base_stats.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from charstats.basestats import BaseStatTable                      # noqa: E402
from charstats.character import CharacterResolver, Contributions   # noqa: E402
from charstats.identity import MAX_STATS, STAT_NAMES               # noqa: E402
from charstats.primary import ITEM_MOD_TO_STATS                    # noqa: E402
from charstats.report import render_character                      # noqa: E402
from gearing.loadout import Loadout, LoadoutEntry, resolve_loadout  # noqa: E402
from gearing.resolver import GearResolver                          # noqa: E402
from gearing.tables import DEFAULT_TABLES                          # noqa: E402

#: The three proof loadouts, one per primary-stat family.  Item ids and the
#: ItemContext are the *only* gear knowledge here; everything else is derived.
#: Contexts are the MapDifficulty-derived raid contexts from the gearing doc.
PROOF_CASES: tuple[dict[str, Any], ...] = (
    {
        "label": "Strength tank -- Blood Death Knight",
        "race_id": 2, "class_id": 6, "spec_name": "Blood",
        # ItemSet 2055, Baleful Grave-Knight's Crucible (DK tier)
        "items": [271474, 271472, 271477, 271473, 271475],
        "context": 6,
    },
    {
        "label": "Agility melee -- Windwalker Monk",
        "race_id": 1, "class_id": 10, "spec_name": "Windwalker",
        # ItemSet 2061, Guile of the Monkey King (Monk tier)
        "items": [271519, 271517, 271522, 271518, 271520],
        "context": 6,
    },
    {
        "label": "Intellect healer -- Mistweaver Monk",
        "race_id": 1, "class_id": 10, "spec_name": "Mistweaver",
        "items": [271519, 271517, 271522, 271518, 271520],
        "context": 6,
    },
    {
        "label": "Intellect caster healer -- Discipline Priest",
        "race_id": 1, "class_id": 5, "spec_name": "Discipline",
        # ItemSet 2063, Cosmic Penitent's Raiment (Priest tier)
        "items": [271555, 271553, 271554, 271556, 271558],
        "context": 6,
    },
)

#: How a gearing ``stat_totals`` key maps back to an ItemModType.  The plain
#: stat names route 1:1; the combined identities need the ItemModType.
COMBINED_STAT_NAMES = {"Agi|Str|Int": 71, "Agi|Str": 72, "Agi|Int": 73,
                       "Str|Int": 74}


def gear_contributions(loadout_result) -> tuple[Contributions, dict[str, Any]]:
    """Reduce a gearing loadout result to the character-side inputs only."""
    name_to_stat = {STAT_NAMES[i]: i for i in range(MAX_STATS)}
    stats: dict[int, int] = {}
    handed_over: dict[str, Any] = {"stats": {}, "ratings": {}, "armor": 0}

    for stat_name, amount in loadout_result.stat_totals.items():
        amount = int(amount)
        if stat_name in name_to_stat:
            index = name_to_stat[stat_name]
            stats[index] = stats.get(index, 0) + amount
            handed_over["stats"][stat_name] = amount
        elif stat_name in COMBINED_STAT_NAMES:
            item_mod = COMBINED_STAT_NAMES[stat_name]
            for index in ITEM_MOD_TO_STATS[item_mod]:
                stats[index] = stats.get(index, 0) + amount
            handed_over["stats"][f"ItemModType {item_mod} ({stat_name})"] = amount

    ratings = {k: int(v) for k, v in loadout_result.rating_totals.items()}
    handed_over["ratings"] = dict(sorted(ratings.items()))
    armor = sum(item.armor for item in loadout_result.items)
    handed_over["armor"] = armor
    handed_over["item_set_spell_roots"] = [
        {"spell_id": b["spell_id"], "threshold": b["threshold"],
         "item_set_id": b["item_set_id"]}
        for b in loadout_result.set_bonuses]

    return (Contributions(stats=stats, ratings=ratings, armor=armor,
                          source="gearing.loadout.resolve_loadout aggregate"),
            handed_over)


def run_case(gear: GearResolver, character: CharacterResolver,
             case: dict[str, Any]) -> dict[str, Any]:
    spec = character.identity.find_spec(case["class_id"], case["spec_name"])
    loadout = Loadout(
        entries=[LoadoutEntry(item_id=i, context=case["context"])
                 for i in case["items"]],
        player_level=90, chr_spec_id=spec.spec_id)
    result = resolve_loadout(gear, loadout)
    contributions, handed_over = gear_contributions(result)
    resolved = character.resolve(case["race_id"], case["class_id"], 90,
                                 spec.spec_id, contributions)
    return {
        "label": case["label"],
        "identity": {
            "race_id": case["race_id"], "class_id": case["class_id"],
            "spec_id": spec.spec_id, "spec": spec.name,
        },
        "gear": {
            "item_ids": case["items"], "context": case["context"],
            "effective_item_levels": [i.effective_item_level
                                      for i in result.items],
        },
        "boundary_payload": handed_over,
        "character": resolved.to_dict(),
        "_rendered": render_character(resolved),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tables", type=Path, default=DEFAULT_TABLES)
    parser.add_argument("--base-stats", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    gear = GearResolver(args.tables)
    character = CharacterResolver(args.tables,
                                  BaseStatTable.from_json_file(args.base_stats))
    cases = [run_case(gear, character, case) for case in PROOF_CASES]

    if args.json:
        payload = {"cases": [{k: v for k, v in c.items() if k != "_rendered"}
                             for c in cases]}
        out = json.dumps(payload, indent=2, default=str)
    else:
        blocks = []
        for case in cases:
            blocks.append("=" * 72)
            blocks.append(f"{case['label']}  items {case['gear']['item_ids']} "
                          f"at item levels {case['gear']['effective_item_levels']}")
            blocks.append("")
            blocks.append("  boundary payload handed to the character side:")
            blocks.append("    stats   : "
                          + json.dumps(case["boundary_payload"]["stats"]))
            blocks.append("    ratings : "
                          + json.dumps(case["boundary_payload"]["ratings"]))
            blocks.append(f"    armor   : {case['boundary_payload']['armor']}")
            blocks.append("    set spell roots: " + json.dumps(
                case["boundary_payload"]["item_set_spell_roots"]))
            blocks.append("")
            blocks.append(case["_rendered"])
            blocks.append("")
        out = "\n".join(blocks)

    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
