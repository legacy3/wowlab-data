"""Command line interface for the character-stat research tool.

Fail-closed policy: base primary stats are not in the snapshot, so every
command that needs them requires ``--base-stats FILE``.  Commands that only
read DB2 (``specs``, ``mastery``, ``acquisition``, ``routing``, ``census``)
work without it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import CharacterSourceError
from .basestats import BaseStatTable
from .census import census
from .character import CharacterResolver, Contributions
from .identity import MAX_STATS, STAT_NAMES
from .mastery import build_profile
from .primary import COMBINED_ITEM_MODS, ITEM_MOD_TO_STATS
from .report import render_character, render_rows
from gearing.tables import DEFAULT_TABLES

#: ``--<name>`` flags that feed Contributions.stats, by Stats index.
STAT_FLAGS = {"strength": 0, "agility": 1, "stamina": 2, "intellect": 3,
              "spirit": 4}

#: ``--<name>-rating`` flags that feed Contributions.ratings.
RATING_FLAGS = {
    "crit": ("CritMelee", "CritRanged", "CritSpell"),
    "haste": ("HasteMelee", "HasteRanged", "HasteSpell"),
    "mastery": ("Mastery",),
    "versatility": ("VersatilityDamageDone", "VersatilityHealingDone",
                    "VersatilityDamageTaken"),
    "leech": ("Lifesteal",),
    "avoidance": ("Avoidance",),
    "speed": ("Speed",),
    "dodge": ("Dodge",),
    "parry": ("Parry",),
    "block": ("Block",),
}


def _emit(payload: Any, args: argparse.Namespace, text: str) -> None:
    out = json.dumps(payload, indent=2, default=str) if args.json else text
    target = getattr(args, "output", None)
    if target:
        Path(target).write_text(out + "\n", encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    else:
        print(out)


def _add_identity(parser: argparse.ArgumentParser, require_spec: bool = False) -> None:
    parser.add_argument("--race", type=str, default=None, help="race name")
    parser.add_argument("--race-id", type=int, default=None)
    parser.add_argument("--class", dest="class_name", type=str, default=None)
    parser.add_argument("--class-id", type=int, default=None)
    parser.add_argument("--spec", type=str, default=None,
                        required=False, help="specialization name")
    parser.add_argument("--spec-id", type=int, default=None)
    parser.add_argument("--level", type=int, default=90)


def _add_contributions(parser: argparse.ArgumentParser) -> None:
    for name in STAT_FLAGS:
        parser.add_argument(f"--{name}", type=int, default=0,
                            help=f"supplied {name} contribution")
    for name in RATING_FLAGS:
        parser.add_argument(f"--{name}-rating", type=int, default=0,
                            dest=f"{name}_rating")
    parser.add_argument("--armor", type=int, default=0)
    parser.add_argument("--attack-power", type=int, default=0)
    parser.add_argument("--spell-power", type=int, default=0)
    parser.add_argument("--health", type=int, default=0)
    parser.add_argument("--gear-json", type=Path, default=None,
                        help="a gearing `loadout --json` payload; its "
                             "stat_totals and rating_totals are folded in")
    parser.add_argument("--armor-specialization", action="store_true",
                        help="apply the spec's armour-specialization passive, "
                             "assuming all eight armour slots match")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="character-stats",
        description="Reconstruct a naked current character from source and "
                    "fold in explicitly supplied stat/rating contributions.  "
                    "Research tooling, not a production API.")
    parser.add_argument("--tables", type=Path, default=DEFAULT_TABLES)
    parser.add_argument("--base-stats", type=Path, default=None,
                        help="JSON with player_classlevelstats / "
                             "player_racestats rows (see README in the doc)")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    naked = sub.add_parser("naked", help="construct a character with no gear")
    _add_identity(naked)
    naked.add_argument("--armor-specialization", action="store_true")
    naked.add_argument("--output", type=Path, default=None)

    stats = sub.add_parser("stats", help="naked character plus supplied contributions")
    _add_identity(stats)
    _add_contributions(stats)
    stats.add_argument("--output", type=Path, default=None)

    specs = sub.add_parser("specs", help="list classes and specializations")
    specs.add_argument("--class-id", type=int, default=None)
    specs.add_argument("--format", choices=("table", "markdown", "csv"),
                       default="table")
    specs.add_argument("--output", type=Path, default=None)

    mastery = sub.add_parser("mastery", help="mastery profile and classification")
    mastery.add_argument("--spec-id", type=int, default=None)
    mastery.add_argument("--class-id", type=int, default=None)
    mastery.add_argument("--mastery-value", type=float, default=0.0,
                         help="evaluate effect amounts at this Mastery value")
    mastery.add_argument("--format", choices=("table", "markdown", "csv"),
                         default="table")
    mastery.add_argument("--output", type=Path, default=None)

    acquisition = sub.add_parser(
        "acquisition", help="baseline spell acquisition for an identity")
    _add_identity(acquisition)
    acquisition.add_argument("--output", type=Path, default=None)

    routing = sub.add_parser(
        "routing", help="how combined ItemModTypes land for each spec")
    routing.add_argument("--class-id", type=int, default=None)
    routing.add_argument("--item-mod", type=int, action="append", default=None)
    routing.add_argument("--format", choices=("table", "markdown", "csv"),
                         default="table")
    routing.add_argument("--output", type=Path, default=None)

    corpus = sub.add_parser(
        "corpus", help="every class/spec at max level, naked")
    corpus.add_argument("--level", type=int, default=90)
    corpus.add_argument("--format", choices=("table", "markdown", "csv"),
                        default="table")
    corpus.add_argument("--output", type=Path, default=None)

    census_parser = sub.add_parser("census", help="population counts")
    census_parser.add_argument("--output", type=Path, default=None)

    return parser


def _resolve_identity(resolver: CharacterResolver, args: argparse.Namespace):
    if args.class_id is not None:
        klass = resolver.identity.klass(args.class_id)
    elif args.class_name:
        klass = resolver.identity.find_class(args.class_name)
    else:
        raise CharacterSourceError("pass --class or --class-id")

    if args.race_id is not None:
        race = resolver.identity.race(args.race_id)
    elif args.race:
        race = resolver.identity.find_race(args.race)
    else:
        raise CharacterSourceError("pass --race or --race-id")

    spec = None
    if args.spec_id is not None:
        spec = resolver.identity.spec(args.spec_id)
    elif args.spec:
        spec = resolver.identity.find_spec(klass.class_id, args.spec)
    return race, klass, spec


def _contributions(resolver: CharacterResolver,
                   args: argparse.Namespace) -> Contributions:
    stats: dict[int, int] = {}
    ratings: dict[str, int] = {}
    for name, index in STAT_FLAGS.items():
        amount = getattr(args, name, 0)
        if amount:
            stats[index] = stats.get(index, 0) + amount
    for name, slots in RATING_FLAGS.items():
        amount = getattr(args, f"{name}_rating", 0)
        if amount:
            for slot in slots:
                ratings[slot] = ratings.get(slot, 0) + amount

    source = "command line"
    if args.gear_json:
        raw = json.loads(Path(args.gear_json).read_text(encoding="utf-8"))
        if "stat_totals" not in raw or "rating_totals" not in raw:
            raise CharacterSourceError(
                f"{args.gear_json} is not a gearing `loadout --json` payload "
                f"(no stat_totals / rating_totals)")
        name_to_stat = {STAT_NAMES[i]: i for i in range(MAX_STATS)}
        for stat_name, amount in raw["stat_totals"].items():
            index = name_to_stat.get(stat_name)
            if index is None:
                continue          # ratings and other ItemModTypes, handled below
            stats[index] = stats.get(index, 0) + int(amount)
        # Combined primary identities arrive under their ItemModType name.
        from .primary import ITEM_MOD_TO_STATS as ROUTES
        combined_names = {"Agi|Str|Int": 71, "Agi|Str": 72, "Agi|Int": 73,
                          "Str|Int": 74}
        for stat_name, item_mod in combined_names.items():
            amount = int(raw["stat_totals"].get(stat_name, 0))
            if not amount:
                continue
            for index in ROUTES[item_mod]:
                stats[index] = stats.get(index, 0) + amount
        for slot, amount in raw["rating_totals"].items():
            ratings[slot] = ratings.get(slot, 0) + int(amount)
        source = f"gearing loadout payload {args.gear_json}"

    return Contributions(stats=stats, ratings=ratings, armor=args.armor,
                         attack_power=args.attack_power,
                         spell_power=args.spell_power, health=args.health,
                         source=source)


def _cmd_naked(resolver, args):
    race, klass, spec = _resolve_identity(resolver, args)
    character = resolver.resolve(
        race.race_id, klass.class_id, args.level,
        spec.spec_id if spec else None,
        apply_armor_specialization=args.armor_specialization)
    _emit(character.to_dict(), args, render_character(character))
    return 0


def _cmd_stats(resolver, args):
    race, klass, spec = _resolve_identity(resolver, args)
    character = resolver.resolve(
        race.race_id, klass.class_id, args.level,
        spec.spec_id if spec else None,
        _contributions(resolver, args),
        apply_armor_specialization=args.armor_specialization)
    _emit(character.to_dict(), args, render_character(character))
    return 0


def _cmd_specs(resolver, args):
    rows = []
    for spec in resolver.identity.specs(args.class_id):
        klass = resolver.identity.klass(spec.class_id)
        from .primary import routing_for
        routing = routing_for(klass, spec)
        rows.append({
            "class_id": spec.class_id, "class": klass.name,
            "spec_id": spec.spec_id, "spec": spec.name,
            "role": spec.role_name,
            "primary_stat_priority": spec.primary_stat_priority,
            "primary_stat": routing.primary_stat_name,
            "ap_per_str": klass.attack_power_per_strength,
            "ap_per_agi": klass.attack_power_per_agility,
            "ranged_ap_per_agi": klass.ranged_attack_power_per_agility,
            "mastery_spell": spec.mastery_spell_ids[0],
        })
    _emit({"specs": rows}, args, render_rows(rows, list(rows[0]), args.format))
    return 0


def _cmd_mastery(resolver, args):
    if args.spec_id is not None:
        specs = [resolver.identity.spec(args.spec_id)]
    else:
        specs = resolver.identity.specs(args.class_id)
    profiles = [build_profile(s, resolver.acquisition.mastery_spells(s))
                for s in specs]
    rows = []
    for profile in profiles:
        klass = resolver.identity.klass(profile.class_id)
        scaling = [e for s in profile.spells for e in s.effects
                   if e.participates_in_mastery]
        rows.append({
            "class": klass.name, "spec_id": profile.spec_id,
            "spec": profile.spec_name, "category": profile.category,
            "mastery_spells": ",".join(str(s.spell_id) for s in profile.spells),
            "scaling_effects": len(scaling),
            "auras": ",".join(sorted({e.aura_name for e in scaling})),
            "amount_at_mastery": ",".join(
                f"{e.amount_at(args.mastery_value):.3f}" for e in scaling),
        })
    _emit({"mastery_value": args.mastery_value,
           "profiles": [p.to_dict() for p in profiles]}, args,
          render_rows(rows, list(rows[0]), args.format))
    return 0


def _cmd_acquisition(resolver, args):
    race, klass, spec = _resolve_identity(resolver, args)
    payload: dict[str, Any] = {
        "race": race.to_dict(), "class": klass.to_dict(),
        "spec": spec.to_dict() if spec else None, "level": args.level,
    }
    if spec:
        passives = resolver.acquisition.spec_spells(spec.spec_id, args.level)
        payload["specialization_spells"] = [
            {**p.to_dict(), "name": resolver.acquisition.spell_name(p.spell_id)}
            for p in passives]
        payload["mastery_spells"] = resolver.acquisition.mastery_spells(spec)
        payload["armor_specializations"] = [
            a.to_dict() for a in
            resolver.acquisition.armor_specializations(spec.spec_id)]
    payload["racial_abilities"] = [
        {**r.to_dict(), "name": resolver.acquisition.spell_name(r.spell_id)}
        for r in resolver.acquisition.racial_abilities(race.race_id,
                                                       klass.class_id)]
    payload["note"] = ("acquisition only; nothing here claims the spell is "
                       "executable or currently active")
    text = [f"{race.name} {klass.name} {spec.name if spec else '<no spec>'} "
            f"level {args.level}"]
    if spec:
        text.append(f"  specialization spells: "
                    f"{len(payload['specialization_spells'])}")
        for entry in payload["specialization_spells"]:
            text.append(f"    {entry['spell_id']:>8} {entry['name']!r}"
                        + (f"  overrides {entry['overrides_spell_id']}"
                           if entry["overrides_spell_id"] else ""))
        text.append(f"  mastery spells: "
                    f"{[m['spell_id'] for m in payload['mastery_spells']]}")
        for armor in payload["armor_specializations"]:
            text.append(f"  armour specialization: spell {armor['spell_id']} "
                        f"+{armor['percent']:g}% {armor['stat_names']} "
                        f"({armor['armor_subclasses']})")
    text.append(f"  racial abilities: {len(payload['racial_abilities'])}")
    for entry in payload["racial_abilities"]:
        text.append(f"    {entry['spell_id']:>8} {entry['name']!r} "
                    f"(skill line {entry['skill_line']})")
    _emit(payload, args, "\n".join(text))
    return 0


def _cmd_routing(resolver, args):
    item_mods = args.item_mod or sorted(COMBINED_ITEM_MODS)
    rows = []
    for spec in resolver.identity.specs(args.class_id):
        klass = resolver.identity.klass(spec.class_id)
        from .primary import routing_for
        routing = routing_for(klass, spec)
        for item_mod in item_mods:
            detail = routing.effective_stats(item_mod)
            rows.append({
                "class": klass.name, "spec": spec.name,
                "spec_id": spec.spec_id,
                "item_mod_type": item_mod,
                "granted": "+".join(detail["granted_stats"]),
                "contributes": "+".join(
                    d["stat_name"] for d in detail["detail"] if d["contributes"])
                    or "-",
                "primary_stat": routing.primary_stat_name,
            })
    _emit({"rows": rows}, args, render_rows(rows, list(rows[0]), args.format))
    return 0


def _cmd_corpus(resolver, args):
    rows = []
    for spec in resolver.identity.specs():
        klass = resolver.identity.klass(spec.class_id)
        profile = build_profile(spec, resolver.acquisition.mastery_spells(spec))
        from .primary import routing_for
        routing = routing_for(klass, spec)
        row = {
            "class_id": spec.class_id, "class": klass.name,
            "spec_id": spec.spec_id, "spec": spec.name,
            "role": spec.role_name,
            "primary_stat": routing.primary_stat_name,
            "mastery_category": profile.category,
            "spec_passives": len(resolver.acquisition.spec_spells(spec.spec_id,
                                                                 args.level)),
            "armour_specs": len(
                resolver.acquisition.armor_specializations(spec.spec_id)),
        }
        if resolver.base_stats.base_stat_table is not None:
            try:
                character = resolver.resolve(1, spec.class_id, args.level,
                                             spec.spec_id)
            except CharacterSourceError as error:
                row["naked"] = f"unavailable: {error}"
            else:
                row["naked_health"] = character.max_health
                row["naked_ap"] = character.attack_power
                row["naked_sp"] = character.spell_power
        rows.append(row)
    _emit({"level": args.level, "rows": rows}, args,
          render_rows(rows, list(rows[0]), args.format))
    return 0


def _cmd_census(resolver, args):
    payload = census(resolver)
    text = ["populations"]
    for key, value in payload["populations"].items():
        text.append(f"  {key:<56} {value}")
    text.append("")
    text.append("mastery categories")
    for key, value in payload["mastery_categories"].items():
        text.append(f"  {key:<56} {value}")
    text.append("")
    text.append("primary stat routing")
    for key, value in payload["primary_stat_routing_counts"].items():
        text.append(f"  {key:<56} {value}")
    text.append("")
    text.append("unsupported")
    for key, value in payload["unsupported"].items():
        text.append(f"  {key}: {value}")
    _emit(payload, args, "\n".join(text))
    return 0


_COMMANDS = {
    "naked": _cmd_naked, "stats": _cmd_stats, "specs": _cmd_specs,
    "mastery": _cmd_mastery, "acquisition": _cmd_acquisition,
    "routing": _cmd_routing, "corpus": _cmd_corpus, "census": _cmd_census,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_stats = (BaseStatTable.from_json_file(args.base_stats)
                  if args.base_stats else None)
    resolver = CharacterResolver(args.tables, base_stats)
    handler = _COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover
        return 1
    return handler(resolver, args)


def run() -> int:
    try:
        return main()
    except CharacterSourceError as error:
        print(f"source error: {error}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover
        return 0
