"""Command line interface.

Fail-closed policy: every command that needs a content selection asks for it.
The only shortcut is ``--season latest-in-source``, which is an explicit opt-in
and prints its own caveat.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import SourceError
from .census import census
from .content import (
    MYTHIC_PLUS_END_OF_RUN_CONTEXTS,
    MYTHIC_PLUS_VAULT_CONTEXTS,
    resolve_roster,
    split_mythic_plus_contexts,
)
from .curves import round_half_away
from .enums import DEFAULT_PLAYER_LEVEL, context_name
from .loadout import (
    Loadout,
    LoadoutEntry,
    parse_item_id_file,
    parse_item_id_spec,
    resolve_loadout,
)
from .report import (
    REPORT_COLUMNS,
    render_csv,
    render_explain,
    render_loadout,
    render_markdown,
    render_table,
    report_row,
)
from .resolver import GearResolver, Variant, distinct_key
from .roster import RosterDiscovery
from .tables import DEFAULT_TABLES

ROSTER_COLUMNS = (
    "item_id", "name", "variant", "context", "item_level", "quality",
    "inv_type", "primary", "stamina", "secondary_1", "secondary_2",
    "armor_or_weapon", "bonus_lists", "curves",
)


def _emit(payload: Any, args: argparse.Namespace, text: str) -> None:
    if getattr(args, "json", False):
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = text
    target = getattr(args, "output", None)
    if target:
        Path(target).write_text(out + "\n", encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    else:
        print(out)


def _render_rows(rows: Sequence[dict[str, Any]], columns: Sequence[str],
                 args: argparse.Namespace) -> str:
    style = getattr(args, "format", "table")
    if style == "markdown":
        return render_markdown(rows, columns)
    if style == "csv":
        return render_csv(rows, columns)
    return render_table(rows, columns)


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("table", "markdown", "csv"),
                        default="table")
    parser.add_argument("--output", type=Path, default=None,
                        help="write to this file instead of stdout")


def _select_window(discovery: RosterDiscovery, args: argparse.Namespace):
    tier_id = args.journal_tier
    if tier_id is None:
        tier_id = discovery.current_season_tier_id()
    windows = discovery.season_windows(tier_id)
    if not windows:
        raise SourceError(f"JournalTier {tier_id} has no JournalTierXInstance rows")
    if args.season == "latest-in-source":
        window = windows[0]
        print(
            "NOTE: --season latest-in-source picked availability condition "
            f"{window.availability_condition_id} by ordering on TimeEvent id. "
            "That is a heuristic over source rows, not proof that this season "
            "is live; TimeEvent timestamps are absent from this snapshot and "
            "from TrinityCore.", file=sys.stderr)
        return window
    try:
        wanted = int(args.season)
    except (TypeError, ValueError):
        raise SourceError(
            f"--season must be an AvailabilityCondition id or "
            f"'latest-in-source'; got {args.season!r}") from None
    for window in windows:
        if window.availability_condition_id == wanted:
            return window
    known = [w.availability_condition_id for w in windows]
    raise SourceError(
        f"JournalTier {tier_id} has no availability condition {wanted}; "
        f"known conditions are {known}")


def _add_season(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--journal-tier", type=int, default=None,
        help="JournalTier id (default: the tier using the current-season "
             "Expansion sentinel)")
    parser.add_argument(
        "--season", required=True,
        help="AvailabilityCondition id from `seasons`, or 'latest-in-source' "
             "to take the newest window by TimeEvent id (a heuristic)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="item-scaling",
        description="Reproduce the retail item scaling pipeline from the "
                    "checked-in Wago tables, mirroring TrinityCore's direct "
                    "consumers.  Research tooling, not a production API.")
    parser.add_argument("--tables", type=Path, default=DEFAULT_TABLES,
                        help="directory holding the CSV/GameTable snapshot")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    # -- single item ----------------------------------------------------
    show = sub.add_parser("show", help="resolve one item in one context")
    show.add_argument("item_id", type=int)
    show.add_argument("--context", type=int, default=0)
    show.add_argument("--bonus-list", dest="bonus_lists", action="append",
                      default=[], type=int, help="extra bonus list id (repeatable)")
    show.add_argument("--gem", dest="gems", action="append", default=[],
                      help="socket:itemid, e.g. 0:241143 (repeatable)")
    show.add_argument("--enchant", dest="enchant_ids", action="append",
                      default=[], type=int)
    show.add_argument("--no-auto-bonus-lists", action="store_true",
                      help="skip ItemBonusMgr tree resolution")
    show.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    show.add_argument("--mythic-plus-level", type=int, default=None)
    show.add_argument("--pvp", action="store_true")
    show.add_argument("--fixed-level", type=int, default=0,
                      help="ITEM_MODIFIER_TIMEWALKER_LEVEL equivalent")
    show.add_argument("--min-item-level", type=int, default=0)
    show.add_argument("--min-item-level-cutoff", type=int, default=0)
    show.add_argument("--max-item-level", type=int, default=0)
    show.add_argument("--squish-patch", type=int, default=None,
                      help="realm build patch (major*10000+minor*100+bugfix) "
                           "for ItemSquishEra application")
    show.add_argument("--output", type=Path, default=None)

    # -- many items ------------------------------------------------------
    items = sub.add_parser("items", help="resolve several items in one run")
    items.add_argument("specs", nargs="*",
                       help="<item>[:<context>][/<bonus>,<bonus>...]")
    items.add_argument("--file", type=Path, default=None,
                       help="file of item ids, one per line, # comments allowed")
    items.add_argument("--context", type=int, default=None,
                       help="apply this context to every item lacking its own")
    items.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    items.add_argument("--explain", action="store_true")
    _add_format(items)

    loadout = sub.add_parser("loadout", help="resolve an equipped loadout")
    loadout.add_argument("--json-file", type=Path, required=True)
    loadout.add_argument("--explain", action="store_true")
    loadout.add_argument("--output", type=Path, default=None)

    # -- discovery -------------------------------------------------------
    variants = sub.add_parser("variants",
                              help="discover and evaluate every source-backed variant")
    variants.add_argument("item_id", type=int)
    variants.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    variants.add_argument("--all", action="store_true",
                          help="keep variants that resolve identically")
    variants.add_argument("--explain", action="store_true")
    _add_format(variants)

    upgrades = sub.add_parser("upgrades",
                              help="list reachable ItemBonusListGroupEntry steps")
    upgrades.add_argument("item_id", type=int)
    upgrades.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    upgrades.add_argument("--evaluate", action="store_true")
    _add_format(upgrades)

    # -- inspection ------------------------------------------------------
    curve = sub.add_parser("curve", help="explain one curve evaluation")
    curve.add_argument("curve_id", type=int)
    curve.add_argument("x", type=float, nargs="?", default=None)
    curve.add_argument("--output", type=Path, default=None)

    gem = sub.add_parser("gem", help="describe a gem item's enchant payload")
    gem.add_argument("item_id", type=int)
    gem.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    gem.add_argument("--output", type=Path, default=None)

    enchant = sub.add_parser("enchant", help="describe a SpellItemEnchantment")
    enchant.add_argument("enchant_id", type=int)
    enchant.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    enchant.add_argument("--restricted", action="store_true",
                         help="use ScalingClassRestricted (scaled ilvl context)")
    enchant.add_argument("--output", type=Path, default=None)

    ratings = sub.add_parser("ratings", help="rating -> percentage conversion")
    ratings.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    ratings.add_argument("--amount", type=float, default=1000.0)
    _add_format(ratings)

    item_set = sub.add_parser("item-set", help="describe an ItemSet and its spells")
    item_set.add_argument("item_set_id", type=int)
    item_set.add_argument("--output", type=Path, default=None)

    census_parser = sub.add_parser("census", help="population and coverage counts")
    census_parser.add_argument("--output", type=Path, default=None)

    # -- content rosters --------------------------------------------------
    tiers = sub.add_parser("tiers", help="list JournalTier rows")
    _add_format(tiers)

    seasons = sub.add_parser("seasons",
                             help="list a tier's AvailabilityCondition windows")
    seasons.add_argument("--journal-tier", type=int, default=None)
    _add_format(seasons)

    instances = sub.add_parser("instances",
                               help="list the instances in a season window")
    _add_season(instances)
    _add_format(instances)

    raid = sub.add_parser("raid", help="dump the raid roster of a season window")
    _add_season(raid)
    raid.add_argument("--journal-instance", type=int, default=None,
                      help="restrict to one raid")
    raid.add_argument("--all-difficulties", action="store_true",
                      help="resolve every MapDifficulty-derived context")
    raid.add_argument("--context", type=int, action="append", default=None,
                      help="resolve only this ItemContext (repeatable)")
    raid.add_argument("--tier-only", action="store_true",
                      help="only items with a non-zero ItemSparse.ItemSet")
    raid.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    raid.add_argument("--include-non-equippable", action="store_true")
    _add_format(raid)

    mplus = sub.add_parser("mythic-plus",
                           help="dump the dungeon roster of a season window")
    _add_season(mplus)
    mplus.add_argument("--journal-instance", type=int, default=None)
    mplus.add_argument("--population", choices=("base", "end-of-run", "vault", "all"),
                       default="base",
                       help="which Mythic+ item population to resolve")
    mplus.add_argument("--keystone-level", type=int, default=None)
    mplus.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    mplus.add_argument("--include-non-equippable", action="store_true")
    _add_format(mplus)

    corpus = sub.add_parser("corpus",
                            help="raid + Mythic+ rosters in one machine-readable dump")
    _add_season(corpus)
    corpus.add_argument("--keystone-level", type=int, default=None)
    corpus.add_argument("--player-level", type=int, default=DEFAULT_PLAYER_LEVEL)
    corpus.add_argument("--output", type=Path, default=None)

    return parser


# -- command implementations ---------------------------------------------


def _cmd_show(resolver: GearResolver, args: argparse.Namespace) -> int:
    from .resolver import GemSlot
    gems = []
    for spec in args.gems:
        socket, _, item = spec.partition(":")
        if not item:
            raise SourceError(f"--gem expects socket:itemid, got {spec!r}")
        gems.append(GemSlot(socket_index=int(socket), gem_item_id=int(item)))
    variant = Variant(label=context_name(args.context), context=args.context,
                      mythic_plus_keystone_level=args.mythic_plus_level,
                      origin="supplied on the command line")
    resolved = resolver.resolve(
        args.item_id, variant,
        player_level=args.player_level,
        extra_bonus_lists=args.bonus_lists,
        gems=gems,
        enchant_ids=args.enchant_ids,
        fixed_level=args.fixed_level,
        min_item_level=args.min_item_level,
        min_item_level_cutoff=args.min_item_level_cutoff,
        max_item_level=args.max_item_level,
        pvp_bonus=args.pvp,
        current_build_patch=args.squish_patch,
        auto_bonus_lists=not args.no_auto_bonus_lists)
    _emit(resolved.to_dict(), args, render_explain(resolved))
    return 0


def _cmd_items(resolver: GearResolver, args: argparse.Namespace) -> int:
    entries: list[LoadoutEntry] = [parse_item_id_spec(s) for s in args.specs]
    if args.file:
        entries.extend(LoadoutEntry(item_id=i) for i in parse_item_id_file(args.file))
    if not entries:
        raise SourceError("no items given; pass ids or --file")
    if args.context is not None:
        entries = [e if e.context else LoadoutEntry(
            item_id=e.item_id, context=args.context,
            bonus_list_ids=e.bonus_list_ids) for e in entries]
    loadout = Loadout(entries=entries, player_level=args.player_level)
    result = resolve_loadout(resolver, loadout)
    if args.explain and not args.json:
        print("\n\n".join(render_explain(i) for i in result.items))
        return 0
    rows = [report_row(i) for i in result.items]
    _emit(result.to_dict(resolver.ratings), args, _render_rows(rows, REPORT_COLUMNS, args))
    return 0


def _cmd_loadout(resolver: GearResolver, args: argparse.Namespace) -> int:
    loadout = Loadout.from_json_file(args.json_file)
    result = resolve_loadout(resolver, loadout)
    if args.explain and not args.json:
        print("\n\n".join(render_explain(i) for i in result.items))
        return 0
    _emit(result.to_dict(resolver.ratings), args, render_loadout(result))
    return 0


def _cmd_variants(resolver: GearResolver, args: argparse.Namespace) -> int:
    found = resolver.discover_variants(args.item_id)
    resolved_all = []
    seen: set[Any] = set()
    for variant in found:
        resolved = resolver.resolve(args.item_id, variant,
                                    player_level=args.player_level)
        if not args.all:
            key = distinct_key(resolved)
            if key in seen:
                continue
            seen.add(key)
        resolved_all.append(resolved)
    payload = {"item_id": args.item_id,
               "discovered_variant_count": len(found),
               "resolved": [x.to_dict() for x in resolved_all]}
    if args.explain and not args.json:
        print("\n\n".join(render_explain(x) for x in resolved_all))
        return 0
    rows = [report_row(x) for x in resolved_all]
    _emit(payload, args, _render_rows(rows, REPORT_COLUMNS, args))
    return 0


def _cmd_upgrades(resolver: GearResolver, args: argparse.Namespace) -> int:
    steps = resolver.discover_upgrade_steps(args.item_id)
    rows: list[dict[str, Any]] = []
    for step in steps:
        row = dict(step)
        if args.evaluate and step["bonus_list_id"]:
            variant = Variant(
                label=f"seq{step['sequence_value']}",
                extra_bonus_lists=(step["bonus_list_id"],),
                origin=f"ItemBonusListGroupEntry {step['group_entry_id']}")
            resolved = resolver.resolve(args.item_id, variant,
                                        player_level=args.player_level,
                                        auto_bonus_lists=False)
            row["effective_item_level"] = resolved.effective_item_level
            row["quality"] = resolved.quality
        rows.append(row)
    columns = ["item_bonus_list_group_id", "group_entry_id", "sequence_value",
               "bonus_list_id", "item_level_selector_id",
               "selector_min_item_level", "player_condition_id"]
    if args.evaluate:
        columns += ["effective_item_level", "quality"]
    _emit({"item_id": args.item_id, "upgrade_steps": rows}, args,
          _render_rows(rows, columns, args))
    return 0


def _cmd_curve(resolver: GearResolver, args: argparse.Namespace) -> int:
    points = resolver.curves.points(args.curve_id)
    if not points:
        raise SourceError(
            f"curve {args.curve_id} has no CurvePoint rows in this snapshot")
    payload: dict[str, Any] = {
        "curve_id": args.curve_id,
        "curve_type": resolver.curves.curve_type(args.curve_id),
        "interpolation": resolver.curves.mode(args.curve_id),
        "point_count": len(points),
        "x_range": list(resolver.curves.x_range(args.curve_id)),
        "points": [list(p) for p in points],
    }
    text = [f"curve {args.curve_id}  type={payload['curve_type']} "
            f"mode={payload['interpolation']} points={len(points)} "
            f"x in [{points[0][0]:g}, {points[-1][0]:g}]"]
    for i, (x, y) in enumerate(points):
        text.append(f"  [{i:>3}] x={x!r} y={y!r}")
    if args.x is not None:
        value = resolver.curves.value_at(args.curve_id, args.x, consumer="cli")
        evaluation = resolver.curves.evaluations[-1]
        payload["evaluation"] = evaluation.to_dict()
        text.append(f"  value_at({args.x!r}) = {value!r} "
                    f"(std::round -> {round_half_away(value)})")
        text.append(f"  bracket: {[list(p) for p in evaluation.bracket]}")
        if evaluation.clamped:
            text.append(f"  clamped: {evaluation.clamped}")
    _emit(payload, args, "\n".join(text))
    return 0


def _cmd_gem(resolver: GearResolver, args: argparse.Namespace) -> int:
    proto = resolver.items.get(args.item_id)
    payload = resolver.enchants.describe_gem(proto, args.player_level)
    _emit(payload, args, json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_enchant(resolver: GearResolver, args: argparse.Namespace) -> int:
    enchant = resolver.enchants.resolve(args.enchant_id, args.player_level,
                                        restricted=args.restricted)
    payload = enchant.to_dict()
    _emit(payload, args, json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_ratings(resolver: GearResolver, args: argparse.Namespace) -> int:
    conversions = resolver.ratings.convert_all(args.amount, args.player_level)
    rows = [c.to_dict() for c in conversions]
    for row in rows:
        row.pop("curve", None)
    columns = ["rating", "combat_ratings_column", "per_point", "amount",
               "linear_percent", "diminishing_curve_id", "final_percent"]
    _emit({"player_level": args.player_level,
           "ratings": [c.to_dict() for c in conversions]}, args,
          _render_rows(rows, columns, args))
    return 0


def _cmd_item_set(resolver: GearResolver, args: argparse.Namespace) -> int:
    payload = resolver.sets.get(args.item_set_id).to_dict()
    _emit(payload, args, json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_census(resolver: GearResolver, args: argparse.Namespace) -> int:
    payload = census(resolver)
    text = [render_table([{"population": k, "count": v}
                          for k, v in payload["populations"].items()],
                         ["population", "count"]),
            "", "unsupported source relationships"]
    for key, value in payload["unsupported"].items():
        text.append(f"  {key}: {value}")
    _emit(payload, args, "\n".join(text))
    return 0


def _cmd_tiers(resolver: GearResolver, args: argparse.Namespace) -> int:
    discovery = RosterDiscovery(resolver.tables)
    rows = discovery.journal_tiers()
    _emit({"journal_tiers": rows}, args,
          _render_rows(rows, ["journal_tier_id", "name", "expansion",
                              "player_condition_id"], args))
    return 0


def _cmd_seasons(resolver: GearResolver, args: argparse.Namespace) -> int:
    discovery = RosterDiscovery(resolver.tables)
    tier_id = args.journal_tier
    if tier_id is None:
        tier_id = discovery.current_season_tier_id()
    windows = discovery.season_windows(tier_id)
    rows = []
    for window in windows:
        rows.append({
            "availability_condition_id": window.availability_condition_id,
            "start_time_events": ",".join(map(str, window.start_time_event_ids)) or "-",
            "end_time_events": ",".join(map(str, window.end_time_event_ids)) or "-",
            "modifier_tree_id": window.modifier_tree_id or "-",
            "instances": len(window.journal_instance_ids),
        })
    text = _render_rows(rows, list(rows[0]) if rows else ["availability_condition_id"],
                        args)
    text += ("\n\nActivation is NOT derivable from this snapshot: TimeEvent "
             "timestamps are absent from the data and TrinityCore only hardcodes "
             "a handful of old ids.  Pick a window explicitly.")
    _emit({"journal_tier_id": tier_id,
           "windows": [w.to_dict() for w in windows]}, args, text)
    return 0


def _cmd_instances(resolver: GearResolver, args: argparse.Namespace) -> int:
    discovery = RosterDiscovery(resolver.tables)
    window = _select_window(discovery, args)
    rows = []
    for instance_id in window.journal_instance_ids:
        info = discovery.instance_info(instance_id)
        loot = discovery.instance_loot(instance_id)
        rows.append({**info.to_dict(), "loot_items": len(loot)})
    _emit({"window": window.to_dict(), "instances": rows}, args,
          _render_rows(rows, ["journal_instance_id", "name", "map_id", "map_name",
                              "instance_type_name", "map_expansion_id", "flags",
                              "loot_items"], args))
    return 0


def _roster_rows(resolved_roster) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member in resolved_roster.items:
        if member.skipped_reason and not member.variants:
            continue
        for variant in member.variants:
            row = report_row(variant)
            row["context"] = variant.variant.context
            rows.append(row)
    return rows


def _cmd_raid(resolver: GearResolver, args: argparse.Namespace) -> int:
    discovery = RosterDiscovery(resolver.tables)
    window = _select_window(discovery, args)
    roster = discovery.raid_roster(window)
    if args.journal_instance is not None:
        roster.instances = [i for i in roster.instances
                            if i.journal_instance_id == args.journal_instance]
        if not roster.instances:
            raise SourceError(
                f"journal instance {args.journal_instance} is not a raid in "
                f"availability condition {window.availability_condition_id}")
        keep = {i.journal_instance_id for i in roster.instances}
        roster.loot = [e for e in roster.loot if e.journal_instance_id in keep]
        roster.difficulty_contexts = {
            k: v for k, v in roster.difficulty_contexts.items() if k in keep}
    contexts = args.context
    if contexts is None:
        contexts = roster.contexts() if args.all_difficulties else None
    resolved = resolve_roster(
        resolver, roster, contexts=contexts, player_level=args.player_level,
        equippable_only=not args.include_non_equippable,
        item_set_only=args.tier_only)
    rows = _roster_rows(resolved)
    _emit(resolved.to_dict(), args, _render_rows(rows, ROSTER_COLUMNS, args))
    return 0


def _cmd_mythic_plus(resolver: GearResolver, args: argparse.Namespace) -> int:
    discovery = RosterDiscovery(resolver.tables)
    window = _select_window(discovery, args)
    roster = discovery.dungeon_roster(window)
    if args.journal_instance is not None:
        keep = {args.journal_instance}
        roster.instances = [i for i in roster.instances
                            if i.journal_instance_id in keep]
        if not roster.instances:
            raise SourceError(
                f"journal instance {args.journal_instance} is not a dungeon in "
                f"availability condition {window.availability_condition_id}")
        roster.loot = [e for e in roster.loot if e.journal_instance_id in keep]
        roster.difficulty_contexts = {
            k: v for k, v in roster.difficulty_contexts.items() if k in keep}

    base_contexts = roster.contexts()
    buckets = split_mythic_plus_contexts(base_contexts)
    if args.population == "base":
        contexts = buckets["base"]
    elif args.population == "end-of-run":
        contexts = buckets["end_of_run"] or list(MYTHIC_PLUS_END_OF_RUN_CONTEXTS)
    elif args.population == "vault":
        contexts = buckets["vault"] or list(MYTHIC_PLUS_VAULT_CONTEXTS)
    else:
        contexts = sorted(set(base_contexts)
                          | set(MYTHIC_PLUS_END_OF_RUN_CONTEXTS)
                          | set(MYTHIC_PLUS_VAULT_CONTEXTS))
    resolved = resolve_roster(
        resolver, roster, contexts=contexts, player_level=args.player_level,
        mythic_plus_keystone_level=args.keystone_level,
        equippable_only=not args.include_non_equippable)
    resolved.notes.append(
        f"population={args.population}; MapDifficulty-derived contexts "
        f"{base_contexts}; buckets {buckets}")
    rows = _roster_rows(resolved)
    _emit(resolved.to_dict(), args, _render_rows(rows, ROSTER_COLUMNS, args))
    return 0


def _cmd_corpus(resolver: GearResolver, args: argparse.Namespace) -> int:
    discovery = RosterDiscovery(resolver.tables)
    window = _select_window(discovery, args)
    raid_roster = discovery.raid_roster(window)
    dungeon_roster = discovery.dungeon_roster(window)
    raid = resolve_roster(resolver, raid_roster,
                          contexts=raid_roster.contexts(),
                          player_level=args.player_level)
    dungeon = resolve_roster(resolver, dungeon_roster,
                             contexts=dungeon_roster.contexts(),
                             player_level=args.player_level,
                             mythic_plus_keystone_level=args.keystone_level)
    payload = {
        "window": window.to_dict(),
        "player_level": args.player_level,
        "raid": raid.to_dict(),
        "mythic_plus": dungeon.to_dict(),
    }
    summary = (f"window {window.availability_condition_id}: "
               f"raid {len(raid.items)} items / {sum(len(i.variants) for i in raid.items)} variants, "
               f"dungeons {len(dungeon.items)} items / "
               f"{sum(len(i.variants) for i in dungeon.items)} variants")
    args.json = True
    _emit(payload, args, summary)
    print(summary, file=sys.stderr)
    return 0


_COMMANDS = {
    "show": _cmd_show,
    "items": _cmd_items,
    "loadout": _cmd_loadout,
    "variants": _cmd_variants,
    "upgrades": _cmd_upgrades,
    "curve": _cmd_curve,
    "gem": _cmd_gem,
    "enchant": _cmd_enchant,
    "ratings": _cmd_ratings,
    "item-set": _cmd_item_set,
    "census": _cmd_census,
    "tiers": _cmd_tiers,
    "seasons": _cmd_seasons,
    "instances": _cmd_instances,
    "raid": _cmd_raid,
    "mythic-plus": _cmd_mythic_plus,
    "corpus": _cmd_corpus,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    resolver = GearResolver(args.tables)
    handler = _COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces this
        return 1
    return handler(resolver, args)


def run() -> int:
    try:
        return main()
    except SourceError as error:
        print(f"source error: {error}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # pragma: no cover
        return 0
