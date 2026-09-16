"""Rendering: compact tables, markdown, CSV, and the explain view.

Column choice follows source semantics rather than a fixed "primary/secondary"
shape: an item contributes whatever stat slots it has, and the report says which.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Iterable, Sequence

from .enums import (
    BONUS_TYPE_NAMES,
    INVTYPE_NAMES,
    MOD_STAMINA,
    PRIMARY_MODS,
    QUALITY_NAMES,
    context_name,
)
from .resolver import ResolvedItem

REPORT_COLUMNS = (
    "item_id", "name", "variant", "context", "bonus_lists", "selector",
    "item_level", "quality", "inv_type", "primary", "stamina",
    "secondary_1", "secondary_2", "armor_or_weapon", "curves",
)


def report_row(resolved: ResolvedItem) -> dict[str, Any]:
    primary: list[tuple[str, int]] = []
    stamina = 0
    secondaries: list[tuple[str, int]] = []
    for stat in resolved.stats:
        if not stat.final_value:
            continue
        if stat.stat_type == MOD_STAMINA:
            stamina = stat.final_value
        elif stat.stat_type in PRIMARY_MODS:
            primary.append((stat.stat_name, stat.final_value))
        elif stat.ratings:
            secondaries.append((stat.stat_name, stat.final_value))
    if resolved.weapon and resolved.weapon["dps"]:
        combat = (f"{resolved.weapon['min_damage']:.1f}-"
                  f"{resolved.weapon['max_damage']:.0f} @ "
                  f"{resolved.weapon['dps']:.2f} dps")
    else:
        combat = str(resolved.armor)
    return {
        "item_id": resolved.item_id,
        "name": resolved.name,
        "variant": resolved.variant.label,
        "context": resolved.variant.context,
        "bonus_lists": ",".join(str(b) for b in resolved.applied_bonus_lists) or "-",
        "selector": resolved.selection.item_level_selector_id or "-",
        "item_level": resolved.effective_item_level,
        "quality": QUALITY_NAMES.get(resolved.quality, resolved.quality),
        "inv_type": INVTYPE_NAMES.get(resolved.inventory_type, resolved.inventory_type),
        "primary": ", ".join(f"{n} {v}" for n, v in primary) or "-",
        "stamina": stamina or "-",
        "secondary_1": (f"{secondaries[0][0]} {secondaries[0][1]}"
                        if secondaries else "-"),
        "secondary_2": (f"{secondaries[1][0]} {secondaries[1][1]}"
                        if len(secondaries) > 1 else "-"),
        "armor_or_weapon": combat,
        "curves": ",".join(sorted({str(c.curve_id)
                                   for c in resolved.curve_evaluations})) or "-",
    }


def render_table(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "(no rows)"
    widths = {c: max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows))
              for c in columns}
    out = [" | ".join(str(c).ljust(widths[c]) for c in columns),
           "-+-".join("-" * widths[c] for c in columns)]
    for row in rows:
        out.append(" | ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns))
    return "\n".join(out)


def render_markdown(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    if not rows:
        return "_(no rows)_"
    out = ["| " + " | ".join(columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|")
                                     for c in columns) + " |")
    return "\n".join(out)


def render_csv(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def render_explain(r: ResolvedItem) -> str:
    lines: list[str] = []
    lines.append(f"Item {r.item_id}  {r.name!r}")
    lines.append(f"  variant           : {r.variant.label} "
                 f"(context {r.variant.context} = {context_name(r.variant.context)})")
    if r.variant.origin:
        lines.append(f"  variant origin    : {r.variant.origin}")
    if r.variant.mythic_plus_keystone_level is not None:
        lines.append(f"  keystone level    : {r.variant.mythic_plus_keystone_level}")
    lines.append(f"  player level      : {r.player_level}")
    lines.append(f"  class/subclass    : {r.class_id}/{r.subclass_id}")
    lines.append(f"  inventory type    : {r.inventory_type} "
                 f"({INVTYPE_NAMES.get(r.inventory_type)})")
    lines.append(f"  base item level   : {r.base_item_level}  (ItemSparse.ItemLevel)")
    lines.append(f"  base quality      : {r.base_quality} "
                 f"({QUALITY_NAMES.get(r.base_quality)})")
    lines.append(f"  effective quality : {r.quality} ({QUALITY_NAMES.get(r.quality)})")
    lines.append(f"  required level    : {r.required_level}")

    lines.append("")
    lines.append("  bonus-list selection (ItemBonusMgr::GetBonusListsForItem)")
    lines.append(f"    trees visited   : {r.selection.trees_visited or '-'}")
    for step in r.selection.trace:
        detail = ", ".join(f"{k}={v}" for k, v in step.detail.items())
        lines.append(f"    tree {step.tree_id:>6} node {step.node_id:>6}  "
                     f"{step.action}{'  ' + detail if detail else ''}")
    lines.append(f"    selected lists  : {r.selection.bonus_list_ids or '-'}")
    lines.append(f"    level selector  : {r.selection.item_level_selector_id or '-'}")
    lines.append(f"    applied lists   : {r.applied_bonus_lists or '-'}")

    if r.bonus_trace:
        lines.append("")
        lines.append("  applied ItemBonus rows (BonusData::AddBonus)")
        for b in r.bonus_trace:
            mark = " " if b.handled else "!"
            lines.append(f"   {mark}list {b.bonus_list_id:>7}  bonus {b.bonus_id:>7}  "
                         f"type {b.type:>2} "
                         f"{BONUS_TYPE_NAMES.get(b.type, 'UNHANDLED'):<34} "
                         f"values {list(b.values)}")

    lines.append("")
    lines.append("  effective item level (Item::GetItemLevel)")
    for step in r.item_level_provenance:
        rest = ", ".join(f"{k}={v}" for k, v in step.items() if k != "step")
        lines.append(f"    {step['step']:<34} {rest}")
    lines.append(f"    => effective item level {r.effective_item_level}")

    if r.curve_evaluations:
        lines.append("")
        lines.append("  curve evaluations")
        for c in r.curve_evaluations:
            pts = " ".join(f"({x:g},{y:g})" for x, y in c.bracket)
            lines.append(f"    curve {c.curve_id} type {c.curve_type} "
                         f"mode {c.mode} x={c.x:g} -> raw {c.y!r}"
                         f"{'  [' + c.clamped + ']' if c.clamped else ''}")
            lines.append(f"      points({c.point_count}) around x: {pts}")
            lines.append(f"      consumer: {c.consumer}")

    lines.append("")
    lines.append("  stats (Item::GetItemStatValue -> Player::_ApplyItemBonuses)")
    for s in r.stats:
        if not s.final_value:
            continue
        parts = [f"alloc={s.stat_allocation}"]
        if s.rand_prop_points is not None:
            parts.append(f"randProp[{s.rand_prop_quality_column}"
                         f"[{s.rand_prop_index}]@ilvl{s.rand_prop_row}]"
                         f"={s.rand_prop_points}")
        if s.socket_cost_per_level is not None:
            parts.append(f"socketCost={s.socket_cost_per_level}"
                         f"*{s.stat_percentage_of_socket}")
        if s.combat_ratings_mult_by_ilvl is not None:
            parts.append(f"ratingMult={s.combat_ratings_mult_by_ilvl}")
        if s.stamina_mult_by_ilvl is not None:
            parts.append(f"staminaMult={s.stamina_mult_by_ilvl}")
        if s.value_before_round is not None:
            parts.append(f"pre-round={s.value_before_round!r}")
        lines.append(f"    [{s.stat_index}] {s.stat_name:<14} = "
                     f"{s.final_value:>7}   " + "  ".join(parts))
        if s.ratings:
            lines.append(f"         -> CombatRatings {', '.join(s.ratings)}")

    if any(r.sockets):
        lines.append("")
        lines.append(f"  sockets           : {r.sockets}")
    for gem in r.gems:
        lines.append(f"    socket {gem['socket_index']} gem {gem['gem_item_id']} "
                     f"{gem['name']!r} enchant {gem['enchant_id']} "
                     f"host ilvl bonus {gem.get('host_item_level_bonus', 0)}")
    if r.armor:
        lines.append(f"  armor             : {r.armor}  (ItemTemplate::GetArmor)")
    if r.weapon:
        lines.append("")
        lines.append("  weapon (ItemTemplate::GetDPS / GetDamage)")
        for k, v in r.weapon.items():
            lines.append(f"    {k:<22} {v}")
    if r.effects:
        lines.append("")
        lines.append("  item effects (ItemXItemEffect -> ItemEffect)")
        for e in r.effects:
            lines.append(f"    effect {e.item_effect_id:>7}  spell {e.spell_id:>8}  "
                         f"{e.trigger_name:<16} spec={e.chr_specialization_id} "
                         f"cd={e.cooldown_ms}ms cat={e.spell_category_id}")
            lines.append(f"        route: {e.route}")
    for enchant in r.enchants:
        lines.append("")
        lines.append(f"  enchant {enchant['enchant_id']}: {enchant['name']!r}")
        for effect in enchant["effects"]:
            amount = effect.get("resolved_amount")
            lines.append(f"    slot {effect['slot']} {effect['type_name']:<16} "
                         f"arg={effect['effect_arg']}"
                         + (f" amount={amount}" if amount is not None else "")
                         + f"  {effect['acquisition']}")
    if r.item_set:
        lines.append("")
        lines.append(f"  item set {r.item_set['item_set_id']}: {r.item_set['name']!r}")
        for s in r.item_set["spells"]:
            lines.append(f"    {s['threshold']}p -> spell {s['spell_id']:>8}  "
                         f"spec={s['chr_spec_id']} subtree={s['trait_sub_tree_id']}")
    if r.warnings:
        lines.append("")
        lines.append("  warnings")
        for w in r.warnings:
            lines.append(f"    ! {w}")
    return "\n".join(lines)


LOADOUT_ITEM_COLUMNS = (
    "item_id", "name", "variant", "item_level", "quality", "inv_type",
    "primary", "stamina", "secondary_1", "secondary_2", "armor_or_weapon",
)


def render_loadout(result: Any) -> str:
    """Human-readable per-item table plus the safe aggregates."""
    rows = [report_row(item) for item in result.items]
    out = [render_table(rows, LOADOUT_ITEM_COLUMNS), ""]
    out.append("raw stat totals (item contributions only, no character policy)")
    for name, value in sorted(result.stat_totals.items()):
        out.append(f"  {name:<22} {value}")
    if result.rating_totals:
        out.append("")
        out.append("raw combat rating totals")
        for name, value in sorted(result.rating_totals.items()):
            out.append(f"  {name:<24} {value}")
    if result.set_bonuses:
        out.append("")
        out.append("satisfied item-set thresholds (acquisition only)")
        for bonus in result.set_bonuses:
            out.append(f"  set {bonus['item_set_id']} {bonus['item_set_name']!r}: "
                       f"{bonus['equipped_count']} equipped, {bonus['threshold']}p "
                       f"-> spell {bonus['spell_id']} (spec {bonus['chr_spec_id']})")
    if result.warnings:
        out.append("")
        out.append("warnings")
        for warning in result.warnings:
            out.append(f"  ! {warning}")
    return "\n".join(out)
