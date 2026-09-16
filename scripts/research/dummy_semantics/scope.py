"""Current-player scope: acquisition roots and authored reachability.

Roots (all source relationships, no gameplay inference):

* ``class-trait``  -- ``TraitTreeLoadout -> TraitNode -> TraitNodeXTraitNodeEntry ->
  TraitNodeEntry -> TraitDefinition.SpellID`` for every tree a current
  ``ChrSpecialization`` loads (spec attribution through ``TraitCond.SpecSetID``
  on the node / its groups, ``SpecSetMember``);
* ``spec-spell``   -- ``SpecializationSpells.SpellID`` and ``ChrSpecialization.MasterySpellID_*``;
* ``class-skill``  -- ``SkillLineAbility.Spell`` for ``SkillLine.CategoryID = 7``
  (class skill lines) whose ``ClassMask`` names a player class;
* ``current-gear`` -- ``ItemEffect`` spells of items in the gearing corpus
  (``docs/research/gearing-corpora/current-gear-corpus.json``);
* ``current-set``  -- ``ItemSetSpell`` rows of ``ItemSet``s containing those items;
* ``current-gem`` / ``current-enchant`` -- ``SpellItemEnchantment`` spells of gems
  (``ItemSparse.Gem_properties``) and enchant-scroll spells
  (``SPELL_EFFECT_ENCHANT_ITEM*`` OnUse) of items with the snapshot's newest
  ``ItemSparse.ExpansionID``.

Edges (authored, client-side facts only):

* ``SpellEffect.EffectTriggerSpell`` (every effect and aura type);
* ``SpellLearnSpell.LearnSpellID``;
* ``SPELL_AURA_OVERRIDE_ACTIONBAR_SPELLS[_TRIGGERED].MiscValue``;
* ``SPELL_AURA_OVERRIDE_SPELLS.MiscValue -> OverrideSpellData.Spells_*``;
* ``SpellAuraRestrictions.*AuraSpell`` (marker consumers -- recorded as a
  separate edge kind, they do *not* extend reachability).

Script-referenced children (``CastSpell(..., SPELL_X)``) are **not** authored
edges; they are reported separately (``script-referenced``) so the report can
say how many server-only relationships the client data does not encode.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from procs.enums import aura, effect
from procs.spells import DIFFICULTY_NONE

from . import GEAR_CORPORA
from .loaders import Bundle

SPELL_AURA_OVERRIDE_ACTIONBAR_SPELLS = aura("OVERRIDE_ACTIONBAR_SPELLS")
SPELL_AURA_OVERRIDE_ACTIONBAR_SPELLS_TRIGGERED = aura("OVERRIDE_ACTIONBAR_SPELLS_TRIGGERED")
SPELL_AURA_OVERRIDE_SPELLS = aura("OVERRIDE_SPELLS")
SPELL_EFFECT_ENCHANT_ITEM = effect("ENCHANT_ITEM")
SPELL_EFFECT_ENCHANT_ITEM_TEMPORARY = effect("ENCHANT_ITEM_TEMPORARY")
SPELL_EFFECT_ENCHANT_ITEM_PRISMATIC = effect("ENCHANT_ITEM_PRISMATIC")
ENCHANT_EFFECTS = {SPELL_EFFECT_ENCHANT_ITEM, SPELL_EFFECT_ENCHANT_ITEM_TEMPORARY, SPELL_EFFECT_ENCHANT_ITEM_PRISMATIC}
ENCHANT_SPELL_TYPES = {1, 3, 7}  # ITEM_ENCHANTMENT_TYPE_COMBAT_SPELL, EQUIP_SPELL, USE_SPELL
ITEM_EFFECT_ON_USE, ITEM_EFFECT_ON_EQUIP, ITEM_EFFECT_ON_PROC = 0, 1, 2
SKILL_CATEGORY_CLASS = 7
TRAIT_COND_GRANTED = 2  # TraitConditionType::Granted (DBCEnums.h) -- not a spec gate
CURRENT_GEAR_CORPUS = GEAR_CORPORA / "current-gear-corpus.json"
ROOT_KINDS = ("class-trait", "spec-spell", "class-skill", "current-gear", "current-set",
              "current-gem", "current-enchant")
PLAYER_CLASS_IDS = range(1, 14)


@dataclass
class Roots:
    by_spell: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))
    specs_by_spell: dict[int, set[int]] = field(default_factory=lambda: defaultdict(set))
    detail: dict[int, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    class_of_spec: dict[int, int] = field(default_factory=dict)
    spec_names: dict[int, str] = field(default_factory=dict)
    class_names: dict[int, str] = field(default_factory=dict)
    gear_items: set[int] = field(default_factory=set)
    current_expansion: int = 0
    notes: list[str] = field(default_factory=list)

    exists = None  # set by Scope: catalog.exists, so roots never name spells the snapshot lacks

    def add(self, spell: int, kind: str, specs: set[int] | None, **detail: Any) -> None:
        if not spell or (self.exists is not None and not self.exists(spell)):
            if spell:
                self.dropped_missing = getattr(self, "dropped_missing", 0) + 1
            return
        self.by_spell[spell].add(kind)
        if specs:
            self.specs_by_spell[spell].update(specs)
        self.detail[spell].append({"kind": kind, **detail})


class Scope:
    """``include_class_skills`` adds ``SkillLineAbility`` class-line spells (an
    *extended* scope: the category-7 lines also hold Mounts/Companions/pet
    families).  The default matches the brief and the proc research: class
    trees, spec spells, current gear, sets, gems, enchants."""

    def __init__(self, bundle: Bundle, include_class_skills: bool = False) -> None:
        self.b = bundle
        self.include_class_skills = include_class_skills
        self.roots = self._roots()
        self.edges = self._edges()
        self.reach, self.depth, self.parent = self._reach()
        self.specs_reach = self._spec_reach()

    # ------------------------------------------------------------------
    def _roots(self) -> Roots:
        src = self.b.source
        r = Roots()
        r.exists = self.b.catalog.exists
        specs = list(src.iter_dicts("ChrSpecialization", ("ID", "ClassID", "Name_lang", "MasterySpellID_0", "MasterySpellID_1", "Flags")))
        # current specs = player-class specs that load a talent tree (drops the 'Initial' rows)
        with_loadout = {spec for _, spec in src.project("TraitTreeLoadout", ("TraitTreeID", "ChrSpecializationID"))}
        for s in specs:
            if s["ClassID"] in PLAYER_CLASS_IDS and s["ID"] in with_loadout:
                r.class_of_spec[s["ID"]] = s["ClassID"]
                r.spec_names[s["ID"]] = s["Name_lang"]
        for c in src.iter_dicts("ChrClasses", ("ID", "Name_lang")):
            r.class_names[c["ID"]] = c["Name_lang"]
        specs_of_class: dict[int, set[int]] = defaultdict(set)
        for spec, cls in r.class_of_spec.items():
            specs_of_class[cls].add(spec)

        # -- traits ----------------------------------------------------------
        loadout_specs: dict[int, set[int]] = defaultdict(set)
        for tree, spec in src.project("TraitTreeLoadout", ("TraitTreeID", "ChrSpecializationID")):
            if spec in r.class_of_spec:
                loadout_specs[tree].add(spec)
        node_tree = {n: t for n, t in src.project("TraitNode", ("ID", "TraitTreeID"))}
        entry_def = {e: d for e, d in src.project("TraitNodeEntry", ("ID", "TraitDefinitionID"))}
        def_spell = {d: (s, o, v) for d, s, o, v in src.project(
            "TraitDefinition", ("ID", "SpellID", "OverridesSpellID", "VisibleSpellID"))}
        spec_set_members: dict[int, set[int]] = defaultdict(set)
        for spec, spec_set in src.project("SpecSetMember", ("ChrSpecializationID", "SpecSet")):
            spec_set_members[spec_set].add(spec)
        cond_spec_set = {cid: (ctype, sset) for cid, ctype, sset in src.project("TraitCond", ("ID", "CondType", "SpecSetID"))}
        node_conds: dict[int, list[int]] = defaultdict(list)
        for cond, node in src.project("TraitNodeXTraitCond", ("TraitCondID", "TraitNodeID")):
            node_conds[node].append(cond)
        group_conds: dict[int, list[int]] = defaultdict(list)
        for cond, group in src.project("TraitNodeGroupXTraitCond", ("TraitCondID", "TraitNodeGroupID")):
            group_conds[group].append(cond)
        node_groups: dict[int, list[int]] = defaultdict(list)
        for group, node in src.project("TraitNodeGroupXTraitNode", ("TraitNodeGroupID", "TraitNodeID")):
            node_groups[node].append(group)

        def node_specs(node: int, tree: int) -> set[int]:
            """Specs that can select ``node``: the intersection of its spec-set conditions,
            defaulting to every spec loading the tree."""
            allowed = set(loadout_specs[tree])
            conds = list(node_conds.get(node, []))
            for g in node_groups.get(node, []):
                conds.extend(group_conds.get(g, []))
            gated = None
            for cid in conds:
                ctype, sset = cond_spec_set.get(cid, (None, 0))
                if not sset or ctype == TRAIT_COND_GRANTED:
                    continue
                members = spec_set_members.get(sset, set())
                gated = members if gated is None else (gated | members)
            if gated is not None:
                allowed &= gated
            return allowed

        for node, entry in src.project("TraitNodeXTraitNodeEntry", ("TraitNodeID", "TraitNodeEntryID")):
            tree = node_tree.get(node)
            if tree not in loadout_specs:
                continue
            definition = entry_def.get(entry)
            spell, overrides, visible = def_spell.get(definition, (0, 0, 0))
            if not spell:
                continue
            specs_here = node_specs(node, tree)
            r.add(spell, "class-trait", specs_here, trait_tree_id=tree, trait_node_id=node,
                  trait_definition_id=definition, overrides_spell_id=overrides)

        # -- spec spells & mastery -------------------------------------------
        for spec, spell, overrides in src.project("SpecializationSpells", ("SpecID", "SpellID", "OverridesSpellID")):
            if spec in r.class_of_spec:
                r.add(spell, "spec-spell", {spec}, spec_id=spec, overrides_spell_id=overrides)
        for s in specs:
            if s["ID"] in r.class_of_spec:
                for m in (s["MasterySpellID_0"], s["MasterySpellID_1"]):
                    r.add(m, "spec-spell", {s["ID"]}, spec_id=s["ID"], mastery=True)

        # -- class skill lines (extended scope only) ---------------------------
        class_skill_lines = {row["ID"]: row["DisplayName_lang"] for row in
                             src.iter_dicts("SkillLine", ("ID", "CategoryID", "DisplayName_lang"))
                             if row["CategoryID"] == SKILL_CATEGORY_CLASS} if self.include_class_skills else {}
        # ChrClasses -> skill line: SkillRaceClassInfo (ClassMask, SkillID)
        line_classes: dict[int, set[int]] = defaultdict(set)
        for skill, class_mask in src.project("SkillRaceClassInfo", ("SkillID", "ClassMask")):
            if skill in class_skill_lines:
                for cls in PLAYER_CLASS_IDS:
                    if class_mask & (1 << (cls - 1)):
                        line_classes[skill].add(cls)
        for skill, spell, class_mask in src.project("SkillLineAbility", ("SkillLine", "Spell", "ClassMask")):
            if skill not in class_skill_lines:
                continue
            classes = set()
            for cls in PLAYER_CLASS_IDS:
                if class_mask & (1 << (cls - 1)):
                    classes.add(cls)
            if not classes:
                classes = line_classes.get(skill, set())
            if not classes:
                continue
            spec_ids = set()
            for cls in classes:
                spec_ids |= specs_of_class[cls]
            r.add(spell, "class-skill", spec_ids, skill_line=skill, skill_name=class_skill_lines[skill],
                  classes=sorted(classes))

        # -- gear ------------------------------------------------------------
        if CURRENT_GEAR_CORPUS.exists():
            corpus = json.loads(CURRENT_GEAR_CORPUS.read_text(encoding="utf-8"))
            for key in ("raid", "mythic_plus"):
                for item in corpus.get(key, {}).get("items", []):
                    r.gear_items.add(int(item["item_id"]))
        else:
            r.notes.append("gearing corpus missing: current-gear roots empty")
        effect_items: dict[int, list[int]] = defaultdict(list)
        for item, eff in src.project("ItemXItemEffect", ("ItemID", "ItemEffectID")):
            effect_items[eff].append(item)
        item_effects = {row["ID"]: row for row in src.iter_dicts(
            "ItemEffect", ("ID", "SpellID", "TriggerType", "ChrSpecializationID"))}
        for eff_id, row in item_effects.items():
            hits = [i for i in effect_items.get(eff_id, []) if i in r.gear_items]
            if hits:
                spec = {row["ChrSpecializationID"]} if row["ChrSpecializationID"] else None
                r.add(row["SpellID"], "current-gear", spec, items=sorted(hits)[:8],
                      trigger_type=row["TriggerType"], item_effect_id=eff_id)
        # sets
        item_set = {row["ID"]: row["ItemSet"] for row in src.iter_dicts("ItemSparse", ("ID", "ItemSet")) if row["ItemSet"]}
        current_sets = {item_set[i] for i in r.gear_items if i in item_set}
        for row in src.iter_dicts("ItemSetSpell", ("ID", "ChrSpecID", "SpellID", "TraitSubTreeID", "Threshold", "ItemSetID")):
            if row["ItemSetID"] in current_sets:
                spec = {row["ChrSpecID"]} if row["ChrSpecID"] else None
                r.add(row["SpellID"], "current-set", spec, item_set_id=row["ItemSetID"],
                      threshold=row["Threshold"], trait_sub_tree_id=row["TraitSubTreeID"])
        # gems & enchants of the newest expansion
        sparse = list(src.iter_dicts("ItemSparse", ("ID", "ExpansionID", "Gem_properties")))
        r.current_expansion = max(int(s["ExpansionID"]) for s in sparse)
        current_items = {s["ID"] for s in sparse if int(s["ExpansionID"]) == r.current_expansion}
        gem_enchant = {g["ID"]: g["Enchant_ID"] for g in src.iter_dicts("GemProperties", ("ID", "Enchant_ID"))}
        enchant_rows = {row["ID"]: row for row in src.iter_dicts(
            "SpellItemEnchantment", ("ID", "Effect_0", "Effect_1", "Effect_2", "EffectArg_0", "EffectArg_1", "EffectArg_2"))}

        def enchant_spells(enchant_id: int) -> list[tuple[int, int]]:
            row = enchant_rows.get(enchant_id)
            if not row:
                return []
            return [(row[f"Effect_{k}"], row[f"EffectArg_{k}"]) for k in range(3)
                    if row[f"Effect_{k}"] in ENCHANT_SPELL_TYPES and row[f"EffectArg_{k}"]]

        for s in sparse:
            if s["ID"] in current_items and s["Gem_properties"]:
                ench = gem_enchant.get(s["Gem_properties"])
                for etype, spell in enchant_spells(ench or 0):
                    r.add(spell, "current-gem", None, item=s["ID"], enchant_id=ench, enchant_effect_type=etype)
        # enchant scrolls: OnUse spell with ENCHANT_ITEM* effect -> MiscValue_0 = enchant id
        cat = self.b.catalog
        for eff_id, row in item_effects.items():
            if row["TriggerType"] != ITEM_EFFECT_ON_USE:
                continue
            items = [i for i in effect_items.get(eff_id, []) if i in current_items]
            if not items:
                continue
            info = cat.get(row["SpellID"])
            if info is None:
                continue
            for e in info.effects:
                if e.effect in ENCHANT_EFFECTS and e.misc0:
                    for etype, spell in enchant_spells(e.misc0):
                        r.add(spell, "current-enchant", None, items=sorted(items)[:4], enchant_id=e.misc0,
                              enchant_effect_type=etype, scroll_spell=row["SpellID"])
        return r

    # ------------------------------------------------------------------
    def _edges(self) -> dict[int, list[tuple[int, str]]]:
        cat = self.b.catalog
        src = self.b.source
        out: dict[int, list[tuple[int, str]]] = defaultdict(list)
        override_data = {row["ID"]: [row[f"Spells_{i}"] for i in range(10) if row[f"Spells_{i}"]]
                         for row in src.iter_dicts("OverrideSpellData", ("ID",) + tuple(f"Spells_{i}" for i in range(10)))}
        for (spell, diff), effects in cat.effects.items():
            if diff != DIFFICULTY_NONE:
                continue
            for eff in effects.values():
                if eff.trigger_spell:
                    out[spell].append((eff.trigger_spell, f"trigger:{eff.effect}:{eff.aura}"))
                if eff.is_aura and eff.aura in (SPELL_AURA_OVERRIDE_ACTIONBAR_SPELLS, SPELL_AURA_OVERRIDE_ACTIONBAR_SPELLS_TRIGGERED) and eff.misc0:
                    out[spell].append((eff.misc0, "override-actionbar"))
                if eff.is_aura and eff.aura == SPELL_AURA_OVERRIDE_SPELLS:
                    for s in override_data.get(eff.misc0, []):
                        out[spell].append((s, "override-spells"))
        for spell, learn in src.project("SpellLearnSpell", ("SpellID", "LearnSpellID")):
            if learn:
                out[spell].append((learn, "learn-spell"))
        return dict(out)

    def _reach(self) -> tuple[set[int], dict[int, int], dict[int, tuple[int, str] | None]]:
        depth: dict[int, int] = {}
        parent: dict[int, tuple[int, str] | None] = {}
        frontier = [(s, 0) for s in sorted(self.roots.by_spell)]
        for s, _ in frontier:
            depth[s] = 0
            parent[s] = None
        i = 0
        while i < len(frontier):
            node, d = frontier[i]
            i += 1
            for child, kind in self.edges.get(node, []):
                if child not in depth and self.b.catalog.exists(child):
                    depth[child] = d + 1
                    parent[child] = (node, kind)
                    frontier.append((child, d + 1))
        return set(depth), depth, parent

    def _spec_reach(self) -> dict[int, set[int]]:
        """spec -> reachable spells (roots attributed to the spec, plus their authored descendants;
        unattributed roots (gems, enchants, class-wide gear) count for every spec)."""
        all_specs = set(self.roots.class_of_spec)
        spec_roots: dict[int, set[int]] = defaultdict(set)
        for spell, kinds in self.roots.by_spell.items():
            specs = self.roots.specs_by_spell.get(spell) or all_specs
            for s in specs:
                spec_roots[s].add(spell)
        out: dict[int, set[int]] = {}
        for spec, roots in spec_roots.items():
            seen = set(roots)
            stack = list(roots)
            while stack:
                node = stack.pop()
                for child, _ in self.edges.get(node, []):
                    if child not in seen and child in self.reach:
                        seen.add(child)
                        stack.append(child)
            out[spec] = seen
        return out

    # ------------------------------------------------------------------
    def chain(self, spell_id: int) -> list[dict[str, Any]]:
        """Root -> ... -> spell path (one witness path, BFS parent)."""
        out = []
        cur: int | None = spell_id
        while cur is not None:
            p = self.parent.get(cur)
            out.append({"spell": cur, "name": self.b.name(cur), "via": p[1] if p else None,
                        "root_kinds": sorted(self.roots.by_spell.get(cur, ()))})
            cur = p[0] if p else None
        return list(reversed(out))

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = defaultdict(int)
        for s, ks in self.roots.by_spell.items():
            for k in ks:
                kinds[k] += 1
        return {
            "roots": len(self.roots.by_spell), "root_kinds": dict(sorted(kinds.items())),
            "reachable": len(self.reach), "max_depth": max(self.depth.values()) if self.depth else 0,
            "depth_histogram": dict(sorted(defaultdict(int, {}).items())) or {
                str(d): sum(1 for v in self.depth.values() if v == d) for d in range(0, max(self.depth.values()) + 1)},
            "gear_items": len(self.roots.gear_items), "current_expansion_id": self.roots.current_expansion,
            "specs": len(self.roots.class_of_spec),
            "edge_kinds": dict(sorted(defaultdict(int, {}).items())) or _edge_kind_counts(self.edges),
            "notes": self.roots.notes,
            "root_spells_dropped_missing_from_snapshot": getattr(self.roots, "dropped_missing", 0),
        }


def _edge_kind_counts(edges: dict[int, list[tuple[int, str]]]) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for lst in edges.values():
        for _, kind in lst:
            c[kind.split(":")[0]] += 1
    return dict(sorted(c.items()))
