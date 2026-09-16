"""Human-readable rendering for the character-stat tool."""

from __future__ import annotations

from typing import Any, Sequence

from .identity import MAX_STATS, STAT_NAMES
from gearing.report import render_csv, render_markdown, render_table


def render_rows(rows: Sequence[dict[str, Any]], columns: Sequence[str],
                style: str = "table") -> str:
    if style == "markdown":
        return render_markdown(rows, columns)
    if style == "csv":
        return render_csv(rows, columns)
    return render_table(rows, columns)


def render_character(c) -> str:
    lines: list[str] = []
    spec = f"{c.spec.name} ({c.spec.spec_id})" if c.spec else "<none>"
    lines.append(f"{c.race.name} {c.klass.name} -- {spec} -- level {c.level}")
    lines.append(f"  primary stat      : {c.routing.primary_stat_name} "
                 f"(ChrSpecialization.PrimaryStatPriority "
                 f"{c.routing.primary_stat_priority})")
    lines.append(f"  base stat source  : {c.base.stats_source}")
    lines.append("")
    lines.append("  stats (Unit::GetTotalStatValue -> int32)")
    for stat in range(MAX_STATS):
        stage = c.stat_stages[stat]
        parts = [f"create={stage.create_value}"]
        if stage.total_flat:
            parts.append(f"supplied={stage.total_flat:g}")
        if stage.total_pct != 1.0:
            parts.append(f"total_pct={stage.total_pct:g}")
        lines.append(f"    {STAT_NAMES[stat]:<10} = {stage.rounded():>8}   "
                     + "  ".join(parts))
    lines.append("")
    lines.append("  derived")
    lines.append(f"    max health      = {c.max_health:>8}   "
                 f"stamina {c.stat_stages[2].rounded()} * HpPerSta[{c.level}]"
                 f"={c.hp_per_sta:g} -> {c.health_from_stamina:g}")
    lines.append(f"    armor           = {c.armor:>8}   "
                 f"(bonus {c.bonus_armor})")
    lines.append(f"    attack power    = {c.attack_power:>8}   "
                 f"str*{c.klass.attack_power_per_strength}="
                 f"{c.attack_power_from_strength:g}  "
                 f"agi*{c.klass.attack_power_per_agility}="
                 f"{c.attack_power_from_agility:g}")
    lines.append(f"    ranged AP       = {c.ranged_attack_power:>8}   "
                 f"(level + agi) * {c.klass.ranged_attack_power_per_agility}")
    lines.append(f"    spell power     = {c.spell_power:>8}   "
                 f"from intellect {c.spell_power_from_intellect}")
    if c.armor_specialization_applied:
        a = c.armor_specialization_applied
        lines.append(f"    armour spec     = spell {a.spell_id}: "
                     f"+{a.percent:g}% {', '.join(a.stat_names)} "
                     f"({', '.join(a.armor_subclasses)})")

    lines.append("")
    lines.append("  ratings (Player::GetRatingBonusValue)")
    for conversion in c.rating_conversions:
        if not conversion["amount"]:
            continue
        curve = conversion["diminishing_curve_id"] or "-"
        extra = ""
        if "effective_percentage_with_base" in conversion:
            extra = (f"  with 5% base -> "
                     f"{conversion['effective_percentage_with_base']:.3f}%")
        lines.append(
            f"    {conversion['rating']:<24} {conversion['amount']:>8.0f} "
            f"* {conversion['per_point']:.6f} = "
            f"{conversion['linear_percent']:>8.3f}%  curve {curve!s:>6} -> "
            f"{conversion['final_percent']:>8.3f}%{extra}")

    if c.mastery:
        lines.append("")
        lines.append(f"  mastery [{c.mastery.category}]")
        lines.append(f"    ActivePlayerData::Mastery = {c.mastery_value:.4f}")
        lines.append(f"    {c.mastery.reason}")
        for spell in c.mastery.spells:
            lines.append(f"    spell {spell.spell_id} {spell.name!r} "
                         f"(MASTERY_AFFECTS_POINTS="
                         f"{spell.mastery_affects_points})")
            for effect in spell.effects:
                amount = effect.amount_at(c.mastery_value)
                marker = "*" if effect.participates_in_mastery else " "
                lines.append(
                    f"     {marker} idx{effect.effect_index} "
                    f"{effect.aura_name:<48} base={effect.base_points:g} "
                    f"coef={effect.bonus_coefficient:g} -> {amount:.4f}")

    if c.warnings:
        lines.append("")
        lines.append("  warnings")
        for warning in c.warnings:
            lines.append(f"    ! {warning}")
    lines.append("")
    lines.append("  NOTE: no talents, buffs, consumables, shapeshift forms or "
                 "aura stages are applied.")
    return "\n".join(lines)
