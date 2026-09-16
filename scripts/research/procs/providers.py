"""Provider carriers: how a proc-capable spell reaches a player, and who owns its state.

Acquisition roots are reused from the gearing archaeology (``ItemEffect``
trigger routes, ``SpellItemEnchantment`` effect routes, ``ItemSetSpell``);
trait and specialization roots are read from the talent tables.  This module
never decides executability -- only *which consumer* turns a root into proc
behaviour and *which object* owns the mutable proc state:

======================  ==========================================  =============================
root                    consumer                                    mutable proc state owner
======================  ==========================================  =============================
ItemEffect OnEquip      Player::ApplyItemEquipSpell -> CastSpell    the passive Aura (cast item)
ItemEffect OnUse        Player::CastItemUseSpell (spell may apply   the applied Aura, if any
                        an aura that is itself a provider)
ItemEffect OnProc       Player::CastItemCombatSpell (legacy)        none (stateless roll)
Enchant EquipSpell      Player::ApplyEnchantment -> CastSpell       the passive Aura (cast item)
Enchant CombatSpell     Player::CastItemCombatSpell                 none (stateless, two rolls)
ItemSetSpell            Player::ApplyEquipSpell                     the passive Aura (no cast item)
trait / spec spell      Player::LearnSpell -> passive auto-cast     the passive Aura
spell-applied aura      the applying spell's APPLY_AURA effect      the Aura on its holder
======================  ==========================================  =============================
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from gearing.effects import (
    TRIGGER_ON_EQUIP,
    TRIGGER_ON_PROC,
    TRIGGER_ON_USE,
    classify_effect,
)
from gearing.enchants import (
    ENCHANT_COMBAT_SPELL,
    ENCHANT_EQUIP_SPELL,
    ENCHANT_USE_SPELL,
    EnchantEngine,
)
from gearing.items import ItemStore

from . import SourceError, UnsupportedSource
from ._trinity_names import SPELL_TARGET_NAMES
from .chance import enchant_combat_spell_chance, item_on_proc_chance
from .definition import ProcDefinitions
from .source import ROOT, Source
from .spells import DIFFICULTY_NONE

CURRENT_GEAR_CORPUS = ROOT / "docs" / "research" / "gearing-corpora" / "current-gear-corpus.json"

TARGET_UNIT_CASTER = 1


@dataclass
class Origins:
    item_effects: list[dict[str, Any]] = field(default_factory=list)
    enchants: list[dict[str, Any]] = field(default_factory=list)
    gems: list[int] = field(default_factory=list)
    set_bonuses: list[dict[str, Any]] = field(default_factory=list)
    current_class_trait: list[dict[str, Any]] = field(default_factory=list)
    any_trait: bool = False
    spec_spells: list[dict[str, Any]] = field(default_factory=list)
    mastery_of_specs: list[int] = field(default_factory=list)
    triggered_by: list[dict[str, Any]] = field(default_factory=list)
    current_gear_items: list[int] = field(default_factory=list)

    def kinds(self) -> list[str]:
        out = []
        if any(e["trigger_type"] == TRIGGER_ON_EQUIP for e in self.item_effects):
            out.append("item-equip")
        if any(e["trigger_type"] == TRIGGER_ON_USE for e in self.item_effects):
            out.append("item-use")
        if any(e["trigger_type"] == TRIGGER_ON_PROC for e in self.item_effects):
            out.append("item-onproc-legacy")
        if any(e["effect_type"] == ENCHANT_EQUIP_SPELL for e in self.enchants):
            out.append("enchant-equip")
        if any(e["effect_type"] == ENCHANT_COMBAT_SPELL for e in self.enchants):
            out.append("enchant-combat")
        if any(e["effect_type"] == ENCHANT_USE_SPELL for e in self.enchants):
            out.append("enchant-use")
        if self.gems:
            out.append("gem")
        if self.set_bonuses:
            out.append("set-bonus")
        if self.current_class_trait:
            out.append("class-trait")
        elif self.any_trait:
            out.append("other-trait")
        if self.spec_spells or self.mastery_of_specs:
            out.append("spec-spell")
        if self.triggered_by:
            out.append("triggered-by-spell")
        if self.current_gear_items:
            out.append("current-gear")
        return out

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["kinds"] = self.kinds()
        return d


class OriginIndex:
    """Reverse index SpellID -> acquisition roots (source relationships only)."""

    def __init__(self, source: Source, defs: ProcDefinitions) -> None:
        self.source = source
        self.defs = defs
        tables = source.tables
        self.items = ItemStore(tables)
        self.enchants = EnchantEngine(tables, curves=None)  # describe() needs no curves
        self._by_spell: dict[int, Origins] = defaultdict(Origins)

        x_effects: dict[int, list[int]] = defaultdict(list)
        for item_id, effect_id in source.project("ItemXItemEffect", ("ItemID", "ItemEffectID")):
            x_effects[effect_id].append(item_id)
        self.effect_items = x_effects
        for row in source.full("ItemEffect"):
            root = classify_effect(row)
            self._by_spell[root.spell_id].item_effects.append({
                "item_effect_id": root.item_effect_id, "trigger_type": root.trigger_type,
                "trigger_name": root.trigger_name, "route": root.route,
                "items": sorted(x_effects.get(root.item_effect_id, []))[:20],
                "item_count": len(x_effects.get(root.item_effect_id, [])),
                "chr_specialization_id": root.chr_specialization_id,
                "cooldown_ms": root.cooldown_ms, "category_cooldown_ms": root.category_cooldown_ms,
                "charges": root.charges,
            })
        self.enchant_rows = source.full("SpellItemEnchantment")
        gem_by_enchant = defaultdict(list)
        for gem_id, enchant_id in source.project("GemProperties", ("ID", "Enchant_ID")):
            gem_by_enchant[enchant_id].append(gem_id)
        for row in self.enchant_rows:
            for slot in range(3):
                etype = int(row[f"Effect_{slot}"])
                if etype in (ENCHANT_COMBAT_SPELL, ENCHANT_EQUIP_SPELL, ENCHANT_USE_SPELL):
                    spell = int(row[f"EffectArg_{slot}"])
                    o = self._by_spell[spell]
                    o.enchants.append({"enchant_id": int(row["ID"]), "slot": slot, "effect_type": etype,
                                       "effect_points_min": int(row[f"EffectPointsMin_{slot}"])})
                    o.gems.extend(gem_by_enchant.get(int(row["ID"]), []))
        for row in source.full("ItemSetSpell"):
            self._by_spell[int(row["SpellID"])].set_bonuses.append({
                "item_set_id": int(row["ItemSetID"]), "threshold": int(row["Threshold"]),
                "chr_spec_id": int(row["ChrSpecID"]), "trait_sub_tree_id": int(row["TraitSubTreeID"])})

        # current class/spec talent trees: TraitTreeLoadout -> TraitNode -> entries -> definitions
        loadout_trees = defaultdict(set)
        for tree, spec in source.project("TraitTreeLoadout", ("TraitTreeID", "ChrSpecializationID")):
            loadout_trees[tree].add(spec)
        node_tree = {n: t for n, t in source.project("TraitNode", ("ID", "TraitTreeID"))}
        entry_def = {e: d for e, d in source.project("TraitNodeEntry", ("ID", "TraitDefinitionID"))}
        def_spell = {d: s for d, s in source.project("TraitDefinition", ("ID", "SpellID"))}
        for spell in def_spell.values():
            if spell:
                self._by_spell[spell].any_trait = True
        seen = set()
        for node, entry in source.project("TraitNodeXTraitNodeEntry", ("TraitNodeID", "TraitNodeEntryID")):
            tree = node_tree.get(node)
            if tree not in loadout_trees:
                continue
            definition = entry_def.get(entry)
            spell = def_spell.get(definition)
            if not spell or (spell, tree) in seen:
                continue
            seen.add((spell, tree))
            self._by_spell[spell].current_class_trait.append({
                "trait_tree_id": tree, "trait_node_id": node, "trait_definition_id": definition,
                "loadout_specs": sorted(loadout_trees[tree])})

        spec_class = {s: c for s, c in source.project("ChrSpecialization", ("ID", "ClassID"))}
        for spec, spell in source.project("SpecializationSpells", ("SpecID", "SpellID")):
            if spec_class.get(spec):
                self._by_spell[spell].spec_spells.append({"spec_id": spec, "class_id": spec_class[spec]})
        for spec, m0, m1 in source.project("ChrSpecialization", ("ID", "MasterySpellID_0", "MasterySpellID_1")):
            for m in (m0, m1):
                if m:
                    self._by_spell[m].mastery_of_specs.append(spec)

        cat = defs.catalog
        for (spell, diff), effects in cat.effects.items():
            if diff != DIFFICULTY_NONE:
                continue
            for eff in effects.values():
                if eff.trigger_spell:
                    self._by_spell[eff.trigger_spell].triggered_by.append({
                        "spell_id": spell, "effect": eff.index, "effect_type": eff.effect,
                        "aura": eff.aura})

        self.current_gear: set[int] = set()
        if CURRENT_GEAR_CORPUS.exists():
            corpus = json.loads(CURRENT_GEAR_CORPUS.read_text(encoding="utf-8"))
            for key in ("raid", "mythic_plus"):
                for item in corpus.get(key, {}).get("items", []):
                    self.current_gear.add(int(item["item_id"]))
        for origins in self._by_spell.values():
            hits = {i for eff in origins.item_effects
                    for i in x_effects.get(eff["item_effect_id"], []) if i in self.current_gear}
            origins.current_gear_items = sorted(hits)

    def origins(self, spell_id: int) -> Origins:
        return self._by_spell.get(spell_id, Origins())

    # ------------------------------------------------------------------
    def lifecycle(self, spell_id: int) -> dict[str, Any]:
        """Source-derived lifetime/holder classification for a provider aura."""
        info = self.defs.catalog.get(spell_id)
        if info is None:
            raise SourceError(f"no spell {spell_id}")
        origins = self.origins(spell_id)
        aura_targets = sorted({e.target_a for e in info.effects if e.is_aura})
        target_names = [SPELL_TARGET_NAMES.get(t, str(t)) if t else "0 (none)" for t in aura_targets]
        self_only = bool(aura_targets) and all(t in (0, TARGET_UNIT_CASTER) for t in aura_targets)
        kinds = origins.kinds()
        if info.is_passive:
            lifecycle = "passive"
        elif info.duration_ms is None or info.duration_ms < 0:
            lifecycle = "active-unlimited"
        else:
            lifecycle = "active-timed"
        holder = "self (implicit TARGET_UNIT_CASTER)" if self_only else (
            "explicit/area target (see implicit targets)" if aura_targets else "no aura effect")
        owner = "Aura object on the holder (charges, proc cooldown, RPPM times shared by all applications)"
        if kinds and set(kinds) <= {"item-onproc-legacy", "enchant-combat", "current-gear", "gem"}:
            owner = "none: the legacy item/enchant path keeps no proc state"
        bindings = []
        if {"item-equip", "enchant-equip", "gem", "set-bonus"} & set(kinds):
            bindings.append("equip-bound: applied by ApplyItemEquipSpell/ApplyEnchantment/ApplyEquipSpell, removed on unequip")
        if {"class-trait", "other-trait", "spec-spell"} & set(kinds):
            bindings.append("learned: passive auto-cast while the spell is known")
        if "triggered-by-spell" in kinds:
            bindings.append("applied by another spell's effect (lifetime = that aura's duration)")
        if "item-use" in kinds:
            bindings.append("applied by an on-use item spell")
        return {"lifecycle": lifecycle, "bindings": bindings, "duration_ms": info.duration_ms,
                "aura_implicit_targets": target_names, "holder": holder,
                "state_owner": owner, "origins": kinds}

    # ------------------------------------------------------------------
    def describe_item(self, item_id: int) -> dict[str, Any]:
        item = self.items.get(item_id)
        overlay = self.defs.overlay
        out: dict[str, Any] = {
            "item_id": item_id, "name": item.name, "inventory_type": item.inventory_type,
            "class_id": item.class_id, "delay_ms": item.delay,
            "legacy_flag": item.is_legacy,
            "item_template_addon_spell_ppm": overlay.item_spell_ppm.get(item_id) if overlay else None,
            "effects": [],
        }
        for row in item.effects:
            root = classify_effect(row)
            entry: dict[str, Any] = root.to_dict()
            if item.is_legacy and root.trigger_type == TRIGGER_ON_EQUIP:
                entry["note"] = "ITEM_FLAG_LEGACY: ApplyItemEquipSpell skips this effect"
            if root.trigger_type in (TRIGGER_ON_EQUIP, TRIGGER_ON_USE):
                d = self.defs.get(root.spell_id)
                entry["proc_provider"] = d.to_dict() if d and d.info.proc_flags else None
                if d and root.trigger_type == TRIGGER_ON_USE:
                    entry["applies_auras_with_proc_data"] = [
                        e.index for e in d.info.effects if e.is_aura and d.info.proc_flags]
            elif root.trigger_type == TRIGGER_ON_PROC:
                spell = self.defs.catalog.get(root.spell_id)
                ppm = entry.get("item_template_addon_spell_ppm") or (overlay.item_spell_ppm.get(item_id, 0.0) if overlay else 0.0)
                entry["legacy_item_proc"] = {
                    "consumer": "Player::CastItemCombatSpell",
                    "triggered_spell": root.spell_id,
                    "triggered_spell_exists": spell is not None,
                    "requires_event": "spell/melee DamageInfo with NORMAL|CRITICAL|ABSORB; victim alive and not self",
                    "chance_source": ("item_template_addon.SpellPPMChance (classic PPM on attack speed)" if ppm
                                      else "triggered spell SpellAuraOptions.ProcChance (>100 = GetWeaponProcChance)"),
                    "triggered_spell_proc_chance": spell.proc_chance if spell else None,
                    "cast_target": "damage victim",
                    "state": "none (no ICD, no charges, no RPPM)",
                }
                if spell is not None and not ppm and spell.proc_chance <= 100:
                    entry["legacy_item_proc"]["chance"] = item_on_proc_chance(
                        spell.proc_chance, 0.0, 0, None).to_dict()
            out["effects"].append(entry)
        return out

    def describe_enchant(self, enchant_id: int) -> dict[str, Any]:
        enchant = self.enchants.describe(enchant_id)
        overlay = self.defs.overlay
        proc_data = overlay.enchant_proc.get(enchant_id) if overlay else None
        out = {"enchant_id": enchant_id, "name": enchant.name,
               "spell_enchant_proc_data": proc_data, "effects": []}
        for eff in enchant.effects:
            entry = {"slot": eff.slot, "type": eff.type, "type_name": eff.type_name,
                     "effect_arg": eff.effect_arg, "effect_points_min": eff.effect_points_min,
                     "acquisition": eff.acquisition}
            if eff.type == ENCHANT_EQUIP_SPELL:
                d = self.defs.get(eff.effect_arg)
                entry["proc_provider"] = d.to_dict() if d and d.info.proc_flags else None
            elif eff.type == ENCHANT_COMBAT_SPELL:
                spell = self.defs.catalog.get(eff.effect_arg)
                if proc_data and proc_data.get("ProcsPerMinute"):
                    chance = {"unknown": "classic PPM on the host item's ItemDelay (item instance)"}
                else:
                    try:
                        chance = enchant_combat_spell_chance(eff.effect_points_min, proc_data, 0, None).to_dict()
                    except UnsupportedSource as exc:
                        chance = {"unknown": str(exc)}
                entry["enchant_combat_proc"] = {
                    "consumer": "Player::CastItemCombatSpell (item combat enchantments)",
                    "triggered_spell": eff.effect_arg,
                    "triggered_spell_exists": spell is not None,
                    "hit_requirement": (f"spell_enchant_proc_data.HitMask 0x{proc_data['HitMask']:X}"
                                        if proc_data and proc_data.get("HitMask")
                                        else "NORMAL|CRITICAL|ABSORB"),
                    "white_hit_only": bool(proc_data and proc_data.get("AttributesMask", 0) & 1),
                    "chance": chance,
                    "chance_source": ("spell_enchant_proc_data.ProcsPerMinute on the item's own delay"
                                      if proc_data and proc_data.get("ProcsPerMinute")
                                      else "EffectPointsMin (0 = GetWeaponProcChance), overridden by spell_enchant_proc_data.Chance"),
                    "rng": "two independent roll_chance draws, each able to cast (see docs, section 8)",
                    "cast_target": "self if SpellInfo::IsPositive() else victim (IsPositive not ported)",
                    "state": "none",
                }
            out["effects"].append(entry)
        return out
