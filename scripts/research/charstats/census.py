"""Population counts for the character-stat surface."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ._aura_names import AURA_TYPE_NAMES
from .character import CharacterResolver
from .mastery import (
    CATEGORY_NO_MASTERY,
    CATEGORY_ORDINARY,
    CATEGORY_SCRIPTED,
    CATEGORY_UNSUPPORTED,
    build_profile,
)


def census(resolver: CharacterResolver) -> dict[str, Any]:
    identity = resolver.identity
    races = identity.races()
    classes = identity.classes()
    specs = identity.specs()
    initial = [s for s in identity.specs(include_initial=True) if s.is_initial]

    mastery_categories: Counter[str] = Counter()
    mastery_auras: Counter[str] = Counter()
    blocked_auras: Counter[str] = Counter()
    mastery_spell_roots = 0
    for spec in specs:
        profile = build_profile(spec, resolver.acquisition.mastery_spells(spec))
        mastery_categories[profile.category] += 1
        mastery_spell_roots += len(profile.spells)
        for spell in profile.spells:
            for effect in spell.effects:
                if not effect.participates_in_mastery:
                    continue
                mastery_auras[effect.aura_name] += 1
                if profile.category == CATEGORY_UNSUPPORTED:
                    blocked_auras[effect.aura_name] += 1

    spec_passives = {spec.spec_id: len(resolver.acquisition.spec_spells(spec.spec_id))
                     for spec in specs}
    armor_specs = resolver.acquisition.armor_specializations()

    primary_priorities: Counter[int] = Counter(s.primary_stat_priority for s in specs)
    routing: Counter[str] = Counter()
    for spec in specs:
        klass = identity.klass(spec.class_id)
        from .primary import routing_for
        routing[routing_for(klass, spec).primary_stat_name] += 1

    return {
        "populations": {
            "ChrRaces rows": len(races),
            "ChrClasses rows": len(classes),
            "playable specializations (excluding Initial)": len(specs),
            "per-class Initial pseudo-specs": len(initial),
            "specs with a mastery spell": sum(
                1 for s in specs if any(s.mastery_spell_ids)),
            "distinct mastery spell roots": mastery_spell_roots,
            "SpecializationSpells rows": len(resolver.tables("SpecializationSpells")),
            "baseline spec passive roots (total across specs)":
                sum(spec_passives.values()),
            "armour-specialization passives discovered": len(armor_specs),
            "SkillLineAbility rows with a race mask": sum(
                1 for r in resolver.tables("SkillLineAbility")
                if int(r["RaceMasks_0"]) or int(r["RaceMasks_1"])),
            "CombatRatings.txt levels": len(
                resolver.tables.gametable("CombatRatings").rows),
            "HpPerSta.txt levels": len(resolver.tables.gametable("HpPerSta").rows),
            "BaseMp.txt levels": len(resolver.tables.gametable("BaseMp").rows),
            "GlobalCurve rows": len(resolver.tables("GlobalCurve")),
        },
        "mastery_categories": dict(mastery_categories),
        "mastery_scaling_auras": dict(mastery_auras.most_common()),
        "mastery_blocking_auras": dict(blocked_auras.most_common()),
        "primary_stat_priority_counts": dict(sorted(primary_priorities.items())),
        "primary_stat_routing_counts": dict(routing),
        "spec_passive_counts": spec_passives,
        "unsupported": {
            "base primary stats": (
                "player_classlevelstats + player_racestats are world-database "
                "tables; neither the snapshot nor the TrinityCore repository "
                "ships their rows"),
            "racial custom spells": (
                "playercreateinfo_spell_custom is a world-database table and is "
                "only consulted when CONFIG_START_ALL_SPELLS is set"),
            "mastery specs blocked on generic semantics":
                mastery_categories.get(CATEGORY_UNSUPPORTED, 0),
            "mastery specs needing a script":
                mastery_categories.get(CATEGORY_SCRIPTED, 0),
        },
    }
