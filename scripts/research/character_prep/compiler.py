"""Fixture compiler: neutral fixture -> prepared-Player state, fail closed.

Joins the existing research instead of duplicating it:

* ``gearing.loadout`` resolves every equipped item (item level, stats, ratings,
  gems, enchants, weapon, sets, spell roots);
* ``charstats.character`` turns base stats + gear contributions into primary /
  derived stats, mastery and the armour-specialization gate (which this module
  closes by supplying the equipped armour subclasses across the eight slots,
  charstats report blocker 5);
* ``charstats.acquisition`` supplies spec spells, mastery spells, racials;
* the trait tables supply the selected trait spells, validated the way
  ``TraitMgr::IsValidEntry`` / ``ValidateConfig`` validate them;
* ``procs`` census + ``SpellAuraOptions.ProcTypeMask`` flag proc providers;
* ``dummy_semantics`` marker corpus classifies passive Dummy auras;
* Track A / Track B contribute through lazily imported hooks and degrade to
  ``unresolved`` when absent.

Every derived value carries ``evidence_class``, a provenance chain and the
paths it was derived from.  Nothing is defaulted: base stats above level 80 are
reported as a first-class gap, never interpolated, even though the pinned
Trinity copies the previous level's row (``ObjectMgr::LoadPlayerInfo``,
``src/server/game/Globals/ObjectMgr.cpp:4349-4356``).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from . import (
    CORPORA,
    ROOT,
    SNAPSHOT_BUILD,
    TDB_RELEASE,
    TRINITY_COMMIT,
    WORLD_DB_CORPORA,
    SourceError,
)
from .fixture import (
    ARMOR_SPECIALIZATION_SLOTS,
    EQUIPMENT_SLOTS,
    SLOT_RULES,
    Fixture,
    FixtureError,
    canonical_json,
)

from charstats import CharacterSourceError, MissingBaseStats
from charstats.basestats import BaseStatTable
from charstats.character import CharacterResolver, Contributions
from charstats.identity import MAX_STATS, STAT_NAMES
from charstats.primary import ITEM_MOD_TO_STATS
from gearing.enums import (
    BONUS_ITEM_LIMIT_CATEGORY,
    BONUS_REQUIRED_LEVEL_CURVE,
    BONUS_SCALING_STAT_DISTRIBUTION_FIXED,
    INVTYPE_NAMES,
    MOD_NAMES,
)
from gearing.loadout import Loadout, LoadoutEntry, resolve_loadout
from gearing.resolver import GearResolver
from gearing.tables import DEFAULT_TABLES, Tables
from procs.enums import attr, effect
from procs.source import Source

MARKERS_CORPUS = ROOT / "docs" / "research" / "dummy-corpora" / "markers.json"
PROC_CENSUS = ROOT / "docs" / "research" / "procs-corpora" / "census.json"
DEFAULT_WORLD_DB_CORPUS = WORLD_DB_CORPORA / "player-base-stats.json"
FIXTURES_DIR = CORPORA / "fixtures"

SPELL_ATTR0_PASSIVE = attr("SPELL_ATTR0_PASSIVE")            # (0, 0x40)
SPELL_ATTR2_ALLOW_NOT_SHAPESHIFTED = (2, 0x00080000)          # SharedDefines.h:530
SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES = (8, 0x00100000)     # charstats.acquisition
SPELL_EFFECT_DUAL_WIELD = effect("DUAL_WIELD")               # 40
SPELL_EFFECT_TITAN_GRIP = effect("TITAN_GRIP")               # 155
SPELL_EFFECTS_GRANT_SKILL = (effect("SKILL_STEP"), effect("SKILL"))
#: ItemTemplate::GetSkill weapon table (ItemTemplate.cpp:99-106; SharedDefines.h SKILL_* ids); 0 = no skill
ITEM_WEAPON_SKILLS = (44, 172, 45, 46, 54, 160, 229, 43, 55, 2152, 136, 0, 0, 473, 0, 173, 0, 0, 226, 228, 356)
SPELL_EFFECTS_ENCHANT_ITEM = (effect("ENCHANT_ITEM"), effect("ENCHANT_ITEM_TEMPORARY"), effect("ENCHANT_ITEM_PRISMATIC"))  # 53, 54, 156
#: AuraStateType values Unit::Update derives from health (Unit.cpp:474-483) and whether each holds at full health
#: SpellEffectInfo::IsAura(): APPLY_AURA / PERSISTENT_AREA_AURA / APPLY_AREA_AURA_* / APPLY_AURA_ON_PET effects
SPELL_EFFECTS_APPLY_AURA = tuple(effect(n) for n in ("APPLY_AURA", "PERSISTENT_AREA_AURA", "APPLY_AREA_AURA_PARTY",
                                                     "APPLY_AREA_AURA_RAID", "APPLY_AREA_AURA_PET", "APPLY_AREA_AURA_FRIEND",
                                                     "APPLY_AREA_AURA_ENEMY", "APPLY_AREA_AURA_OWNER", "APPLY_AURA_ON_PET",
                                                     "APPLY_AREA_AURA_SUMMONS", "APPLY_AREA_AURA_PARTY_NONRANDOM"))
HEALTH_AURA_STATES = {2: False, 6: False, 13: False, 21: True, 23: True, 24: True, 25: False}
TRAIT_COND_AVAILABLE = 0      # TraitConditionType (DBCEnums.h:2840-2848)
TRAIT_COND_VISIBLE = 1
TRAIT_COND_GRANTED = 2
TRAIT_COND_RANKS_ALLOWED = 5
TRAIT_COND_FLAG_IS_GATE = 0x1        # TraitCondFlags (DBCEnums.h:2830-2836)
TRAIT_COND_FLAG_IS_SUFFICIENT = 0x4
TRAIT_CURRENCY_TRAIT_SOURCED = 2     # TraitCurrencyType (DBCEnums.h:2858-2864)
TRAIT_NODE_SELECTION = 2      # TraitNodeType::Selection (DBCEnums.h:2899)
TRAIT_NODE_SUBTREE_SELECTION = 3
TRAIT_EDGE_SUFFICIENT = 2     # TraitEdgeType::SufficientForAvailability (DBCEnums.h:2866)
TRAIT_EDGE_REQUIRED = 3
SKILL_CATEGORY_WEAPON = 6     # SkillCategory (SharedDefines.h:6369-6377)
SKILL_CATEGORY_CLASS = 7
SKILL_CATEGORY_ARMOR = 8
SKILL_CATEGORY_LANGUAGES = 10
SKILL_RUNEFORGING = 960       # SharedDefines.h:6011
SKILL_FLAG_ALWAYS_MAX_VALUE = 0x10   # DBCEnums.h:2379
ACQUIRE_AUTOMATIC_SKILL_RANK = 1     # SkillLineAbilityAcquireMethod (DBCEnums.h:2360-2367)
ACQUIRE_AUTOMATIC_CHAR_LEVEL = 2
ACQUIRE_LEARNED_OR_AUTOMATIC = 4
MAX_CLASSES = 16              # SharedDefines.h:174; loop bound of TraitMgr.cpp:274 (i < MAX_CLASSES)
#: Trinity's RaceMask::GetRaceBit (src/server/game/Miscellaneous/RaceMask.h:97-146); ChrRaces.PlayableRaceBit must agree
TRINITY_RACE_BITS = {**{r: r - 1 for r in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 22, 24, 25, 26, 27, 28, 29, 30, 31, 32)},
                     34: 11, 35: 12, 36: 13, 37: 14, 52: 16, 70: 15, 84: 17, 85: 18, 86: 20, 91: 19}
MAX_PVP_TALENT_SLOTS = 4      # DBCEnums.h:2795
GENERIC_MASTERY_SPELL = 114585   # "Mastery", SPELL_AURA_MASTERY BasePoints 8, default skill 183 (agent F)
PLAYER_CLASS_IDS = range(1, 14)
POWER_TYPE_FLAG_SET_TO_MAX_ON_LEVEL_UP = 0x1000     # DBCEnums.h:2305
POWER_TYPE_FLAG_SET_TO_MAX_ON_INITIAL_LOGIN = 0x2000  # DBCEnums.h:2306
POWER_NAMES = {0: "Mana", 1: "Rage", 2: "Focus", 3: "Energy", 4: "ComboPoints", 5: "Runes", 6: "RunicPower",
               7: "SoulShards", 8: "LunarPower", 9: "HolyPower", 10: "AlternatePower", 11: "Maelstrom", 12: "Chi",
               13: "Insanity", 16: "ArcaneCharges", 17: "Fury", 18: "Pain", 19: "Essence", 23: "AlternateQuest",
               24: "AlternateEncounter", 25: "AlternateMount"}
#: ``Player::InitStatsForLevel`` power values (Player.cpp:2487-2493).  Powers not named here are untouched by
#: InitStatsForLevel; ``Player::Create`` then fills every PowerType flagged SetToMaxOnInitialLogIn (Player.cpp:500-502),
#: ``Player::GiveLevel`` every PowerType flagged SetToMaxOnLevelUp (Player.cpp:2254-2257), ``Player::LoadFromDB``
#: restores saved values clamped to max (Player.cpp:18709-18723) and zeroes lunar power (Player.cpp:18725).
INIT_STATS_FOR_LEVEL_POWER = {0: ("full", 2488), 3: ("full", 2489), 1: ("clamped to max (filled only if above max)", 2490),
                              2: ("full", 2492), 6: ("0", 2493)}
#: ItemTemplate flags read by Player::CanUseItem / CanEquipItem (Entities/Item/ItemTemplate.h:197-281): (Flags_n, mask)
ITEM_FLAG_UNIQUE_EQUIPPABLE = (0, 0x00080000)              # ItemTemplate.h:209
ITEM_FLAG2_FACTION_HORDE = (1, 0x00000001)                 # ItemTemplate.h:226
ITEM_FLAG2_FACTION_ALLIANCE = (1, 0x00000002)              # ItemTemplate.h:227
ITEM_FLAG2_INTERNAL_ITEM = (1, 0x00000800)                 # ItemTemplate.h:237
ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD = (2, 0x00080000)       # ItemTemplate.h:281
INVTYPE_WEAPON, INVTYPE_SHIELD, INVTYPE_RANGED, INVTYPE_2HWEAPON = 13, 14, 15, 17
INVTYPE_WEAPONOFFHAND, INVTYPE_RANGEDRIGHT = 22, 26
ITEM_SUBCLASS_WEAPON_POLEARM, ITEM_SUBCLASS_WEAPON_WAND = 6, 19
SPELL_AURA_MOD_MAX_CHARGES = 411      # SpellAuraDefines.h:498
CLASS_HOOK = ("controlled_units.ownership", "CHARACTER_PREP_HOOK")
WEAPON_HOOK = ("weapon_combat.weapon", "CHARACTER_PREP_HOOK")
MOD_BY_NAME = {v: k for k, v in MOD_NAMES.items()}
COMBINED_STAT_NAMES = {"Agi|Str|Int": 71, "Agi|Str": 72, "Agi|Int": 73, "Str|Int": 74}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def node(value: Any, evidence: str, provenance: list[dict[str, Any]], derived_from: list[str] | None = None,
         **extra: Any) -> dict[str, Any]:
    out = {"value": value, "evidence_class": evidence, "provenance": provenance,
           "derived_from": sorted(derived_from or [])}
    out.update(extra)
    return out


def unresolved(item: str, reason: str, reopen: str, **extra: Any) -> dict[str, Any]:
    out = {"item": item, "reason": reason, "reopen_condition": reopen}
    out.update(extra)
    return out


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 lookup; raises KeyError with the failing token."""
    if pointer in ("", "/"):
        return doc
    cur = doc
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError):
                raise KeyError(token) from None
        elif isinstance(cur, dict):
            if token not in cur:
                raise KeyError(token)
            cur = cur[token]
        else:
            raise KeyError(token)
    return cur


def _shown_path(path: Path | None) -> str | None:
    """Repository-relative path, or the bare file name: a committed corpus never embeds a host path."""
    if path is None:
        return None
    resolved = Path(path).resolve()
    return str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else resolved.name


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextmanager
def armor_specializations_as(acquisition: Any, specs: list[Any]) -> Iterator[None]:
    """Scope ``acquisition.armor_specializations`` to ``specs`` for one ``CharacterResolver.resolve`` call.

    charstats is not modified: an instance attribute shadows the method and is removed afterwards.
    """
    acquisition.armor_specializations = lambda spec_id=None: list(specs)
    try:
        yield
    finally:
        del acquisition.armor_specializations


def discover_armor_specializations(acquisition: Any, spec_id: int, spell_ids: set[int]) -> list[Any]:
    """charstats' discovery rule applied to an arbitrary spell set (its ``_spec_spells`` index swapped for one call)."""
    saved = acquisition._spec_spells
    try:
        acquisition._spec_spells = {spec_id: [{"SpellID": str(s)} for s in sorted(spell_ids)]}
        return acquisition.armor_specializations(spec_id)
    finally:
        acquisition._spec_spells = saved


# ---------------------------------------------------------------------------
# base stats
# ---------------------------------------------------------------------------

def base_stat_table_from_world_corpus(doc: dict[str, Any], path: Path | None = None) -> BaseStatTable:
    """``world-db-corpora/player-base-stats.json`` -> ``charstats.BaseStatTable``.

    Mirrors: ``ObjectMgr::LoadPlayerInfo`` (ObjectMgr.cpp:3807; stats block :4261-4356) reading
    ``player_racestats`` then ``player_classlevelstats``; the race modifier is
    added per level.  No row is invented for a missing level (Trinity's own
    gap-fill at :4349-4356 copies the previous level; that is reported by the
    compiler, not applied here).
    """
    tables = doc["tables"]
    cls = tables["player_classlevelstats"]
    idx = {c: i for i, c in enumerate(cls["columns"])}
    class_level: dict[int, dict[int, tuple[int, ...]]] = defaultdict(dict)
    for row in cls["rows"]:
        class_level[int(row[idx["class"]])][int(row[idx["level"]])] = tuple(
            int(row[idx[c]]) for c in ("str", "agi", "sta", "inte", "spi"))
    race = tables["player_racestats"]
    ridx = {c: i for i, c in enumerate(race["columns"])}
    race_mods = {int(row[ridx["race"]]): tuple(int(row[ridx[c]]) for c in ("str", "agi", "sta", "inte", "spi"))
                 for row in race["rows"]}
    prov = doc.get("provenance", {})
    shown = ""
    if path:
        # never embed a host path in a committed corpus: show repository-relative paths
        resolved = Path(path).resolve()
        shown = str(resolved.relative_to(ROOT)) if resolved.is_relative_to(ROOT) else resolved.name
    source = (f"{prov.get('tdb_release', 'TDB')} player_classlevelstats + player_racestats "
              f"(world-db corpus{' ' + shown if shown else ''})")
    return BaseStatTable(class_level_stats=dict(class_level), race_stat_modifiers=race_mods, source=source)


def load_base_stats(fixture: Fixture, *, base_stats_file: Path | None = None,
                    world_db_corpus: Path | None = None) -> tuple[BaseStatTable | None, dict[str, Any]]:
    """Pick the base-stat source: CLI override > fixture section > none.  Always explicit."""
    spec = fixture.base_stats_spec
    if base_stats_file is not None:
        base_stats_file = Path(base_stats_file).resolve()
        table = BaseStatTable.from_json_file(base_stats_file)
        shown = (str(base_stats_file.relative_to(ROOT)) if base_stats_file.is_relative_to(ROOT)
                 else base_stats_file.name)
        return table, {"source": "base-stats-file", "path": shown, "sha256": _sha(base_stats_file)}
    if world_db_corpus is not None or spec["source"] == "world-db-corpus":
        # a CLI path is relative to the cwd; a fixture path is relative to the repository root
        path = Path(world_db_corpus).resolve() if world_db_corpus is not None else (
            (ROOT / spec["path"]).resolve() if spec.get("path") else DEFAULT_WORLD_DB_CORPUS)
        if not path.exists():
            raise SourceError(f"world-db corpus {path} does not exist")
        doc = json.loads(path.read_text(encoding="utf-8"))
        table = base_stat_table_from_world_corpus(doc, path)
        levels = sorted({lvl for levels in table.class_level_stats.values() for lvl in levels})
        races_present = sorted(table.race_stat_modifiers)
        shown = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name
        fill_rule = spec.get("fill_rule") or "none"
        extra: dict[str, Any] = {}
        if fill_rule == "trinity":
            # explicit opt-in: ObjectMgr::LoadPlayerInfo's gap fill (ObjectMgr.cpp:4349-4356) as agent F's adapter
            # reproduces it; filled cells are tagged trinity-consumer(fill-rule), never Retail truth
            from .basestats_tdb import FILL_RULE_TRINITY, load as load_tdb, playercreateinfo_pairs
            pairs = playercreateinfo_pairs()
            if pairs is None:
                raise SourceError("fill_rule trinity needs the playercreateinfo pairs of character-prep-corpora/"
                                  "base-stat-gap.json (python3 character_prep.py base-stat-gap --tdb ...)")
            table = load_tdb(path).table(FILL_RULE_TRINITY, pairs=pairs)
            extra = {"fill_rule_coordinates": "src/server/game/Globals/ObjectMgr.cpp:4336-4356",
                     "filled_cell_evidence": "trinity-consumer(fill-rule)",
                     "pairs_source": "docs/research/character-prep-corpora/base-stat-gap.json playercreateinfo_pairs"}
        return table, {"source": "world-db-corpus", "path": shown, "sha256": _sha(path),
                       "corpus_provenance": doc.get("provenance", {}), "max_level_present": max(levels),
                       "races_present": races_present, "fill_rule": fill_rule, **extra}
    if spec["source"] == "inline":
        table = BaseStatTable(
            class_level_stats={int(c): {int(l): tuple(int(x) for x in v) for l, v in levels.items()}
                               for c, levels in spec["class_level_stats"].items()},
            race_stat_modifiers={int(r): tuple(int(x) for x in v)
                                 for r, v in spec.get("race_stat_modifiers", {}).items()},
            source=spec.get("provenance") or "inline (fixture)")
        return table, {"source": "inline", "provenance": spec.get("provenance")}
    return None, {"source": "unsupplied"}


# ---------------------------------------------------------------------------
# snapshot projections
# ---------------------------------------------------------------------------

class Snapshot:
    """Every projection the compiler reads, loaded once and lazily."""

    def __init__(self, tables_root: Path | str = DEFAULT_TABLES) -> None:
        self.tables = Tables(tables_root)
        self.source = Source(tables_root)
        self.gear = GearResolver(self.tables)
        self._cache: dict[str, Any] = {}

    def _memo(self, key: str, build: Callable[[], Any]) -> Any:
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # -- spells -----------------------------------------------------------
    @property
    def spell_names(self) -> dict[int, str]:
        return self._memo("names", lambda: {r[0]: r[1] for r in self.source.project("SpellName", ("ID", "Name_lang"))})

    @property
    def spell_attrs(self) -> dict[int, tuple[int, ...]]:
        cols = ("SpellID", "DifficultyID") + tuple(f"Attributes_{i}" for i in range(17))

        def build() -> dict[int, tuple[int, ...]]:
            out = {}
            for r in self.source.project("SpellMisc", cols):
                if r[1] == 0:
                    out[r[0]] = tuple(int(x) for x in r[2:])
            return out
        return self._memo("attrs", build)

    @property
    def spell_effects(self) -> dict[int, list[tuple[int, int, int, int, int]]]:
        cols = ("SpellID", "DifficultyID", "Effect", "EffectAura", "EffectMiscValue_0", "EffectMiscValue_1", "EffectIndex")

        def build() -> dict[int, list[tuple[int, int, int, int, int]]]:
            out: dict[int, list[tuple[int, int, int, int, int]]] = defaultdict(list)
            for r in self.source.project("SpellEffect", cols):
                if r[1] == 0:
                    out[r[0]].append((r[6], r[2], r[3], r[4], r[5]))
            return dict(out)
        return self._memo("effects", build)

    @property
    def proc_masks(self) -> dict[int, int]:
        def build() -> dict[int, int]:
            out = {}
            for r in self.source.project("SpellAuraOptions", ("SpellID", "DifficultyID", "ProcTypeMask_0", "ProcTypeMask_1")):
                if r[1] == 0:
                    out[r[0]] = (int(r[2]) & 0xFFFFFFFF) | ((int(r[3]) & 0xFFFFFFFF) << 32)
            return out
        return self._memo("procmask", build)

    @property
    def shapeshift_masks(self) -> dict[int, int]:
        return self._memo("shapeshift", lambda: {
            r[0]: (int(r[1]) & 0xFFFFFFFF) | ((int(r[2]) & 0xFFFFFFFF) << 32)
            for r in self.source.project("SpellShapeshift", ("SpellID", "ShapeshiftMask_0", "ShapeshiftMask_1"))})

    @property
    def equipped_items(self) -> dict[int, tuple[int, int, int]]:
        return self._memo("equipped", lambda: {
            r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in self.source.project(
                "SpellEquippedItems", ("SpellID", "EquippedItemClass", "EquippedItemSubclass", "EquippedItemInvTypes"))})

    @property
    def aura_restrictions(self) -> dict[int, int]:
        def build() -> dict[int, int]:
            return {r[0]: int(r[2]) for r in self.source.project("SpellAuraRestrictions", ("SpellID", "DifficultyID", "CasterAuraState"))
                    if r[1] == 0 and r[2]}
        return self._memo("caster_aura_state", build)

    @property
    def show_future_spell_conditions(self) -> dict[int, int]:
        """SpellMisc.ShowFutureSpellPlayerConditionID (DifficultyID 0) -> SpellInfo (SpellInfo.cpp:1373)."""
        return self._memo("sfspc", lambda: {
            r[0]: int(r[2]) for r in self.source.project("SpellMisc", ("SpellID", "DifficultyID", "ShowFutureSpellPlayerConditionID"))
            if r[1] == 0 and r[2]})

    @property
    def race_alliance(self) -> dict[int, int]:
        """ChrRaces.Alliance -> Player::TeamForRace (Player.cpp:6376-6391): 0 Alliance, 1 Horde, 2 Pandaria neutral."""
        return self._memo("race_alliance", lambda: {
            r[0]: int(r[1]) for r in self.source.project("ChrRaces", ("ID", "Alliance"))})

    @property
    def max_charges_auras(self) -> dict[int, list[tuple[int, float]]]:
        """spell -> [(charge category, base points)] of SPELL_AURA_MOD_MAX_CHARGES effects (SpellHistory.cpp:964-973)."""
        def build() -> dict[int, list[tuple[int, float]]]:
            out: dict[int, list[tuple[int, float]]] = defaultdict(list)
            for r in self.source.project("SpellEffect", ("SpellID", "DifficultyID", "EffectAura", "EffectMiscValue_0",
                                                          "EffectBasePointsF")):
                if r[1] == 0 and r[2] == SPELL_AURA_MOD_MAX_CHARGES:
                    out[r[0]].append((int(r[3]), float(r[4])))
            return dict(out)
        return self._memo("maxcharges", build)

    @property
    def charge_categories(self) -> dict[int, int]:
        def build() -> dict[int, int]:
            return {r[0]: int(r[2]) for r in self.source.project("SpellCategories", ("SpellID", "DifficultyID", "ChargeCategory"))
                    if r[1] == 0 and r[2]}
        return self._memo("chargecat", build)

    @property
    def categories(self) -> dict[int, tuple[int, int]]:
        return self._memo("spellcategory", lambda: {
            r[0]: (int(r[1]), int(r[2])) for r in self.source.project("SpellCategory", ("ID", "MaxCharges", "ChargeRecoveryTime"))})

    @property
    def cooldowns(self) -> dict[int, tuple[int, int]]:
        def build() -> dict[int, tuple[int, int]]:
            return {r[0]: (int(r[2]), int(r[3])) for r in self.source.project(
                "SpellCooldowns", ("SpellID", "DifficultyID", "RecoveryTime", "CategoryRecoveryTime")) if r[1] == 0}
        return self._memo("cooldowns", build)

    def spell_exists(self, spell_id: int) -> bool:
        return spell_id in self.spell_names

    def is_passive(self, spell_id: int) -> bool:
        attrs = self.spell_attrs.get(spell_id)
        return bool(attrs and attrs[SPELL_ATTR0_PASSIVE[0]] & SPELL_ATTR0_PASSIVE[1])

    def has_attr(self, spell_id: int, key: tuple[int, int]) -> bool:
        attrs = self.spell_attrs.get(spell_id)
        return bool(attrs and attrs[key[0]] & key[1])

    # -- traits -----------------------------------------------------------
    @property
    def traits(self) -> dict[str, Any]:
        """Trait projections in DB2 store order (ascending ID), as ``TraitMgr::Load`` indexes them (TraitMgr.cpp:91-310)."""
        def build() -> dict[str, Any]:
            src = self.source
            t: dict[str, Any] = {}
            t["tree"] = {r[0]: {"system": r[1], "flags": r[2]} for r in src.project("TraitTree", ("ID", "TraitSystemID", "Flags"))}
            t["loadouts"] = defaultdict(list)          # spec -> [(loadout id, tree)]
            for r in src.project("TraitTreeLoadout", ("ID", "TraitTreeID", "ChrSpecializationID")):
                t["loadouts"][r[2]].append((r[0], r[1]))
            t["loadout_entries"] = defaultdict(list)
            for r in src.project("TraitTreeLoadoutEntry", ("ID", "TraitTreeLoadoutID", "SelectedTraitNodeID",
                                                           "SelectedTraitNodeEntryID", "NumPoints", "OrderIndex")):
                t["loadout_entries"][r[1]].append({"node": r[2], "entry": r[3], "points": r[4], "order": r[5]})
            t["node"] = {r[0]: {"tree": r[1], "type": r[2], "subtree": r[3], "flags": r[4]}
                         for r in src.project("TraitNode", ("ID", "TraitTreeID", "Type", "TraitSubTreeID", "Flags"))}
            t["tree_nodes"] = defaultdict(list)        # tree -> [node] (store order: Tree::Nodes, TraitMgr.cpp:197)
            for node_id, n in t["node"].items():
                t["tree_nodes"][n["tree"]].append(node_id)
            t["entry"] = {r[0]: {"definition": r[1], "max_ranks": r[2], "entry_type": r[3], "subtree": r[4]}
                          for r in src.project("TraitNodeEntry", ("ID", "TraitDefinitionID", "MaxRanks", "NodeEntryType", "TraitSubTreeID"))}
            t["node_entries"] = defaultdict(list)      # node -> [entry]  (Node::Entries, TraitMgr.cpp:199-209)
            t["entry_nodes"] = defaultdict(list)      # entry -> [node]
            for r in src.project("TraitNodeXTraitNodeEntry", ("TraitNodeID", "TraitNodeEntryID", "_Index")):
                if r[1] in t["entry"]:
                    t["node_entries"][r[0]].append(r[1])
                    t["entry_nodes"][r[1]].append(r[0])
            t["definition"] = {r[0]: {"spell": r[1], "overrides": r[2], "visible": r[3]}
                               for r in src.project("TraitDefinition", ("ID", "SpellID", "OverridesSpellID", "VisibleSpellID"))}
            t["definition_effect_points"] = defaultdict(list)
            for r in src.project("TraitDefinitionEffectPoints", ("TraitDefinitionID", "EffectIndex", "OperationType", "CurveID")):
                t["definition_effect_points"][r[0]].append({"effect_index": r[1], "operation_type": r[2], "curve_id": r[3]})
            t["subtree"] = {r[0]: {"name": r[1], "tree": r[2]} for r in src.project("TraitSubTree", ("ID", "Name_lang", "TraitTreeID"))}
            t["cond"] = {r[0]: {"id": r[0], "type": r[1], "spec_set": r[2], "granted_ranks": r[3], "currency": r[4],
                                "spent_required": r[5], "level": r[6], "tree": r[7], "quest": r[8], "achievement": r[9],
                                "group_ref": r[10], "node_ref": r[11], "entry_ref": r[12], "flags": r[13], "account_element": r[14]}
                         for r in src.project("TraitCond", ("ID", "CondType", "SpecSetID", "GrantedRanks", "TraitCurrencyID",
                                                            "SpentAmountRequired", "RequiredLevel", "TraitTreeID", "QuestID",
                                                            "AchievementID", "TraitNodeGroupID", "TraitNodeID", "TraitNodeEntryID",
                                                            "Flags", "TraitCondAccountElementID"))}
            t["account_elements"] = {r[0] for r in src.project("TraitCondAccountElement", ("ID",))}
            t["spec_set"] = defaultdict(set)
            for r in src.project("SpecSetMember", ("ChrSpecializationID", "SpecSet")):
                t["spec_set"][r[1]].add(r[0])

            def link(table: str, cols: tuple[str, str], key_first: bool, known: dict) -> dict[int, list[int]]:
                out: dict[int, list[int]] = defaultdict(list)
                for r in src.project(table, cols):
                    key, val = (r[0], r[1]) if key_first else (r[1], r[0])
                    if val in known:
                        out[key].append(val)
                return out
            t["node_conds"] = link("TraitNodeXTraitCond", ("TraitCondID", "TraitNodeID"), False, t["cond"])
            t["group_conds"] = link("TraitNodeGroupXTraitCond", ("TraitCondID", "TraitNodeGroupID"), False, t["cond"])
            t["entry_conds"] = link("TraitNodeEntryXTraitCond", ("TraitCondID", "TraitNodeEntryID"), False, t["cond"])
            t["groups"] = {r[0] for r in src.project("TraitNodeGroup", ("ID",))}
            t["node_groups"] = defaultdict(list)       # node -> [group]  (Node::Groups, TraitMgr.cpp:211-219)
            for r in src.project("TraitNodeGroupXTraitNode", ("TraitNodeGroupID", "TraitNodeID")):
                if r[0] in t["groups"]:
                    t["node_groups"][r[1]].append(r[0])
            t["edges_in"] = defaultdict(list)          # child -> [(parent, type)]  (TraitMgr.cpp:252-260)
            for r in src.project("TraitEdge", ("LeftTraitNodeID", "RightTraitNodeID", "Type")):
                if r[0] in t["node"] and r[1] in t["node"]:
                    t["edges_in"][r[1]].append((r[0], r[2]))
            t["cost"] = {r[0]: {"amount": r[1], "currency": r[2]} for r in src.project("TraitCost", ("ID", "Amount", "TraitCurrencyID"))}
            t["entry_costs"] = link("TraitNodeEntryXTraitCost", ("TraitNodeEntryID", "TraitCostID"), True, t["cost"])
            t["node_costs"] = link("TraitNodeXTraitCost", ("TraitNodeID", "TraitCostID"), True, t["cost"])
            t["group_costs"] = link("TraitNodeGroupXTraitCost", ("TraitNodeGroupID", "TraitCostID"), True, t["cost"])
            t["tree_costs"] = link("TraitTreeXTraitCost", ("TraitTreeID", "TraitCostID"), True, t["cost"])
            t["currency"] = {r[0]: {"type": r[1], "currency_types_id": r[2], "flags": r[3]}
                             for r in src.project("TraitCurrency", ("ID", "Type", "CurrencyTypesID", "Flags"))}
            t["tree_currencies"] = defaultdict(list)
            for r in src.project("TraitTreeXTraitCurrency", ("TraitTreeID", "TraitCurrencyID", "_Index")):
                if r[1] in t["currency"]:
                    t["tree_currencies"][r[0]].append((r[2], r[1]))
            t["currency_sources"] = defaultdict(list)
            for r in src.project("TraitCurrencySource", ("ID", "TraitCurrencyID", "Amount", "QuestID", "AchievementID", "PlayerLevel", "TraitNodeEntryID")):
                t["currency_sources"][r[1]].append({"id": r[0], "amount": r[2], "quest": r[3], "achievement": r[4], "level": r[5], "entry": r[6]})
            t["pvp_talent"] = {r[0]: {"spec": r[1], "spell": r[2], "overrides": r[3], "category": r[4], "level": r[5]}
                               for r in src.project("PvpTalent", ("ID", "SpecID", "SpellID", "OverridesSpellID", "PvpTalentCategoryID", "LevelRequired"))}
            t["pvp_category"] = {r[0]: r[1] for r in src.project("PvpTalentCategory", ("ID", "TalentSlotMask"))}
            t["pvp_slot_unlock"] = {r[0]: (r[1], r[2], r[3]) for r in src.project(
                "PvpTalentSlotUnlock", ("Slot", "LevelRequired", "DeathKnightLevelRequired", "DemonHunterLevelRequired"))}
            return t
        return self._memo("traits", build)

    @property
    def skill_lines(self) -> dict[int, dict[str, Any]]:
        return self._memo("skill_lines", lambda: {
            r[0]: {"category": r[1], "name": r[2], "parent": r[3]}
            for r in self.source.project("SkillLine", ("ID", "CategoryID", "DisplayName_lang", "ParentSkillLineID"))})

    @property
    def skill_race_class_info(self) -> list[dict[str, Any]]:
        return self._memo("srci", lambda: [
            {"id": r[0], "skill": r[1], "class_mask": r[2], "flags": r[3], "availability": r[4], "min_level": r[5],
             "tier": r[6], "race_mask": (int(r[7]) & 0xFFFFFFFF) | ((int(r[8]) & 0xFFFFFFFF) << 32)}
            for r in self.source.project("SkillRaceClassInfo", ("ID", "SkillID", "ClassMask", "Flags", "Availability", "MinLevel",
                                                                "SkillTierID", "RaceMasks_0", "RaceMasks_1"))])

    @property
    def skill_line_abilities(self) -> dict[int, list[dict[str, Any]]]:
        """skill -> SkillLineAbility rows in store order (DB2Manager::GetSkillLineAbilitiesBySkill)."""
        def build() -> dict[int, list[dict[str, Any]]]:
            out: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for r in self.source.project("SkillLineAbility", ("ID", "SkillLine", "Spell", "MinSkillLineRank", "ClassMask",
                                                              "AcquireMethod", "RaceMasks_0", "RaceMasks_1")):
                out[r[1]].append({"id": r[0], "skill": r[1], "spell": r[2], "min_rank": r[3], "class_mask": r[4],
                                  "acquire_method": r[5],
                                  "race_mask": (int(r[6]) & 0xFFFFFFFF) | ((int(r[7]) & 0xFFFFFFFF) << 32)})
            return dict(out)
        return self._memo("sla", build)

    @property
    def spell_levels(self) -> dict[int, tuple[int, int]]:
        """SpellID -> (SpellLevel, BaseLevel) for DifficultyID 0 (SpellInfo::SpellLevel / BaseLevel)."""
        return self._memo("spell_levels", lambda: {
            r[0]: (int(r[2]), int(r[3])) for r in self.source.project("SpellLevels", ("SpellID", "DifficultyID", "SpellLevel", "BaseLevel"))
            if r[1] == 0})

    @property
    def race_bits(self) -> dict[int, int]:
        """ChrRaces.PlayableRaceBit (db2); cross-checked against Trinity's RaceMask::GetRaceBit table."""
        return self._memo("race_bits", lambda: {
            r[0]: int(r[1]) for r in self.source.project("ChrRaces", ("ID", "PlayableRaceBit"))})

    def class_trait_trees(self, class_id: int) -> tuple[list[int], dict[str, Any]]:
        """``TraitMgr::GetTreesForConfig`` for a Combat config (TraitMgr.cpp:338-354).

        ``_skillLinesByClass[class]`` is the last SkillLineXTraitTree row (store order) whose skill line has
        CategoryID 7 and whose SkillRaceClassInfo ClassMask names the class (TraitMgr.cpp:262-284);
        ``_traitTreesBySkillLine[line]`` is every SkillLineXTraitTree tree of that line in store order.
        """
        def build() -> dict[str, Any]:
            by_line: dict[int, list[int]] = defaultdict(list)
            line_of_class: dict[int, int] = {}
            srci_by_skill: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for row in self.skill_race_class_info:
                srci_by_skill[row["skill"]].append(row)
            for _, line, tree in self.source.project("SkillLineXTraitTree", ("ID", "SkillLineID", "TraitTreeID")):
                if tree not in self.traits["tree"] or line not in self.skill_lines:
                    continue
                by_line[line].append(tree)
                if self.skill_lines[line]["category"] == SKILL_CATEGORY_CLASS:
                    for row in srci_by_skill.get(line, []):
                        for c in range(1, MAX_CLASSES):
                            if row["class_mask"] & (1 << (c - 1)):
                                line_of_class[c] = line
            return {"by_line": dict(by_line), "line_of_class": line_of_class}
        idx = self._memo("class_trees", build)
        line = idx["line_of_class"].get(class_id)
        trees = list(idx["by_line"].get(line, [])) if line is not None else []
        return trees, {"skill_line_id": line, "skill_line_name": self.skill_lines.get(line, {}).get("name") if line else None}

    def default_skill_spells(self, race_id: int, class_id: int, level: int) -> dict[str, Any]:
        """Spells a fresh character of (race, class, level) learns through its default skills.

        Mirrors ``ObjectMgr::LoadPlayerInfo`` skill list (ObjectMgr.cpp:4059-4072), ``Player::LearnDefaultSkills``
        (Player.cpp:25259-25275), ``LearnDefaultSkill`` (:25277-25321, ``GetSkillRangeType`` ObjectMgr.cpp:9002-9023),
        ``SetSkill`` -> ``LearnSkillRewardedSpells`` (Player.cpp:5870, :25391-25441).
        """
        key = f"default_skills:{race_id}:{class_id}:{level}"

        def build() -> dict[str, Any]:
            race_bit = self.race_bits.get(race_id, -1)
            class_mask = 1 << (class_id - 1)

            def has_race(mask: int) -> bool:
                return 0 <= race_bit < 64 and bool(mask & (1 << race_bit))

            skills: list[dict[str, Any]] = []
            seen: set[int] = set()
            for row in self.skill_race_class_info:
                if row["availability"] != 1:
                    continue
                if row["race_mask"] and not has_race(row["race_mask"]):
                    continue
                if not (row["class_mask"] in (-1, 0) or row["class_mask"] & class_mask):
                    continue
                if row["skill"] in seen:          # HasSkill(rcInfo->SkillID) -> continue (Player.cpp:25267)
                    continue
                if row["min_level"] > level:      # Player.cpp:25270
                    continue
                line = self.skill_lines.get(row["skill"])
                if line is None:                  # SKILL_RANGE_NONE -> nothing learned
                    continue
                seen.add(row["skill"])
                if row["tier"]:
                    range_type = "rank"
                elif row["skill"] == SKILL_RUNEFORGING or line["category"] == SKILL_CATEGORY_ARMOR:
                    range_type = "mono"
                elif line["category"] == SKILL_CATEGORY_LANGUAGES:
                    range_type = "language"
                else:
                    range_type = "level"
                if range_type == "language":
                    value: int | None = 300
                elif range_type == "mono":
                    value = 1
                elif range_type == "level":
                    max_value = level * 5         # Unit::GetMaxSkillValueForLevel (Unit.h:931)
                    if row["flags"] & SKILL_FLAG_ALWAYS_MAX_VALUE:
                        value = max_value
                    elif class_id == 6:
                        value = min(max(1, (level - 1) * 5), max_value)
                    else:
                        value = 1
                else:
                    # SKILL_RANGE_RANK needs world-DB skill_tiers; the value only matters for AutomaticSkillRank
                    # (ALWAYS_MAX or the DK formula read the tier maximum; otherwise the value is 1)
                    value = None if (row["flags"] & SKILL_FLAG_ALWAYS_MAX_VALUE or class_id == 6) else 1
                skills.append({"skill_line": row["skill"], "skill_name": line["name"], "category": line["category"],
                               "skill_race_class_info_id": row["id"], "range_type": range_type, "skill_value": value})
            spells: list[dict[str, Any]] = []
            undecided: list[dict[str, Any]] = []
            for sk in skills:
                for ab in self.skill_line_abilities.get(sk["skill_line"], []):
                    if not self.spell_exists(ab["spell"]):
                        continue                  # GetSpellInfo == nullptr (Player.cpp:25400)
                    method = ab["acquire_method"]
                    if method not in (ACQUIRE_AUTOMATIC_SKILL_RANK, ACQUIRE_AUTOMATIC_CHAR_LEVEL, ACQUIRE_LEARNED_OR_AUTOMATIC):
                        continue
                    if ab["race_mask"] and not has_race(ab["race_mask"]):
                        continue
                    if ab["class_mask"] and not (ab["class_mask"] & class_mask):
                        continue
                    spell_level, base_level = self.spell_levels.get(ab["spell"], (0, 0))
                    if max(spell_level, base_level) > level:
                        continue
                    rec = {"spell_id": ab["spell"], "skill_line": sk["skill_line"], "skill_name": sk["skill_name"],
                           "skill_category": sk["category"], "skill_line_ability_id": ab["id"], "acquire_method": method,
                           "racial": bool(ab["race_mask"])}
                    if method == ACQUIRE_LEARNED_OR_AUTOMATIC:
                        pc = self.show_future_spell_conditions.get(ab["spell"], 0)
                        if pc:
                            why = (f"AcquireMethod 4: SpellMisc.ShowFutureSpellPlayerConditionID {pc} is evaluated on the player "
                                   f"(ConditionMgr::IsPlayerMeetingCondition; level, map, ...) and then the world-DB conditions "
                                   f"table (Player.cpp:25409-25415)")
                        else:
                            why = ("AcquireMethod 4: no ShowFutureSpellPlayerConditionID; only world-DB conditions rows "
                                   f"(CONDITION_SOURCE_TYPE_SKILL_LINE_ABILITY, SourceEntry {ab['id']}) decide; with no row the "
                                   "spell is learned (Player.cpp:25413-25414)")
                        undecided.append(dict(rec, why=why, show_future_spell_player_condition_id=pc))
                        continue
                    if method == ACQUIRE_AUTOMATIC_SKILL_RANK:
                        if sk["skill_value"] is None:
                            undecided.append(dict(rec, why="skill value needs world-DB skill_tiers (SKILL_RANGE_RANK)"))
                            continue
                        if sk["skill_value"] < ab["min_rank"]:
                            continue              # RemoveSpell branch (Player.cpp:25432-25433)
                    spells.append(rec)
            return {"race_bit": race_bit, "skills": skills, "spells": spells, "undecided": undecided}
        return self._memo(key, build)

    # -- powers -----------------------------------------------------------
    @property
    def power_types(self) -> dict[int, dict[str, Any]]:
        cols = ("ID", "PowerTypeEnum", "MinPower", "MaxBasePower", "CenterPower", "DefaultPower", "RegenPeace", "RegenCombat", "Flags")
        return self._memo("powertype", lambda: {
            r[1]: {"id": r[0], "min": r[2], "max_base": r[3], "center": r[4], "default": r[5],
                   "regen_peace": r[6], "regen_combat": r[7], "flags": r[8]}
            for r in self.source.project("PowerType", cols)})

    @property
    def class_powers(self) -> dict[int, list[int]]:
        def build() -> dict[int, list[int]]:
            out: dict[int, list[int]] = defaultdict(list)
            for r in self.source.project("ChrClassesXPowerTypes", ("ClassID", "PowerType")):
                out[r[0]].append(r[1])
            return {k: sorted(v) for k, v in out.items()}
        return self._memo("classpowers", build)

    # -- corpora ------------------------------------------------------------
    @property
    def markers(self) -> dict[int, dict[str, Any]]:
        def build() -> dict[int, dict[str, Any]]:
            if not MARKERS_CORPUS.exists():
                return {}
            doc = json.loads(MARKERS_CORPUS.read_text(encoding="utf-8"))
            return {int(k): v for k, v in doc.get("player_dummy_aura_owners", {}).items()}
        return self._memo("markers", build)

    @property
    def proc_provider_ids(self) -> set[int]:
        def build() -> set[int]:
            if not PROC_CENSUS.exists():
                return set()
            doc = json.loads(PROC_CENSUS.read_text(encoding="utf-8"))
            return set(int(x) for x in doc.get("player", {}).get("provider_ids", []))
        return self._memo("providers", build)


# ---------------------------------------------------------------------------
# trait engine: static port of TraitMgr (src/server/game/Spells/TraitMgr.cpp)
# ---------------------------------------------------------------------------

class TraitEngine:
    """One Combat trait config for (spec, level), evaluated the way ``TraitMgr`` evaluates it.

    Ported functions (TraitMgr.cpp at the pinned commit):
    ``GetTreesForConfig`` :338-354, ``FillOwnedCurrenciesMap`` :409-473, ``GetGateConditionsForNode`` :475-500,
    ``AddSpentCurrenciesForEntry`` :502-540, ``CountTraitNodeRanks`` :579-600, ``MeetsTraitCondition`` :602-679,
    ``NodeMeetsTraitConditions`` :681-764, ``GetGrantedTraitEntriesForConfig`` :766-820, ``IsValidEntry`` :822-836,
    ``ValidateConfig`` :838-1015 (entry check :871-904, removal loop :906-926, sub-trees :928-964, currencies :966-980),
    ``CanApplyTraitNode`` :1017-1031.

    A config entry is ``{"node", "entry", "rank", "granted"}``.  Everything a player-state accessor would answer
    (quests, achievements, account data elements) is reported through ``self.undecidable`` instead of guessed;
    the current class trees use none of them (census in the report).
    """

    def __init__(self, t: dict[str, Any], spec_id: int, class_id: int, level: int, trees: list[int]) -> None:
        self.t = t
        self.spec_id = spec_id
        self.class_id = class_id
        self.level = level
        self.trees = trees
        self.undecidable: list[str] = []

    # -- helpers --------------------------------------------------------------
    def _cond(self, cid: int) -> dict[str, Any]:
        return self.t["cond"][cid]

    def count_ranks(self, entries: list[dict[str, Any]], group_id: int, node_id: int, entry_id: int) -> tuple[int, int, int]:
        g = n = e = 0
        for te in entries:
            if group_id and group_id in self.t["node_groups"].get(te["node"], []):
                g += te["rank"]
            if node_id == te["node"]:
                n += te["rank"]
            if entry_id == te["entry"]:
                e += te["rank"]
        return g, n, e

    def gate_conditions(self, node_id: int) -> dict[int, int]:
        """currency -> SpentAmountRequired of the IsGate condition with the highest requirement."""
        out: dict[int, int] = {}
        conds = list(self.t["node_conds"].get(node_id, []))
        for g in self.t["node_groups"].get(node_id, []):
            conds.extend(self.t["group_conds"].get(g, []))
        for cid in conds:
            c = self._cond(cid)
            if not c["flags"] & TRAIT_COND_FLAG_IS_GATE:
                continue
            if c["currency"] not in out or out[c["currency"]] < c["spent_required"]:
                out[c["currency"]] = c["spent_required"]
        return out

    def entry_costs(self, node_id: int, entry_id: int) -> list[dict[str, int]]:
        t = self.t
        costs: list[int] = []
        for g in t["node_groups"].get(node_id, []):
            costs.extend(t["group_costs"].get(g, []))
        if entry_id in t["node_entries"].get(node_id, []):
            costs.extend(t["entry_costs"].get(entry_id, []))
        costs.extend(t["node_costs"].get(node_id, []))
        costs.extend(t["tree_costs"].get(t["node"][node_id]["tree"], []))
        return [t["cost"][c] for c in costs]

    def spent(self, entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for te in entries:
            gates = self.gate_conditions(te["node"])
            for cost in self.entry_costs(te["node"], te["entry"]):
                amount = cost["amount"] * te["rank"]
                cur = out.setdefault(cost["currency"], {"total": 0, "by_gate": {}})
                cur["total"] += amount
                gate = gates.get(cost["currency"], 0)
                cur["by_gate"][gate] = cur["by_gate"].get(gate, 0) + amount
        return out

    # -- conditions -------------------------------------------------------------
    def meets_condition(self, c: dict[str, Any], entries: list[dict[str, Any]], spent: dict[int, dict[str, Any]]) -> bool:
        if c["quest"]:
            self.undecidable.append(f"TraitCond {c['id']} QuestID {c['quest']} (Player::IsQuestRewarded)")
            return False
        if c["achievement"]:
            self.undecidable.append(f"TraitCond {c['id']} AchievementID {c['achievement']} (Player::HasAchieved)")
            return False
        if c["spec_set"] and self.spec_id not in self.t["spec_set"].get(c["spec_set"], set()):
            return False
        has_ref = bool(c["group_ref"] or c["node_ref"] or c["entry_ref"])
        if c["currency"] in self.t["currency"]:
            if has_ref:
                cur = spent.get(c["currency"], {"by_gate": {}})
                amount = sum(v for gate, v in cur["by_gate"].items() if gate < c["spent_required"])
                if amount < c["spent_required"]:
                    return False
        elif has_ref:
            g, n, e = self.count_ranks(entries, c["group_ref"], c["node_ref"], c["entry_ref"])
            req = c["spent_required"]
            if req and g < req and n < req and e < req:
                return False
            if not req and g != 0 and n != 0 and e != 0:
                return False
        if c["level"] and self.level < c["level"]:
            return False
        if c["account_element"] in self.t["account_elements"]:
            self.undecidable.append(f"TraitCond {c['id']} TraitCondAccountElementID {c['account_element']} (player data element)")
            return False
        return True

    def node_meets(self, node_id: int, entry_id: int, entries: list[dict[str, Any]], spent: dict[int, dict[str, Any]]) -> bool:
        t = self.t

        def meets(cond_ids: list[int], ctype: int, rank_of: Callable[[], int]) -> tuple[bool, bool]:
            sufficient = failed = False
            rank = None
            for cid in cond_ids:
                c = self._cond(cid)
                if c["type"] != ctype:
                    continue
                if ctype == TRAIT_COND_RANKS_ALLOWED:
                    if rank is None:
                        rank = rank_of()
                    if rank < c["granted_ranks"]:
                        continue
                if not self.meets_condition(c, entries, spent):
                    failed = True
                    continue
                if c["flags"] & TRAIT_COND_FLAG_IS_SUFFICIENT:
                    sufficient = True
                    break
            return sufficient, failed

        def of_type(ctype: int) -> bool:
            has_failed = False
            s, f = meets(t["node_conds"].get(node_id, []), ctype, lambda: self.count_ranks(entries, 0, node_id, entry_id)[1])
            if s:
                return True
            has_failed |= f
            for g in t["node_groups"].get(node_id, []):
                conds = t["group_conds"].get(g, [])
                if not conds:
                    continue
                s, f = meets(conds, ctype, lambda g=g: self.count_ranks(entries, g, node_id, entry_id)[0])
                if s:
                    return True
                has_failed |= f
            for e in t["node_entries"].get(node_id, []):
                conds = t["entry_conds"].get(e, [])
                if e != entry_id or not conds:
                    continue
                s, f = meets(conds, ctype, lambda: self.count_ranks(entries, 0, node_id, entry_id)[2])
                if s:
                    return True
                has_failed |= f
            return not has_failed

        return of_type(TRAIT_COND_VISIBLE) and of_type(TRAIT_COND_AVAILABLE) and of_type(TRAIT_COND_RANKS_ALLOWED)

    # -- granted ------------------------------------------------------------------
    def granted_entries(self, entries: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        t = self.t
        entries = entries or []
        spent = self.spent(entries)
        out: dict[tuple[int, int], dict[str, Any]] = {}

        def add(node_id: int, entry_id: int, ranks: int, cond_id: int) -> None:
            rec = out.get((node_id, entry_id))
            if rec is None:
                rec = out[(node_id, entry_id)] = {"node": node_id, "entry": entry_id, "rank": 0, "granted": 0, "granted_by": []}
            rec["granted"] = min(rec["granted"] + ranks, t["entry"][entry_id]["max_ranks"])
            rec["granted_by"].append(cond_id)

        for tree in self.trees:
            for node_id in t["tree_nodes"].get(tree, []):
                node_entries = t["node_entries"].get(node_id, [])
                for e in node_entries:
                    if self.node_meets(node_id, e, entries, spent):
                        for cid in t["entry_conds"].get(e, []):
                            c = self._cond(cid)
                            if c["type"] == TRAIT_COND_GRANTED and self.meets_condition(c, entries, spent):
                                add(node_id, e, c["granted_ranks"], cid)
                cond_lists = [t["node_conds"].get(node_id, [])] + [t["group_conds"].get(g, []) for g in t["node_groups"].get(node_id, [])]
                for conds in cond_lists:
                    for cid in conds:
                        c = self._cond(cid)
                        if c["type"] == TRAIT_COND_GRANTED and self.meets_condition(c, entries, spent):
                            for e in node_entries:
                                if self.node_meets(node_id, e, entries, spent):
                                    add(node_id, e, c["granted_ranks"], cid)
        return list(out.values())

    # -- validation ---------------------------------------------------------------
    def is_valid_entry(self, te: dict[str, Any]) -> bool:
        if te["node"] not in self.t["node"] or te["entry"] not in self.t["node_entries"].get(te["node"], []):
            return False
        return self.t["entry"][te["entry"]]["max_ranks"] >= te["rank"] + te["granted"]

    def fully_filled(self, node_id: int, entries: list[dict[str, Any]]) -> bool:
        t = self.t
        by_key = {(te["node"], te["entry"]): te for te in entries}

        def matches(e: int) -> bool:
            te = by_key.get((node_id, e))
            return te is not None and te["rank"] + te["granted"] == t["entry"][e]["max_ranks"]
        node_entries = t["node_entries"].get(node_id, [])
        if t["node"][node_id]["type"] == TRAIT_NODE_SELECTION:
            return any(matches(e) for e in node_entries)
        return all(matches(e) for e in node_entries)

    def check_entry(self, te: dict[str, Any], entries: list[dict[str, Any]], spent: dict[int, dict[str, Any]]) -> tuple[str, str]:
        """``ValidateConfig::isValidTraitEntry`` (TraitMgr.cpp:871-904): (LearnResult, reason)."""
        t = self.t
        if not self.is_valid_entry(te):
            if te["node"] not in t["node"] or te["entry"] not in t["node_entries"].get(te["node"], []):
                return "Unknown", "IsValidEntry: entry is not attached to the node (TraitMgr.cpp:824-830)"
            return "Unknown", (f"IsValidEntry: rank {te['rank']} + granted {te['granted']} > MaxRanks "
                               f"{t['entry'][te['entry']]['max_ranks']} (TraitMgr.cpp:832)")
        ntype = t["node"][te["node"]]["type"]
        if ntype in (TRAIT_NODE_SELECTION, TRAIT_NODE_SUBTREE_SELECTION):
            count = sum(1 for o in entries if o["node"] == te["node"])
            if count != 1:
                return "Unknown", f"selection node {te['node']} has {count} config entries; exactly one allowed (TraitMgr.cpp:877-879)"
        if not self.node_meets(te["node"], te["entry"], entries, spent):
            return "Unknown", "NodeMeetsTraitConditions failed (TraitMgr.cpp:881-882)"
        parents = t["edges_in"].get(te["node"], [])
        if parents:
            any_parent = False
            for parent, edge_type in parents:
                if not self.fully_filled(parent, entries):
                    if edge_type == TRAIT_EDGE_REQUIRED:
                        return "NotEnoughTalentsInPrimaryTree", f"required parent node {parent} not fully filled (TraitMgr.cpp:889-892)"
                    continue
                any_parent = True
            if not any_parent:
                return "NotEnoughTalentsInPrimaryTree", (f"no fully filled parent among {[p for p, _ in parents]} "
                                                         f"(TraitMgr.cpp:900-901)")
        return "Ok", ""

    def validate(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strict pass (``removeInvalidEntries=false`` reports the first failure; here every failing entry is listed)."""
        spent = self.spent(entries)
        out = []
        for te in entries:
            result, why = self.check_entry(te, entries, spent)
            if result != "Ok":
                out.append({"node_id": te["node"], "node_entry_id": te["entry"], "rank": te["rank"],
                            "granted_ranks": te["granted"], "result": result, "reason": why})
        return out

    def load_path(self, entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """``ValidateConfig(config, player, false, removeInvalidEntries=true)`` as ``Player::_LoadTraits`` calls it
        (Player.cpp:28553): invalid entries are erased, or zeroed when they carry granted ranks, and validation restarts
        (TraitMgr.cpp:906-926)."""
        cur = [dict(te) for te in entries]
        removed: list[dict[str, Any]] = []
        i = 0
        while i < len(cur):
            spent = self.spent(cur)
            result, why = self.check_entry(cur[i], cur, spent)
            if result == "Ok":
                i += 1
                continue
            te = cur[i]
            removed.append({"node_id": te["node"], "node_entry_id": te["entry"], "rank": te["rank"], "granted_ranks": te["granted"],
                            "result": result, "reason": why,
                            "action": "erased" if (not te["granted"] or not te["rank"]) else "rank zeroed"})
            if not te["granted"] or not te["rank"]:
                cur.pop(i)
            else:
                te["rank"] = 0
            i = 0
        return cur, removed

    def subtrees(self, entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        t = self.t
        out: dict[int, dict[str, Any]] = {}
        for te in entries:
            node = t["node"][te["node"]]
            if node["type"] == TRAIT_NODE_SUBTREE_SELECTION:
                out.setdefault(t["entry"][te["entry"]]["subtree"], {"entries": [], "active": False})["active"] = True
            if node["subtree"]:
                out.setdefault(node["subtree"], {"entries": [], "active": False})["entries"].append(te["entry"])
        return out

    # -- currencies ---------------------------------------------------------------
    def owned_currencies(self, entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        t = self.t
        out: dict[int, dict[str, Any]] = {}
        present = {te["entry"] for te in entries if te["rank"] > 0 or te["granted"] > 0}
        for tree in self.trees:
            for _, cur_id in sorted(t["tree_currencies"].get(tree, [])):
                cur = t["currency"][cur_id]
                rec = out.setdefault(cur_id, {"type": cur["type"], "amount": 0, "undecided_sources": []})
                if cur["type"] != TRAIT_CURRENCY_TRAIT_SOURCED:
                    rec["undecided_sources"].append(f"TraitCurrency type {cur['type']} reads player money/currency/data elements")
                    continue
                for s in t["currency_sources"].get(cur_id, []):
                    if (s["quest"] or s["achievement"]) and s["amount"]:
                        rec["undecided_sources"].append(f"TraitCurrencySource {s['id']} (quest {s['quest']}, achievement "
                                                        f"{s['achievement']}) amount {s['amount']}")
                        continue
                    if s["quest"] or s["achievement"]:
                        continue          # amount 0: the outcome is the same either way
                    if s["level"] and self.level < s["level"]:
                        continue
                    if s["entry"] and s["entry"] not in present:
                        continue
                    rec["amount"] += s["amount"]
        return out


# ---------------------------------------------------------------------------
# hooks (Track A / Track B)
# ---------------------------------------------------------------------------

def call_hook(spec: tuple[str, str], context: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Import ``module.attr`` lazily; ``(result, status)`` where status explains a miss."""
    module_name, attr_name = spec
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        return None, f"module {module_name} not importable ({error.__class__.__name__}: {error})"
    hook = getattr(module, attr_name, None)
    if hook is None:
        return None, f"module {module_name} has no {attr_name}"
    try:
        result = hook(context)
    except Exception as error:  # a hook failure must not take the compiler down
        return None, f"{module_name}.{attr_name} raised {error.__class__.__name__}: {error}"
    if not isinstance(result, dict):
        return None, f"{module_name}.{attr_name} returned {type(result).__name__}, expected dict"
    return result, "ok"


# ---------------------------------------------------------------------------
# compiler
# ---------------------------------------------------------------------------

class Compiler:
    def __init__(self, snapshot: Snapshot | None = None) -> None:
        self.snap = snapshot or Snapshot()
        self._characters: dict[int, CharacterResolver] = {}

    def character_resolver(self, base_stats: BaseStatTable | None) -> CharacterResolver:
        key = id(base_stats)
        if key not in self._characters:
            self._characters[key] = CharacterResolver(self.snap.tables, base_stats)
        return self._characters[key]

    # ------------------------------------------------------------------
    def compile(self, fixture: Fixture, *, base_stats_file: Path | None = None,
                world_db_corpus: Path | None = None) -> dict[str, Any]:
        snap = self.snap
        errors: list[str] = []
        warnings: list[str] = []
        unresolved_items: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        derived: dict[str, Any] = {}
        deps: list[dict[str, str]] = []

        def dep(src: str, dst: str) -> None:
            deps.append({"from": src, "to": dst})

        ident = fixture.identity
        race_id, class_id, spec_id, level = ident["race_id"], ident["class_id"], ident["spec_id"], ident["level"]

        # -- identity ---------------------------------------------------
        base_table, base_source = load_base_stats(fixture, base_stats_file=base_stats_file, world_db_corpus=world_db_corpus)
        character = self.character_resolver(base_table)
        try:
            race = character.identity.race(race_id)
            klass = character.identity.klass(class_id)
            spec = character.identity.spec(spec_id)
        except CharacterSourceError as error:
            errors.append(f"identity: {error}")
            return self._finish(fixture, derived, deps, errors, warnings, unresolved_items, blocked, base_source)
        if spec.class_id != class_id:
            errors.append(f"identity: spec {spec_id} ({spec.name}) belongs to class {spec.class_id}, not {class_id}")
        if class_id not in PLAYER_CLASS_IDS:
            errors.append(f"identity: class {class_id} is not a player class (1..13)")
        if race.playable_race_bit < 0:
            errors.append(f"identity: race {race_id} ({race.name}) has no PlayableRaceBit")
        max_level = 90  # CURRENT_EXPANSION == EXPANSION_MIDNIGHT: GetMaxLevelForExpansion (brief pin)
        if not 1 <= level <= max_level:
            errors.append(f"identity: level {level} outside 1..{max_level} (World.cpp:750 MaxPlayerLevel default)")
        if errors:
            return self._finish(fixture, derived, deps, errors, warnings, unresolved_items, blocked, base_source)
        derived["identity"] = node(
            {"race": race.to_dict(), "class": klass.to_dict(), "spec": spec.to_dict(), "level": level},
            "db2-fact",
            [{"step": "identity", "source": "ChrRaces / ChrClasses / ChrSpecialization",
              "consumer": "Player::Create (Player.cpp:387) race/class pair (PlayerInfo, Player.cpp:396-402); "
                          "customization validation (Player.cpp:415) is display-only"}],
            ["/fixture/identity"])
        dep("/fixture/identity", "/derived/identity")

        # -- traits ------------------------------------------------------
        traits = self._compile_traits(fixture, spec, class_id, level, errors, warnings, unresolved_items)
        derived["traits"] = traits
        dep("/fixture/traits", "/derived/traits")
        dep("/derived/identity", "/derived/traits")

        # -- pvp traits ----------------------------------------------------
        derived["pvp_traits"] = self._compile_pvp(fixture, spec, class_id, level, errors, warnings)
        dep("/fixture/pvp_traits", "/derived/pvp_traits")
        dep("/derived/identity", "/derived/pvp_traits")

        # -- gear ----------------------------------------------------------
        hero = fixture.hero_subtree_id
        gear, gear_items = self._compile_gear(fixture, spec, level, hero, errors, warnings)
        derived["gear"] = gear
        dep("/fixture/equipment", "/derived/gear")
        dep("/derived/identity", "/derived/gear")

        # -- acquired spells (needs gear roots + traits) -------------------
        spells = self._compile_spells(fixture, character, race, klass, spec, level, traits, gear, derived["pvp_traits"],
                                      gear_items, errors, warnings, unresolved_items)
        derived["spells"] = spells
        for src in ("/fixture/learned_spells", "/derived/traits", "/derived/gear", "/derived/pvp_traits", "/derived/identity"):
            dep(src, "/derived/spells")

        # -- dual wield / titan grip gates now known: revalidate off-hand ----
        self._validate_offhand(gear_items, spells, errors)
        self._validate_weapon_skills(race_id, class_id, level, gear_items, spells, errors)
        self._validate_item_requirements(race_id, class_id, level, gear_items, spells, errors, unresolved_items)

        # -- armour specialization gate (blocker 5) ------------------------
        armor_spec = self._armor_specialization(character, spec, spells, gear_items, warnings)
        derived["armor_specialization"] = armor_spec
        dep("/derived/gear", "/derived/armor_specialization")
        dep("/derived/spells", "/derived/armor_specialization")

        # -- character stats ------------------------------------------------
        stats = self._compile_stats(character, race_id, class_id, level, spec, gear, armor_spec, base_table, base_source,
                                    unresolved_items, warnings)
        derived["stats"] = stats
        dep("/derived/gear", "/derived/stats")
        dep("/derived/armor_specialization", "/derived/stats")
        dep("/server_inputs/base_stats", "/derived/stats")
        dep("/derived/identity", "/derived/stats")

        # -- hooks ----------------------------------------------------------
        hook_ctx = {"fixture": fixture.raw,
                    "identity": dict(derived["identity"]["value"], learned_spells=fixture.learned_spells),
                    "gear": gear["value"],
                    "spells": spells["value"], "stats": stats["value"] if stats["evidence_class"] != "unresolved" else None,
                    "snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT}
        derived["controlled_units"] = self._hook(CLASS_HOOK, "controlled_units", fixture.controlled_units, hook_ctx,
                                                 unresolved_items, "Track A (controlled_units)",
                                                 ["/fixture/controlled_units", "/derived/spells", "/derived/stats"])
        dep("/fixture/controlled_units", "/derived/controlled_units")
        dep("/derived/spells", "/derived/controlled_units")
        dep("/derived/stats", "/derived/controlled_units")
        derived["weapon"] = self._weapon(gear_items, hook_ctx, unresolved_items)
        dep("/derived/gear", "/derived/weapon")
        dep("/derived/stats", "/derived/weapon")

        # -- initial state --------------------------------------------------
        derived["initial_state"] = self._initial_state(class_id, character, stats, spells, gear, base_table, unresolved_items)
        for src in ("/derived/stats", "/derived/spells", "/derived/gear", "/derived/identity"):
            dep(src, "/derived/initial_state")

        # -- blocked semantics ---------------------------------------------
        blocked.extend(self._blocked(spec, spells, stats))

        return self._finish(fixture, derived, deps, errors, warnings, unresolved_items, blocked, base_source)

    # ------------------------------------------------------------------
    def _finish(self, fixture: Fixture, derived: dict[str, Any], deps: list[dict[str, str]], errors: list[str],
                warnings: list[str], unresolved_items: list[dict[str, Any]], blocked: list[dict[str, Any]],
                base_source: dict[str, Any]) -> dict[str, Any]:
        seen: set[tuple[str, str]] = set()
        edges = []
        for e in deps:
            key = (e["from"], e["to"])
            if key not in seen:
                seen.add(key)
                edges.append(e)
        out = {
            "provenance": {
                "snapshot_build": SNAPSHOT_BUILD,
                "trinitycore_commit": TRINITY_COMMIT,
                "world_database": TDB_RELEASE if base_source.get("source") == "world-db-corpus" else None,
                "generator": "python3 character_prep.py compile --fixture <file> --json",
                "fixture_path": _shown_path(fixture.path),
                "fixture_sha256": fixture.sha256,
                "fixture_provenance": fixture.provenance,
                "base_stats_source": base_source,
            },
            "fixture": {"identity": fixture.identity, "hero_subtree_id": fixture.hero_subtree_id,
                        "trait_entry_count": len(fixture.trait_entries), "slots": sorted(fixture.equipment, key=EQUIPMENT_SLOTS.index),
                        "options": fixture.options},
            "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
            "derived": derived,
            "dependencies": edges,
            "warnings": warnings,
            "unresolved": unresolved_items,
            "blocked": blocked,
        }
        out["observations_report"] = observations_report(out, fixture.observations)
        return out

    # ------------------------------------------------------------------
    def trait_config(self, spec_id: int, class_id: int, level: int, pairs: list[tuple[int, int]],
                     hero: int | None, *, simulate_load_path: bool = True) -> dict[str, Any]:
        """Build the Trinity config for fixture ``(entry, rank)`` pairs + hero sub-tree and evaluate it (no side effects).

        Mirrors ``Player::_LoadTraits`` (Player.cpp:28533-28550): granted entries first (rank 0), then the saved rank
        of each loaded entry.  Returns the engine, the config and every finding; the caller decides what is an error.
        """
        t = self.snap.traits
        trees, tree_info = self.snap.class_trait_trees(class_id)
        engine = TraitEngine(t, spec_id, class_id, level, trees)
        granted = engine.granted_entries()
        config: dict[tuple[int, int], dict[str, Any]] = {}
        for g in granted:
            config[(g["node"], g["entry"])] = {"node": g["node"], "entry": g["entry"], "rank": 0, "granted": g["granted"],
                                               "granted_by": g["granted_by"], "source": "granted"}
        errors: list[str] = []
        for entry_id, rank in pairs:
            if entry_id not in t["entry"]:
                errors.append(f"traits: TraitNodeEntry {entry_id} is not in the snapshot (TraitMgr::IsValidEntry TraitMgr.cpp:822-830)")
                continue
            nodes = [n for n in t["entry_nodes"].get(entry_id, []) if t["node"][n]["tree"] in trees]
            if len(nodes) != 1:
                other = sorted({t["node"][n]["tree"] for n in t["entry_nodes"].get(entry_id, [])})
                errors.append(f"traits: TraitNodeEntry {entry_id} sits in {len(nodes)} nodes of the class trees {trees} "
                              f"(entry trees {other}); the node cannot be derived (TraitMgr::GetTreesForConfig TraitMgr.cpp:338-354)")
                continue
            key = (nodes[0], entry_id)
            rec = config.setdefault(key, {"node": nodes[0], "entry": entry_id, "rank": 0, "granted": 0, "granted_by": [],
                                          "source": "fixture"})
            rec["rank"] = rank
            rec["source"] = "fixture" if not rec["granted"] else "fixture+granted"
        selected = {t["entry"][te["entry"]]["subtree"]: te for te in config.values()
                    if t["node"][te["node"]]["type"] == TRAIT_NODE_SUBTREE_SELECTION and te["rank"] + te["granted"] > 0}
        hero_info: dict[str, Any] = {"subtree_id": hero, "name": None, "selection_node_id": None, "selection_entry_id": None,
                                     "selection_source": None}
        if hero is not None:
            st = t["subtree"].get(hero)
            if st is None:
                errors.append(f"traits: hero_subtree_id {hero} is not a TraitSubTree")
            elif st["tree"] not in trees:
                errors.append(f"traits: hero subtree {hero} ({st['name']}) belongs to tree {st['tree']}, not the class trees {trees}")
            else:
                hero_info["name"] = st["name"]
                if hero in selected:
                    hero_info.update(selection_node_id=selected[hero]["node"], selection_entry_id=selected[hero]["entry"],
                                     selection_source=selected[hero]["source"])
                else:
                    cands = [(n, e) for n in t["tree_nodes"].get(st["tree"], [])
                             if t["node"][n]["type"] == TRAIT_NODE_SUBTREE_SELECTION
                             for e in t["node_entries"].get(n, []) if t["entry"][e]["subtree"] == hero]
                    usable = [(n, e) for n, e in cands if engine.node_meets(n, e, list(config.values()), engine.spent(list(config.values())))]
                    if len(usable) != 1:
                        errors.append(f"traits: hero subtree {hero} has {len(usable)} selectable SubTreeSelection entries "
                                      f"for spec {spec_id} ({usable} of {cands}); cannot derive the selection")
                    else:
                        n, e = usable[0]
                        config[(n, e)] = {"node": n, "entry": e, "rank": t["entry"][e]["max_ranks"], "granted": 0, "granted_by": [],
                                          "source": "derived from hero_subtree_id"}
                        hero_info.update(selection_node_id=n, selection_entry_id=e, selection_source="derived from hero_subtree_id")
            for other, te in selected.items():
                if other != hero:
                    errors.append(f"traits: SubTreeSelection entry {te['entry']} selects subtree {other} but hero_subtree_id is {hero}")
        elif selected:
            errors.append(f"traits: SubTreeSelection entries select subtrees {sorted(selected)} but hero_subtree_id is null")
        entries = list(config.values())
        strict = engine.validate(entries)
        applied_config, removed = engine.load_path(entries) if simulate_load_path else (entries, [])
        subtrees = engine.subtrees(entries)
        spent = engine.spent(entries)
        owned = engine.owned_currencies(entries)
        currency_rows = []
        for cur_id in sorted(set(spent) | set(owned)):
            s_total = spent.get(cur_id, {"total": 0})["total"]
            o = owned.get(cur_id)
            ctype = t["currency"][cur_id]["type"] if cur_id in t["currency"] else None
            row = {"currency_id": cur_id, "type": ctype, "spent": s_total, "owned": o["amount"] if o else None,
                   "undecided_sources": o["undecided_sources"] if o else [],
                   "checked": ctype == TRAIT_CURRENCY_TRAIT_SOURCED and s_total != 0}
            row["ok"] = (not row["checked"]) or (o is not None and o["amount"] >= s_total)
            row["unspent"] = (o["amount"] - s_total) if o is not None else None
            currency_rows.append(row)
        return {"engine": engine, "trees": trees, "tree_info": tree_info, "granted": granted, "config": entries,
                "errors": errors, "strict": strict, "applied_config": applied_config, "removed_on_load": removed,
                "subtrees": subtrees, "currencies": currency_rows, "hero": hero_info}

    def _compile_traits(self, fixture: Fixture, spec: Any, class_id: int, level: int, errors: list[str], warnings: list[str],
                        unresolved_items: list[dict[str, Any]]) -> dict[str, Any]:
        t = self.snap.traits
        spec_id = spec.spec_id
        hero = fixture.hero_subtree_id
        cfg = self.trait_config(spec_id, class_id, level, fixture.trait_entries, hero)
        engine: TraitEngine = cfg["engine"]
        errors.extend(cfg["errors"])
        for bad in cfg["strict"]:
            errors.append(f"traits: node {bad['node_id']} entry {bad['node_entry_id']} rank {bad['rank']} granted "
                          f"{bad['granted_ranks']}: {bad['result']} -- {bad['reason']} (TraitMgr::ValidateConfig)")
        for row in cfg["currencies"]:
            if not row["ok"]:
                errors.append(f"traits: currency {row['currency_id']} spent {row['spent']} > owned {row['owned']} at level {level} "
                              f"(ValidateConfig NotEnoughTalentsInPrimaryTree, TraitMgr.cpp:966-980)")
            if row["undecided_sources"]:
                unresolved_items.append(unresolved(
                    "/derived/traits/value/currencies", f"currency {row['currency_id']}: {row['undecided_sources']}",
                    "a character-state input (rewarded quests / achievements / currencies) in the fixture"))
            if row["checked"] and row["ok"] and row["unspent"]:
                warnings.append(f"traits: currency {row['currency_id']} has {row['unspent']} unspent point(s) at level {level} "
                                f"(allowed at load; requireSpendingAllCurrencies is false there, Player.cpp:28553)")
        if engine.undecidable:
            unresolved_items.append(unresolved(
                "/derived/traits", f"trait conditions need player state: {sorted(set(engine.undecidable))}",
                "a character-state input for the named quests / achievements / data elements"))
        active = {st for st, d in cfg["subtrees"].items() if d["active"]}
        resolved = []
        not_applied: list[dict[str, Any]] = []
        for te in cfg["config"]:
            n = t["node"][te["node"]]
            e = t["entry"][te["entry"]]
            d = t["definition"].get(e["definition"], {"spell": 0, "overrides": 0, "visible": 0})
            applied = (te["rank"] + te["granted"] > 0) and (not n["subtree"] or n["subtree"] in active)
            resolved.append({
                "node_entry_id": te["entry"], "node_id": te["node"], "tree_id": n["tree"], "node_type": n["type"],
                "subtree_id": n["subtree"], "selects_subtree_id": e["subtree"] if n["type"] == TRAIT_NODE_SUBTREE_SELECTION else None,
                "rank": te["rank"], "granted_ranks": te["granted"], "learned_rank": te["rank"] + te["granted"],
                "max_ranks": e["max_ranks"], "granted_by_conditions": sorted(set(te["granted_by"])),
                "definition_id": e["definition"], "spell_id": d["spell"], "overrides_spell_id": d["overrides"],
                "spell_name": self.snap.spell_names.get(d["spell"], "") if d["spell"] else "",
                "spell_in_snapshot": self.snap.spell_exists(d["spell"]) if d["spell"] else None,
                "effect_point_modifiers": t["definition_effect_points"].get(e["definition"], []),
                "source": te["source"], "applied": applied,
            })
            if not applied and te["rank"] + te["granted"] > 0 and n["subtree"]:
                not_applied.append({"node_entry_id": te["entry"], "subtree_id": n["subtree"],
                                    "subtree_name": t["subtree"][n["subtree"]]["name"], "rank": te["rank"],
                                    "granted_ranks": te["granted"]})
                if te["rank"]:
                    warnings.append(f"traits: entry {te['entry']} has purchased rank {te['rank']} in hero subtree {n['subtree']} "
                                    f"({t['subtree'][n['subtree']]['name']}), which is not active; not applied "
                                    f"(Player::ApplyTraitConfig -> TraitMgr::CanApplyTraitNode, Player.cpp:29387-29388)")
        resolved.sort(key=lambda r: (r["tree_id"], r["node_id"], r["node_entry_id"]))
        prov = [
            {"step": "class-trees", "consumer": "TraitMgr::GetTreesForConfig (TraitMgr.cpp:338-354) <- SkillLineXTraitTree x "
                                                "SkillLine.CategoryID 7 x SkillRaceClassInfo.ClassMask (TraitMgr.cpp:262-284)",
             "trees": cfg["trees"], **cfg["tree_info"],
             "note": "TraitTreeLoadout is not used to pick trees; TraitMgr's per-spec loadout map (TraitMgr.cpp:300-309) has no consumer"},
            {"step": "granted", "consumer": "TraitMgr::GetGrantedTraitEntriesForConfig (TraitMgr.cpp:766-820) on the empty config, "
                                            "Player::_LoadTraits (Player.cpp:28533-28534)",
             "granted": [{"node_id": g["node"], "node_entry_id": g["entry"], "granted_ranks": g["granted"],
                          "conditions": sorted(set(g["granted_by"]))} for g in cfg["granted"]]},
            {"step": "merge", "consumer": "Player::_LoadTraits saved Rank overwrites the granted entry's Rank (Player.cpp:28536-28550); "
                                          "fixture rank = purchased ranks, excluding granted ranks"},
            {"step": "validate", "consumer": "TraitMgr::ValidateConfig (TraitMgr.cpp:838-1015), strict: every failing entry is an error",
             "failures": cfg["strict"]},
            {"step": "trinity-load-path", "consumer": "ValidateConfig(removeInvalidEntries=true) at Player.cpp:28553 (TraitMgr.cpp:906-926)",
             "removed_or_zeroed": cfg["removed_on_load"],
             "note": "what the pinned Trinity would silently do with this fixture; reported, never applied by this compiler"},
            {"step": "hero-subtree", "consumer": "ValidateConfig sub-tree cache (TraitMgr.cpp:928-964); CanApplyTraitNode "
                                                 "(TraitMgr.cpp:1017-1031)", "hero": cfg["hero"]},
            {"step": "currency", "consumer": "FillOwnedCurrenciesMap (TraitMgr.cpp:409-473) vs spent (AddSpentCurrenciesForEntry "
                                             "TraitMgr.cpp:502-540); only TraitSourced currencies are checked (:971)",
             "currencies": cfg["currencies"]},
        ]
        failed = bool(cfg["errors"] or cfg["strict"] or not all(r["ok"] for r in cfg["currencies"]))
        return node(
            {"tree_ids": cfg["trees"], "class_skill_line": cfg["tree_info"], "hero_subtree": dict(cfg["hero"], active=hero in active if hero else False),
             "active_subtree_ids": sorted(active), "entries": resolved,
             "not_applied_inactive_subtree": not_applied,
             "not_applied_rule": "entries of an inactive hero sub-tree (typically the granted entry node of every selectable "
                                 "sub-tree) stay in the config but are not applied: TraitMgr::CanApplyTraitNode (TraitMgr.cpp:1017-1031)",
             "granted_entries": [{"node_id": g["node"], "node_entry_id": g["entry"], "granted_ranks": g["granted"]} for g in cfg["granted"]],
             "validation_failures": cfg["strict"], "trinity_load_path_removed": cfg["removed_on_load"],
             "currencies": cfg["currencies"], "loadout_string": fixture.raw["traits"].get("loadout_string"),
             "spell_ids": sorted({r["spell_id"] for r in resolved if r["spell_id"] and r["applied"]})},
            "unresolved" if failed else "db2-fact", prov, ["/fixture/traits", "/derived/identity"],
            evidence_note="values are db2 facts evaluated by a port of TraitMgr (trinity-consumer logic)")

    # ------------------------------------------------------------------
    def _compile_pvp(self, fixture: Fixture, spec: Any, class_id: int, level: int, errors: list[str], warnings: list[str]) -> dict[str, Any]:
        t = self.snap.traits
        enabled = fixture.options["enable_pvp_talents"]
        out: list[dict[str, Any]] = []
        for entry in fixture.pvp_traits:
            tid, slot = entry["pvp_talent_id"], entry["slot"]
            row = t["pvp_talent"].get(tid)
            if row is None:
                errors.append(f"pvp_traits: PvpTalent {tid} not in snapshot (Player::LearnPvpTalent Player.cpp:27686)")
                continue
            if row["spec"] and row["spec"] != spec.spec_id:
                errors.append(f"pvp_traits: PvpTalent {tid} belongs to spec {row['spec']}, not {spec.spec_id}")
            if slot >= MAX_PVP_TALENT_SLOTS:
                errors.append(f"pvp_traits: slot {slot} >= MAX_PVP_TALENT_SLOTS {MAX_PVP_TALENT_SLOTS} (DBCEnums.h:2795)")
            unlock = t["pvp_slot_unlock"].get(slot)
            if unlock is not None:
                need = unlock[1] if class_id == 6 else unlock[2] if class_id == 12 else unlock[0]
                if need > level:
                    errors.append(f"pvp_traits: slot {slot} requires level {need} (PvpTalentSlotUnlock; "
                                  f"DB2Manager::GetRequiredLevelForPvpTalentSlot, Player.cpp:27696)")
            mask = t["pvp_category"].get(row["category"])
            if mask is not None and not (mask & (1 << slot)):
                errors.append(f"pvp_traits: PvpTalent {tid} category {row['category']} does not allow slot {slot} "
                              f"(PvpTalentCategory.TalentSlotMask, Player.cpp:27699-27702)")
            if row["level"] > level:
                errors.append(f"pvp_traits: PvpTalent {tid} requires level {row['level']}")
            out.append({"pvp_talent_id": tid, "slot": slot, "spell_id": row["spell"], "spell_name": self.snap.spell_names.get(row["spell"], ""),
                        "overrides_spell_id": row["overrides"], "acquired": enabled,
                        "active": False})
        if fixture.pvp_traits and not enabled:
            warnings.append("pvp_traits listed but options.enable_pvp_talents is false: not acquired")
        return node(
            {"enabled": enabled, "entries": out,
             "note": "even when acquired, PvP talents are only toggled on inside PvP areas "
                     "(Player::TogglePvpTalents Player.cpp:27778; IsAreaThatActivatesPvpTalents :27856): encounter/host state"},
            "db2-fact", [{"step": "pvp-talents", "consumer": "Player::_LoadPvpTalents (Player.cpp:28467) -> AddPvpTalent (:27730)"}],
            ["/fixture/pvp_traits", "/derived/identity"])

    # ------------------------------------------------------------------
    def _compile_gear(self, fixture: Fixture, spec: Any, level: int, hero: int | None, errors: list[str],
                      warnings: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        snap = self.snap
        entries: list[LoadoutEntry] = []
        slot_of: dict[int, str] = {}
        per_slot: dict[str, dict[str, Any]] = {}
        for slot, raw in fixture.equipment.items():
            try:
                entry = LoadoutEntry.from_dict(dict(raw, slot=slot))
            except SourceError as error:
                errors.append(f"equipment.{slot}: {error}")
                continue
            if not snap.gear.items.exists(entry.item_id):
                errors.append(f"equipment.{slot}: item {entry.item_id} is not in the snapshot (ItemSparse/Item)")
                continue
            proto = snap.gear.items.get(entry.item_id)
            inv = proto.inventory_type
            rule = SLOT_RULES.get(inv)
            if rule is None or slot not in rule["slots"]:
                errors.append(f"equipment.{slot}: item {entry.item_id} has InventoryType {inv} "
                              f"({INVTYPE_NAMES.get(inv, '?')}) which cannot go in {slot} "
                              f"(Player::FindEquipSlot Player.cpp:{rule['line'] if rule else 9194})")
                continue
            # armour proficiency: Player::CanUseItem (Player.cpp:11282-11287) ArmorTypeMask & (1 << subclass), cloaks exempt
            if proto.class_id == 4 and inv != 16:
                klass = self.character_resolver(None).identity.klass(fixture.identity["class_id"])
                if not (klass.armor_type_mask & (1 << proto.subclass_id)):
                    errors.append(f"equipment.{slot}: armour subclass {proto.subclass_id} is outside ChrClasses.ArmorTypeMask "
                                  f"{klass.armor_type_mask:#x} (Player::CanUseItem Player.cpp:11282-11287)")
                    continue
            entries.append(entry)
            slot_of[len(entries) - 1] = slot
            per_slot[slot] = {"item_id": entry.item_id, "inventory_type": inv, "inventory_type_name": INVTYPE_NAMES.get(inv),
                              "class_id": proto.class_id, "subclass_id": proto.subclass_id,
                              "offhand_requires": rule.get("offhand_requires") if slot == "OFFHAND" else None,
                              "limit_category": proto.limit_category,
                              "requirements": {
                                  "allowable_class": int(proto.sparse["AllowableClass"]),
                                  "allowable_races": [int(proto.sparse["AllowableRaces_0"]), int(proto.sparse["AllowableRaces_1"])],
                                  "required_skill": int(proto.sparse["RequiredSkill"]),
                                  "required_skill_rank": int(proto.sparse["RequiredSkillRank"]),
                                  "required_ability": int(proto.sparse["RequiredAbility"]),
                                  "min_faction_id": int(proto.sparse["MinFactionID"]),
                                  "min_reputation": int(proto.sparse["MinReputation"]),
                                  "required_holiday": int(proto.sparse["RequiredHoliday"]),
                                  "unique_equippable": proto.has_flag(ITEM_FLAG_UNIQUE_EQUIPPABLE),
                                  "faction_horde": proto.has_flag(ITEM_FLAG2_FACTION_HORDE),
                                  "faction_alliance": proto.has_flag(ITEM_FLAG2_FACTION_ALLIANCE),
                                  "internal_item": proto.has_flag(ITEM_FLAG2_INTERNAL_ITEM),
                                  "always_allow_dual_wield": proto.has_flag(ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD),
                              }}
        loadout = Loadout(entries=entries, player_level=level, chr_spec_id=spec.spec_id, trait_sub_tree_id=hero)
        result = resolve_loadout(snap.gear, loadout)
        for i, item in enumerate(result.items):
            slot = slot_of[i]
            per_slot[slot].update({
                "name": item.name, "effective_item_level": item.effective_item_level, "quality": item.quality,
                "applied_bonus_lists": item.applied_bonus_lists,
                "stats": [{"stat_type": s.stat_type, "stat_name": s.stat_name, "value": s.final_value} for s in item.stats if s.final_value],
                "rating_contributions": item.rating_contributions, "armor": item.armor, "weapon": item.weapon,
                "gems": item.gems, "enchants": [{k: v for k, v in e.items() if k != "effects"} | {"effect_count": len(e.get("effects", []))}
                                                 for e in item.enchants],
                "item_set_id": item.item_set_id, "spell_roots": item.spell_roots, "warnings": item.warnings,
                "required_level": item.required_level,
                "required_level_unresolved": sorted({b.type for b in item.bonus_trace
                                                     if b.type in (BONUS_REQUIRED_LEVEL_CURVE, BONUS_SCALING_STAT_DISTRIBUTION_FIXED)}),
                "limit_category_bonus": sorted({b.values[0] for b in item.bonus_trace if b.type == BONUS_ITEM_LIMIT_CATEGORY}),
                "unhandled_bonuses": [b.to_dict() for b in item.unhandled_bonuses],
            })
        warnings.extend(f"gear: {w}" for w in result.warnings)
        value = {
            "slots": {slot: per_slot[slot] for slot in EQUIPMENT_SLOTS if slot in per_slot},
            "stat_totals": dict(sorted(result.stat_totals.items())),
            "rating_totals": dict(sorted(result.rating_totals.items())),
            "armor_total": sum(i.armor for i in result.items),
            "set_bonuses": result.set_bonuses,
            "spell_roots": sorted(result.spell_roots, key=lambda r: (r["spell_id"], r.get("item_id", 0), r.get("via", ""))),
            "armor_subclass_by_slot": {slot: per_slot[slot]["subclass_id"] for slot in ARMOR_SPECIALIZATION_SLOTS
                                       if slot in per_slot and per_slot[slot]["class_id"] == 4},
        }
        prov = [{"step": "resolve-loadout", "consumer": "gearing.loadout.resolve_loadout (gearing report §2-§9): Item::GetItemLevel, "
                                                        "Player::_ApplyItemBonuses, ApplyEnchantment, ItemSetSpell thresholds",
                 "item_count": len(result.items)},
                {"step": "set-thresholds", "consumer": "gearing.sets.SetEngine.satisfied_bonuses (Player::AddItemsSetItem/UpdateItemSetAuras)",
                 "chr_spec_id": spec.spec_id, "trait_sub_tree_id": hero, "satisfied": [(b["item_set_id"], b["threshold"]) for b in result.set_bonuses]},
                {"step": "crafted-stat-modifiers", "evidence_class": "unresolved",
                 "note": "ItemBonus type 25 MODIFIED_CRAFTING_STAT is NYI in Item.cpp (DBCEnums.h:1273); gearing reports it as "
                         "unhandled_bonuses per item; supplied bonus lists are still identity inputs"}]
        return node(value, "db2-fact" if not errors else "unresolved", prov, ["/fixture/equipment", "/derived/identity"]), per_slot

    # ------------------------------------------------------------------
    def _validate_offhand(self, gear_items: dict[str, Any], spells: dict[str, Any], errors: list[str]) -> None:
        """Off-hand gates of ``Player::FindEquipSlot`` (Player.cpp:9243-9262) and ``Player::CanEquipItem``
        (Player.cpp:10846-10871): one-hand weapons and two-handers need the gates; INVTYPE_WEAPONOFFHAND needs dual wield
        unless ITEM_FLAG3_ALWAYS_ALLOW_DUAL_WIELD; a 1H polearm never goes off-hand; nothing goes off-hand while
        ``IsTwoHandUsed`` (Player.cpp:13189-13199).  Titan's Grip subclass masks come from the granting spell's
        SpellEquippedItems (Player::CanTitanGrip Player.cpp:13125-13147)."""
        off = gear_items.get("OFFHAND")
        if not off:
            return
        gates = spells["value"]["gates"]
        need = off.get("offhand_requires")
        req = off.get("requirements", {})
        if off["inventory_type"] == INVTYPE_WEAPONOFFHAND and not req.get("always_allow_dual_wield"):
            need = "dual_wield"
        if need and not gates.get(need, {}).get("granted"):
            line = {"dual_wield": 9249, "titan_grip": 9261}[need]
            if off["inventory_type"] == INVTYPE_WEAPONOFFHAND:
                line = 10861
            errors.append(f"equipment.OFFHAND: item {off['item_id']} ({off['inventory_type_name']}) needs {need} "
                          f"(Player.cpp:{line}); no acquired spell has "
                          f"SPELL_EFFECT_{'DUAL_WIELD' if need == 'dual_wield' else 'TITAN_GRIP'}")
        if off["inventory_type"] == INVTYPE_WEAPON and off["class_id"] == 2 and off["subclass_id"] == ITEM_SUBCLASS_WEAPON_POLEARM:
            errors.append(f"equipment.OFFHAND: item {off['item_id']} is a one-hand polearm; never off-hand "
                          f"(Player::CanEquipItem EQUIP_ERR_WRONG_SLOT, Player.cpp:10852-10853)")
        tg_by = gates.get("titan_grip", {}).get("by", [])
        for slot in ("MAINHAND", "OFFHAND"):
            it = gear_items.get(slot)
            if it and it["inventory_type"] == INVTYPE_2HWEAPON and gates.get("titan_grip", {}).get("granted"):
                masks = [self.snap.equipped_items.get(sid) for sid in tg_by]
                for m in masks:
                    if m and m[0] == it["class_id"] and m[1] and not m[1] & (1 << it["subclass_id"]):
                        errors.append(f"equipment.{slot}: two-hander subclass {it['subclass_id']} is outside the Titan's Grip "
                                      f"subclass mask {m[1]:#x} (Player::CanTitanGrip Player.cpp:13125-13147)")
        main = gear_items.get("MAINHAND")
        if main:
            tg = gates.get("titan_grip", {}).get("granted")
            two_hand_used = ((main["inventory_type"] == INVTYPE_2HWEAPON and not tg)
                             or main["inventory_type"] == INVTYPE_RANGED
                             or (main["inventory_type"] == INVTYPE_RANGEDRIGHT and main["class_id"] == 2
                                 and main["subclass_id"] != ITEM_SUBCLASS_WEAPON_WAND))
            if two_hand_used:
                errors.append(f"equipment.OFFHAND: item {off['item_id']} cannot be equipped while MAINHAND {main['item_id']} "
                              f"({main['inventory_type_name']}) uses both hands (Player::IsTwoHandUsed Player.cpp:13189-13199; "
                              f"CanEquipItem EQUIP_ERR_2HANDED_EQUIPPED Player.cpp:10870-10871)")

    def _validate_item_requirements(self, race_id: int, class_id: int, level: int, gear_items: dict[str, Any],
                                    spells: dict[str, Any], errors: list[str], unresolved_items: list[dict[str, Any]]) -> None:
        """The rest of ``Player::CanUseItem`` (Player.cpp:11129-11234) and ``CanEquipUniqueItem`` (Player.cpp:27374-27437)
        that ``Player::_LoadInventory`` applies to every equipped item (``CanEquipItem`` Player.cpp:19240; a failing
        item is removed and mailed, Player.cpp:19295-19299).  Character-state requirements (reputation, profession skill,
        holiday) are reported unresolved, never assumed."""
        acquired = {a["spell_id"] for a in spells["value"]["acquired"]} | set(spells["value"]["gate_spell_ids"])
        default = self.snap.default_skill_spells(race_id, class_id, level)
        skill_values = {sk["skill_line"]: sk["skill_value"] for sk in default["skills"]}
        race_bit = self.snap.race_bits.get(race_id, -1)
        team = self.snap.race_alliance.get(race_id)
        seen_unique: dict[int, str] = {}
        limit_categories: dict[int, list[str]] = defaultdict(list)
        for slot, it in gear_items.items():
            req = it.get("requirements")
            if not req:
                continue
            where = f"equipment.{slot}: item {it['item_id']}"
            if req["internal_item"]:
                errors.append(f"{where} is ITEM_FLAG2_INTERNAL_ITEM (CanUseItem EQUIP_ERR_CANT_EQUIP_EVER, Player.cpp:11195-11196)")
            if (req["faction_horde"] and team != 1) or (req["faction_alliance"] and team != 0):
                errors.append(f"{where} is faction-restricted and race {race_id} has ChrRaces.Alliance {team} "
                              f"(CanUseItem Player.cpp:11198-11202; Player::TeamForRace Player.cpp:6376-6391)")
            if not (req["allowable_class"] & (1 << (class_id - 1))):
                errors.append(f"{where}: AllowableClass {req['allowable_class']:#x} excludes class {class_id} "
                              f"(CanUseItem EQUIP_ERR_CANT_EQUIP_EVER, Player.cpp:11204-11205)")
            races = (req["allowable_races"][0] & 0xFFFFFFFF) | ((req["allowable_races"][1] & 0xFFFFFFFF) << 32)
            if not (0 <= race_bit < 64 and races & (1 << race_bit)):
                errors.append(f"{where}: AllowableRaces {races:#x} excludes race {race_id} (bit {race_bit}) "
                              f"(CanUseItem Player.cpp:11204-11205)")
            if it.get("required_level_unresolved"):
                unresolved_items.append(unresolved(
                    f"/derived/gear/value/slots/{slot}/required_level",
                    f"item {it['item_id']} carries ItemBonus types {it['required_level_unresolved']} (required-level curve / "
                    f"fixed scaling level) that gearing does not evaluate (Item::GetRequiredLevel Item.cpp:2864-2874)",
                    "evaluate RequiredLevelCurve / ITEM_MODIFIER_TIMEWALKER_LEVEL for this item"))
            elif it.get("required_level") is not None and level < it["required_level"]:
                errors.append(f"{where} requires level {it['required_level']} (Item::GetRequiredLevel Item.cpp:2864-2874) but the "
                              f"character is level {level}: CanUseItem EQUIP_ERR_CANT_EQUIP_LEVEL_I (Player.cpp:11148-11149); "
                              f"_LoadInventory would mail the item (Player.cpp:19295-19299)")
            if req["required_ability"] and req["required_ability"] not in acquired:
                errors.append(f"{where} requires spell {req['required_ability']}, which is not acquired "
                              f"(CanUseItem EQUIP_ERR_PROFICIENCY_NEEDED, Player.cpp:11215-11216); supply it in learned_spells "
                              f"if the character learned it")
            if req["required_skill"]:
                value = skill_values.get(req["required_skill"])
                if value is None:
                    unresolved_items.append(unresolved(
                        f"/derived/gear/value/slots/{slot}",
                        f"item {it['item_id']} requires skill {req['required_skill']} rank {req['required_skill_rank']}, which is not "
                        f"a default skill of this identity (profession / learned skill values are character state; "
                        f"CanUseItem Player.cpp:11207-11213)",
                        "a saved-skill input (character_skills) in the fixture"))
                elif value < max(1, req["required_skill_rank"]):
                    errors.append(f"{where} requires skill {req['required_skill']} rank {req['required_skill_rank']}; the default "
                                  f"skill value is {value} (CanUseItem Player.cpp:11207-11213)")
            if req["min_faction_id"]:
                unresolved_items.append(unresolved(
                    f"/derived/gear/value/slots/{slot}",
                    f"item {it['item_id']} requires reputation rank {req['min_reputation']} with faction {req['min_faction_id']} "
                    f"(CanUseItem Player.cpp:11225-11226); reputation is character state (ReputationMgr::LoadFromDB, "
                    f"Player.cpp:18651)", "a reputation input in the fixture"))
            if req["required_holiday"]:
                unresolved_items.append(unresolved(
                    f"/derived/gear/value/slots/{slot}",
                    f"item {it['item_id']} requires holiday {req['required_holiday']} to be active (CanUseItem Player.cpp:11222-11223)",
                    "host calendar state"))
            if req["unique_equippable"]:
                if it["item_id"] in seen_unique:
                    errors.append(f"{where} is unique-equippable and already equipped in {seen_unique[it['item_id']]} "
                                  f"(CanEquipUniqueItem EQUIP_ERR_ITEM_UNIQUE_EQUIPPABLE, Player.cpp:27408-27414)")
                seen_unique.setdefault(it["item_id"], slot)
            for cat in set(it.get("limit_category_bonus") or []) | ({it["limit_category"]} if it.get("limit_category") else set()):
                limit_categories[cat].append(slot)
        for cat, slots in sorted(limit_categories.items()):
            if len(slots) > 1:
                unresolved_items.append(unresolved(
                    "/derived/gear", f"ItemLimitCategory {cat} is carried by {len(slots)} equipped items {slots}; the allowed "
                    f"quantity (GetItemLimitCategoryQuantity, ItemLimitCategoryCondition) is not evaluated "
                    f"(CanEquipUniqueItem Player.cpp:27416-27434)", "evaluate ItemLimitCategory / ItemLimitCategoryCondition"))

    def _validate_weapon_skills(self, race_id: int, class_id: int, level: int, gear_items: dict[str, Any],
                                spells: dict[str, Any], errors: list[str]) -> None:
        """``Player::CanUseItem`` weapon proficiency: ``GetSkillValue(proto->GetSkill()) == 0`` -> PROFICIENCY_NEEDED
        (Player.cpp:11279-11280).  Skills come from the default skills (value >= 1 for level-range skills) or from an
        acquired spell with SPELL_EFFECT_SKILL / SKILL_STEP naming the skill."""
        default = self.snap.default_skill_spells(race_id, class_id, level)
        learned = {sk["skill_line"] for sk in default["skills"] if sk["skill_value"] != 0}
        for a in spells["value"]["acquired"]:
            for _, eff, _, misc0, _ in self.snap.spell_effects.get(a["spell_id"], []):
                if eff in SPELL_EFFECTS_GRANT_SKILL:
                    learned.add(misc0)
        for slot in ("MAINHAND", "OFFHAND", "RANGED"):
            it = gear_items.get(slot)
            if not it or it["class_id"] != 2:
                continue
            sub = it["subclass_id"]
            skill = ITEM_WEAPON_SKILLS[sub] if 0 <= sub < len(ITEM_WEAPON_SKILLS) else 0
            if skill == 0 or skill not in learned:
                errors.append(f"equipment.{slot}: item {it['item_id']} weapon subclass {sub} needs skill {skill}, which "
                              f"race {race_id} class {class_id} does not have (Player::CanUseItem PROFICIENCY_NEEDED, "
                              f"Player.cpp:11279-11280; ItemTemplate::GetSkill ItemTemplate.cpp:97-140)")

    # ------------------------------------------------------------------
    def _compile_spells(self, fixture: Fixture, character: CharacterResolver, race: Any, klass: Any, spec: Any, level: int,
                        traits: dict[str, Any], gear: dict[str, Any], pvp: dict[str, Any], gear_items: dict[str, Any],
                        errors: list[str], warnings: list[str], unresolved_items: list[dict[str, Any]]) -> dict[str, Any]:
        snap = self.snap
        acq = character.acquisition
        roots: dict[int, list[dict[str, Any]]] = defaultdict(list)

        for s in acq.spec_spells(spec.spec_id, level):
            roots[s.spell_id].append({"kind": "spec-spell", "overrides_spell_id": s.overrides_spell_id, "spell_level": s.spell_level})
        for m in acq.mastery_spells(spec):
            roots[m["spell_id"]].append({"kind": "mastery-spell"})
        for r in traits["value"]["entries"]:
            if r["spell_id"] and r["applied"]:
                roots[r["spell_id"]].append({"kind": "class-trait", "node_entry_id": r["node_entry_id"],
                                             "rank": r["rank"] + r["granted_ranks"], "overrides_spell_id": r["overrides_spell_id"]})
        # default skills (Player::LearnDefaultSkills -> LearnSkillRewardedSpells): racial abilities are roots; every other
        # default-skill spell (class line, weapon/armour/language/generic lines) is reported separately
        # (dummy_semantics.scope convention include_class_skills=False) but still feeds the dual-wield / titan-grip gates
        default = snap.default_skill_spells(race.race_id, klass.class_id, level)
        default_skill: list[dict[str, Any]] = []
        default_ids: set[int] = set()
        for ds in default["spells"]:
            if ds["racial"]:
                roots[ds["spell_id"]].append({"kind": "race-gated-default-skill", "skill_line": ds["skill_line"],
                                              "skill_line_ability_id": ds["skill_line_ability_id"]})
                continue
            default_ids.add(ds["spell_id"])
            if fixture.options["include_class_skill_lines"]:
                default_skill.append({**ds, "name": snap.spell_names.get(ds["spell_id"], ""),
                                      "passive": snap.is_passive(ds["spell_id"]),
                                      "effects": sorted({e[1] for e in snap.spell_effects.get(ds["spell_id"], [])})})
        if default["undecided"]:
            unresolved_items.append(unresolved(
                "/derived/spells/value/default_skill_spells",
                f"{len(default['undecided'])} SkillLineAbility rows need conditions or world-DB skill tiers: "
                f"{sorted({(u['spell_id'], u['why'][:40]) for u in default['undecided']})}",
                "evaluate SpellInfo.ShowFutureSpellPlayerConditionID / conditions table (CONDITION_SOURCE_TYPE_SKILL_LINE_ABILITY) "
                "or supply skill_tiers", undecided=default["undecided"]))
        for r in gear["value"]["spell_roots"]:
            roots[r["spell_id"]].append({"kind": {"ItemEffect": "gear-item-effect", "SpellItemEnchantment": "gear-enchant",
                                                  "Gem": "gear-gem", "ItemSetSpell": "gear-set"}.get(r["via"], r["via"]),
                                         **{k: v for k, v in r.items() if k not in ("spell_id", "via")}})
        if pvp["value"]["enabled"]:
            for e in pvp["value"]["entries"]:
                roots[e["spell_id"]].append({"kind": "pvp-talent", "pvp_talent_id": e["pvp_talent_id"], "active_outside_pvp_area": False})
        # explicitly learned spells: character_spell rows (Player::_LoadSpells Player.cpp:18609 -> AddSpell loading=true);
        # identity input, because no automatic path reproduces them (e.g. 296087 via SkillLineAbility AcquireMethod 0)
        for sid in fixture.learned_spells:
            if not snap.spell_exists(sid):
                errors.append(f"learned_spells: spell {sid} is not in the snapshot (Player::AddSpell drops it, Player.cpp:2692-2705)")
                continue
            if sid in roots or sid in default_ids:
                warnings.append(f"learned_spells: spell {sid} is already acquired automatically "
                                f"({sorted({r['kind'] for r in roots.get(sid, [])}) or ['default-skill']}); listing it is redundant")
            roots[sid].append({"kind": "learned-spell", "source": "fixture learned_spells (character_spell)",
                               "consumer": "Player::_LoadSpells (Player.cpp:18609) -> Player::AddSpell"})

        # gates from acquired spells (SPELL_EFFECT_DUAL_WIELD / TITAN_GRIP): Spell::EffectDualWield SpellEffects.cpp:2237-2242,
        # Spell::EffectTitanGrip :4954-4960
        gates = {"dual_wield": {"granted": False, "by": []}, "titan_grip": {"granted": False, "by": []}}
        for sid in set(roots) | default_ids:
            for _, eff, _, _, _ in snap.spell_effects.get(sid, []):
                if eff == SPELL_EFFECT_DUAL_WIELD:
                    gates["dual_wield"]["granted"] = True
                    gates["dual_wield"]["by"].append(sid)
                elif eff == SPELL_EFFECT_TITAN_GRIP:
                    gates["titan_grip"]["granted"] = True
                    gates["titan_grip"]["by"].append(sid)
        for g in gates.values():
            g["by"] = sorted(set(g["by"]))

        acquired: list[dict[str, Any]] = []
        missing: list[int] = []
        for sid in sorted(roots):
            if not snap.spell_exists(sid):
                missing.append(sid)
                continue
            acquired.append(self._describe_spell(sid, roots[sid], gear_items, warnings))
        if missing:
            warnings.append(f"spells: {len(missing)} acquisition roots name SpellIDs absent from SpellName: {missing[:12]}"
                            f" (charstats report: 13 dangling SpecializationSpells references)")
        kinds = defaultdict(int)
        for a in acquired:
            for r in a["roots"]:
                kinds[r["kind"]] += 1
        passives_active = sorted(a["spell_id"] for a in acquired if a["passive"] and a["initial_aura"] == "active")
        value = {
            "count": len(acquired), "root_kind_counts": dict(sorted(kinds.items())), "gates": gates,
            "gate_spell_ids": sorted(default_ids),
            "acquired": acquired, "missing_from_snapshot": missing,
            "passives_active_at_prep": passives_active,
            "passives_gated": sorted(a["spell_id"] for a in acquired if a["passive"] and a["initial_aura"] != "active"),
            "proc_providers": sorted(a["spell_id"] for a in acquired if a["proc_provider"]),
            "marker_dummy_auras": sorted(a["spell_id"] for a in acquired if a["marker"] is not None),
            "default_skill_spells": {"count": len(default_skill), "reported_separately": True, "race_bit": default["race_bit"],
                                     "skills": default["skills"],
                                     "spells": sorted(default_skill, key=lambda d: (d["spell_id"], d["skill_line"])),
                                     "passives": sorted({d["spell_id"] for d in default_skill if d["passive"]})},
        }
        prov = [
            {"step": "spec-spells", "consumer": "Player::LearnSpecializationSpells (Player.cpp:30631-30648): SpellLevel <= level"},
            {"step": "mastery", "consumer": "Player::CanUseMastery / UpdateMastery (ChrSpecialization.MasterySpellID)"},
            {"step": "traits", "consumer": "Player::ApplyTraitEntry -> LearnSpell (Player.cpp:29406)"},
            {"step": "default-skills", "consumer": "ObjectMgr::LoadPlayerInfo skills (ObjectMgr.cpp:4059-4072) -> Player::LearnDefaultSkills "
                                                  "(Player.cpp:25259-25275) -> LearnDefaultSkill (:25277-25321) -> SetSkill (:5870) -> "
                                                  "LearnSkillRewardedSpells (:25391-25441); race bit = RaceMask::GetRaceBit (RaceMask.h:97-146)",
             "note": "race-gated abilities (non-empty SkillLineAbility race mask: racial lines, but also GENERIC / All Classes / "
                     "Riding / language rows) are roots; the rest is reported separately"},
            {"step": "gear-roots", "consumer": "Player::ApplyItemEquipSpell / ApplyEnchantment / ApplyEquipSpell (gearing.loadout spell_roots)"},
            {"step": "learned-spells", "consumer": "Player::_LoadSpells (Player.cpp:18609) -> AddSpell; identity input "
                                                   "/fixture/learned_spells", "spell_ids": fixture.learned_spells},
            {"step": "passive-cast-on-learn", "consumer": "Player::AddSpell (Player.cpp:2792-2794, :2916-2929) -> HandlePassiveSpellLearn (:3079-3104)"},
            {"step": "item-dependent-passives", "consumer": "Player::ApplyItemDependentAuras (Player.cpp:8280-8300) -> HasItemFitToSpellRequirements (:26157-26210)"},
            {"step": "proc-providers", "consumer": "SpellAuraOptions.ProcTypeMask (db2) + procs census player.provider_ids (proc report)"},
            {"step": "markers", "consumer": "dummy_semantics markers corpus (dummy report §markers)"},
        ]
        return node(value, "trinity-consumer", prov, ["/fixture/learned_spells", "/derived/traits", "/derived/gear",
                                                      "/derived/pvp_traits", "/derived/identity"])

    def _describe_spell(self, sid: int, roots: list[dict[str, Any]], gear_items: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        snap = self.snap
        passive = snap.is_passive(sid)
        initial = "n/a (not passive)"
        gate_reason = None
        if passive:
            initial = "active"
            stances = snap.shapeshift_masks.get(sid, 0)
            if stances and not snap.has_attr(sid, SPELL_ATTR2_ALLOW_NOT_SHAPESHIFTED):
                initial, gate_reason = "gated:shapeshift-form", f"SpellShapeshift.ShapeshiftMask {stances:#x} with no form at prep (HandlePassiveSpellLearn Player.cpp:3083-3085)"
            equipped = snap.equipped_items.get(sid)
            if equipped and equipped[0] >= 0 and any(e[1] in SPELL_EFFECTS_APPLY_AURA and e[2] for e in snap.spell_effects.get(sid, [])):
                fits, why = self._item_fit(sid, equipped, gear_items)
                if not fits:
                    initial, gate_reason = "gated:equipment", why
                else:
                    gate_reason = why
            state = snap.aura_restrictions.get(sid)
            if state and initial == "active":
                if HEALTH_AURA_STATES.get(state):
                    initial, gate_reason = "active-after-first-update", (
                        f"SpellAuraRestrictions.CasterAuraState {state} is health-derived and holds at full health: not cast on "
                        f"learn (HandlePassiveSpellLearn Player.cpp:3102-3103) but cast by Unit::ModifyAuraState "
                        f"(Unit.cpp:6078-6098) on the first Unit::Update (Unit.cpp:474-483)")
                elif state in HEALTH_AURA_STATES:
                    initial, gate_reason = "gated:caster-aura-state", (
                        f"SpellAuraRestrictions.CasterAuraState {state} is health-derived and false at full health (Unit.cpp:474-483)")
                else:
                    initial, gate_reason = "gated:caster-aura-state", (
                        f"SpellAuraRestrictions.CasterAuraState {state} needs a runtime aura state (HandlePassiveSpellLearn Player.cpp:3102-3103)")
        charge_cat = snap.charge_categories.get(sid)
        charges = snap.categories.get(charge_cat) if charge_cat else None
        cd = snap.cooldowns.get(sid, (0, 0))
        marker = snap.markers.get(sid)
        return {
            "spell_id": sid, "name": snap.spell_names.get(sid, ""), "roots": roots, "passive": passive,
            "initial_aura": initial, "gate_reason": gate_reason,
            "proc_provider": bool(snap.proc_masks.get(sid)) or sid in snap.proc_provider_ids,
            "proc_type_mask": snap.proc_masks.get(sid, 0),
            "in_proc_census_player_scope": sid in snap.proc_provider_ids,
            "marker": ({"marker_consumed": marker["marker_consumed"], "observer_kinds": marker["observer_kinds"],
                        "newer_than_trinity": marker["newer_than_trinity"]} if marker else None),
            "charges": ({"category": charge_cat, "max_charges": charges[0], "recovery_ms": charges[1]} if charges else None),
            "cooldown_ms": cd[0], "category_cooldown_ms": cd[1],
        }

    def _item_fits_spell(self, sid: int, equipped: tuple[int, int, int], it: dict[str, Any] | None) -> bool:
        """``Item::IsFitToSpellRequirements`` (Item.cpp:1477-1506) for an equipped item description."""
        if it is None:
            return False
        item_class, subclass_mask, inv_mask = equipped
        enchant = any(e[1] in SPELL_EFFECTS_ENCHANT_ITEM for e in self.snap.spell_effects.get(sid, []))
        if item_class != -1:
            if item_class != it["class_id"]:
                return False
            if subclass_mask and not subclass_mask & (1 << it["subclass_id"]):
                return False
        if enchant and inv_mask:
            if it["inventory_type"] == 13 and inv_mask & ((1 << 21) | (1 << 22)):
                return True
            if not inv_mask & (1 << it["inventory_type"]):
                return False
        return True

    def _item_fit(self, sid: int, equipped: tuple[int, int, int], gear_items: dict[str, Any]) -> tuple[bool, str]:
        """``Player::HasItemFitToSpellRequirements`` (Player.cpp:26158-26221); ``GetUseableItemByPos`` is taken as the
        equipped item (durability / broken state is not a preparation input)."""
        item_class, subclass_mask, _ = equipped
        if item_class < 0:
            return True, "no item requirement (Player.cpp:26160-26161)"
        if item_class == 2:
            for slot in ("MAINHAND", "OFFHAND"):
                if self._item_fits_spell(sid, equipped, gear_items.get(slot)):
                    return True, f"weapon requirement met by {slot} (Player.cpp:26168-26176)"
            return False, f"weapon requirement (subclass mask {subclass_mask:#x}) not met by MAINHAND/OFFHAND (Player.cpp:26168-26176)"
        if item_class == 4:
            if self.snap.has_attr(sid, SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES):
                for slot in ARMOR_SPECIALIZATION_SLOTS:
                    if not self._item_fits_spell(sid, equipped, gear_items.get(slot)):
                        return False, f"all-armour-slots requirement: {slot} does not fit subclass mask {subclass_mask:#x} (Player.cpp:26203-26213)"
                return True, "all eight armour slots fit (Player.cpp:26203-26213)"
            if subclass_mask & (1 << 6):
                if self._item_fits_spell(sid, equipped, gear_items.get("OFFHAND")):
                    return True, "shield requirement met by OFFHAND (Player.cpp:26183-26187)"
            for slot in EQUIPMENT_SLOTS[:EQUIPMENT_SLOTS.index("MAINHAND")]:
                if self._item_fits_spell(sid, equipped, gear_items.get(slot)):
                    return True, f"armour requirement met by {slot} (Player.cpp:26196-26200)"
            return False, f"armour requirement (subclass mask {subclass_mask:#x}) not met (Player.cpp:26180-26201)"
        return False, f"EquippedItemClass {item_class} is not handled; Trinity logs an error and returns false (Player.cpp:26215-26220)"

    # ------------------------------------------------------------------
    def _armor_specialization(self, character: CharacterResolver, spec: Any, spells: dict[str, Any], gear_items: dict[str, Any],
                              warnings: list[str]) -> dict[str, Any]:
        """Armour-specialization candidates among *every* acquired spell (roots + default-skill spells).

        The discovery rule is ``charstats.acquisition.AcquisitionEngine.armor_specializations`` unchanged (ATTR8
        REQUIRES_EQUIPPED_INV_TYPES + EquippedItemClass 4 + SPELL_AURA_MOD_TOTAL_STAT_PERCENTAGE); charstats only
        feeds it ``SpecializationSpells`` rows, which misses the classes whose passive arrives through the class
        skill line (Rogue 86092, Hunter 86538, Warlock 86091, Mage 89744, Priest 89745, Evoker 366524).
        """
        acquired = {a["spell_id"] for a in spells["value"]["acquired"]} | set(spells["value"]["gate_spell_ids"])
        specs = discover_armor_specializations(character.acquisition, spec.spec_id, acquired)
        via_spec = {a.spell_id for a in character.acquisition.armor_specializations(spec.spec_id)}
        by_slot = {slot: (gear_items[slot]["subclass_id"] if slot in gear_items and gear_items[slot]["class_id"] == 4 else None)
                   for slot in ARMOR_SPECIALIZATION_SLOTS}
        evaluated = []
        passing = []
        for a in specs:
            fits, why = self._item_fit(a.spell_id, self.snap.equipped_items[a.spell_id], gear_items)
            missing = [slot for slot, sub in by_slot.items() if sub is None or not (a.armor_subclass_mask & (1 << sub))]
            evaluated.append({"spell_id": a.spell_id, "name": self.snap.spell_names.get(a.spell_id, ""),
                              "armor_subclasses": list(a.armor_subclasses), "stats": list(a.stat_names),
                              "percent": a.percent, "gate_passed": fits, "gate_reason": why, "slots_failing": missing,
                              "via_specialization_spells": a.spell_id in via_spec})
            if fits:
                passing.append(a)
        if len(specs) > 1:
            warnings.append(f"armor specialization: {len(specs)} candidates {[a.spell_id for a in specs]}; charstats applies "
                            f"exactly one, so none is applied")
        applied = passing[0] if len(specs) == 1 and passing else None
        return node({"candidates": evaluated, "applied_spell_id": applied.spell_id if applied else None,
                     "equipped_armor_subclass_by_slot": by_slot},
                    "trinity-consumer",
                    [{"step": "armour-specialization-gate", "consumer": "Player::ApplyItemDependentAuras (Player.cpp:8280-8300) -> "
                                                                        "HasItemFitToSpellRequirements (Player.cpp:26203-26213) -> "
                                                                        "Item::IsFitToSpellRequirements (Item.cpp:1477-1506)",
                      "note": "closes charstats report blocker 5 (the equipped armour subclasses across the eight slots are known); "
                              "candidates come from the whole acquired spell set, not only SpecializationSpells"}],
                    ["/derived/gear", "/derived/spells"], _applied=applied)

    # ------------------------------------------------------------------
    def _compile_stats(self, character: CharacterResolver, race_id: int, class_id: int, level: int, spec: Any, gear: dict[str, Any],
                       armor_spec: dict[str, Any], base_table: BaseStatTable | None, base_source: dict[str, Any],
                       unresolved_items: list[dict[str, Any]], warnings: list[str]) -> dict[str, Any]:
        stats: dict[int, int] = {}
        for name, amount in gear["value"]["stat_totals"].items():
            mod = MOD_BY_NAME.get(name)
            if name in COMBINED_STAT_NAMES:
                mod = COMBINED_STAT_NAMES[name]
            if mod is None:
                continue
            for stat in ITEM_MOD_TO_STATS.get(mod, ()):
                stats[stat] = stats.get(stat, 0) + int(amount)
        contributions = Contributions(stats=stats, ratings={k: int(v) for k, v in gear["value"]["rating_totals"].items()},
                                      armor=int(gear["value"]["armor_total"]), source="gearing.loadout.resolve_loadout aggregate")
        applied_spec = armor_spec.pop("_applied", None)
        apply = applied_spec is not None
        prov = [{"step": "boundary", "consumer": "tools/gear_to_character.py gear_contributions (ItemModType -> Stats routing, "
                                                 "Player::_ApplyItemBonuses HandleStatFlatModifier)",
                 "handed_over": contributions.to_dict()}]
        if base_table is None:
            unresolved_items.append(unresolved(
                "/derived/stats", "base primary stats unsupplied (server_inputs.base_stats.source = unsupplied)",
                "supply player_classlevelstats + player_racestats (world-db corpus or inline with provenance)"))
            return node(None, "unresolved", prov + [{"step": "base-primary-stats", "result": "unsupplied"}],
                        ["/derived/gear", "/derived/armor_specialization", "/server_inputs/base_stats"])
        if race_id not in base_table.race_stat_modifiers:
            unresolved_items.append(unresolved(
                "/derived/stats", f"player_racestats has no row for race {race_id}; ObjectMgr::LoadPlayerInfo builds "
                                  f"PlayerInfo only for races in that table (ObjectMgr.cpp:4319-4330), so Trinity would have no level info at all",
                f"a player_racestats row for race {race_id} in the pinned TDB"))
            return node(None, "unresolved", prov + [{"step": "base-primary-stats", "result": "race-missing"}],
                        ["/derived/gear", "/derived/armor_specialization", "/server_inputs/base_stats"])
        try:
            with armor_specializations_as(character.acquisition, [applied_spec] if applied_spec else []):
                resolved = character.resolve(race_id, class_id, level, spec.spec_id, contributions, apply_armor_specialization=apply)
        except MissingBaseStats as error:
            known = sorted(base_table.class_level_stats.get(class_id, {}))
            unresolved_items.append(unresolved(
                "/derived/stats",
                f"player_classlevelstats has no row for class {class_id} level {level} "
                f"(corpus max level {max(known) if known else None}); nothing is interpolated",
                f"rows for class {class_id} level {level} in a TDB release; note the pinned Trinity would copy level "
                f"{max(known) if known else '?'} (ObjectMgr::LoadPlayerInfo gap-fill, ObjectMgr.cpp:4349-4356) -- "
                f"Trinity authoring, not Retail truth",
                evidence_class="unresolved", trinity_fallback={"consumer": "ObjectMgr::LoadPlayerInfo",
                                                                "coordinates": "src/server/game/Globals/ObjectMgr.cpp:4349-4356",
                                                                "behaviour": f"copies level {max(known) if known else '?'} row and logs an error"},
                detail=str(error)))
            return node(None, "unresolved", prov + [{"step": "base-primary-stats", "result": "level-row-missing",
                                                    "levels_present": [known[0], known[-1]] if known else []}],
                        ["/derived/gear", "/derived/armor_specialization", "/server_inputs/base_stats"])
        d = resolved.to_dict()
        warnings.extend(f"stats: {w}" for w in resolved.warnings)
        filled = getattr(base_table, "filled", {}).get((class_id, level))
        base_evidence = "world-db-fact"
        if filled is not None:
            base_evidence = "trinity-consumer(fill-rule)"
            row_level = filled.source_level          # the fill copies cell by cell (ObjectMgr.cpp:4349-4356): follow the chain
            while (class_id, row_level) in base_table.filled:
                row_level = base_table.filled[(class_id, row_level)].source_level
            prov.append({"step": "base-primary-stats", "result": "trinity-fill-rule",
                         "filled_from_level": filled.source_level, "originating_row_level": row_level,
                         "consumer": "ObjectMgr::LoadPlayerInfo gap fill (ObjectMgr.cpp:4349-4356), explicit fixture opt-in "
                                     "server_inputs.base_stats.fill_rule = trinity"})
            unresolved_items.append(unresolved(
                "/derived/stats/value/base",
                f"player_classlevelstats has no row for class {class_id} level {level}; the fixture opted into Trinity's gap "
                f"fill, which serves the level-{row_level} row unchanged (copied forward cell by cell; Trinity authoring). The Retail base "
                f"stats at level {level} are unresolved",
                "a TDB release with rows for this level, or naked Retail character-sheet observations",
                evidence_class="trinity-consumer(fill-rule)"))
        value = {
            "base": d["base"], "primary_stat_routing": d["primary_stat_routing"],
            "stats": {STAT_NAMES[i]: resolved.stat(i) for i in range(MAX_STATS)},
            "stat_stages": d["stats"],
            "max_health": d["max_health"], "health_from_stamina": d["health_from_stamina"], "hp_per_sta": d["hp_per_sta"],
            "armor": d["armor"], "bonus_armor": d["bonus_armor"],
            "attack_power": d["attack_power"], "ranged_attack_power": d["ranged_attack_power"],
            "spell_power": d["spell_power"], "ratings": d["ratings"], "mastery": d["mastery"], "mastery_value": d["mastery_value"],
            "armor_specialization_applied": d["armor_specialization_applied"],
            "create_mana": d["base"]["create_mana"],
            "base_stats_evidence": base_evidence,
            "caveat": d["caveat"],
        }
        return node(value, "trinity-consumer", prov + d["provenance"],
                    ["/derived/gear", "/derived/armor_specialization", "/server_inputs/base_stats", "/derived/identity"],
                    base_stats_source=base_source.get("source"), base_stats_fill_rule=base_source.get("fill_rule", "none"))

    # ------------------------------------------------------------------
    def _hook(self, spec: tuple[str, str], name: str, section: dict[str, Any], ctx: dict[str, Any],
              unresolved_items: list[dict[str, Any]], owner: str, derived_from: list[str]) -> dict[str, Any]:
        result, status = call_hook(spec, dict(ctx, section=section))
        if result is None:
            unresolved_items.append(unresolved(
                f"/derived/{name}", f"{owner} hook unavailable: {status}",
                f"{spec[0]} importable and exposing {spec[1]}(context: dict) -> dict"))
            return node({"fixture_section": section, "hook": status, "owner": owner}, "unresolved",
                        [{"step": "hook", "module": spec[0], "attribute": spec[1], "status": status}],
                        derived_from)
        return node({"fixture_section": section, "hook": status, "owner": owner, "result": result},
                    str(result.get("evidence_class", "structural-inference")),
                    [{"step": "hook", "module": spec[0], "attribute": spec[1], "status": status}],
                    derived_from)

    def _weapon(self, gear_items: dict[str, Any], ctx: dict[str, Any], unresolved_items: list[dict[str, Any]]) -> dict[str, Any]:
        facts = {}
        for slot in ("MAINHAND", "OFFHAND", "RANGED"):
            it = gear_items.get(slot)
            if it and it.get("weapon"):
                facts[slot] = {"item_id": it["item_id"], "inventory_type": it["inventory_type_name"],
                               "subclass_id": it["subclass_id"], **it["weapon"]}
        hook = self._hook(WEAPON_HOOK, "weapon", {"slots": facts}, ctx, unresolved_items, "Track B/C (weapon_combat)",
                          ["/derived/gear", "/derived/stats"])
        hook["value"]["gearing_weapon_facts"] = facts
        hook["provenance"].append({"step": "weapon-facts", "consumer": "gearing.resolver (Item::GetTemplate Delay / DmgVariance, "
                                                                        "ItemSparse) -- weapon dps/delay per item; swing model is Track B"})
        return hook

    # ------------------------------------------------------------------
    def _initial_state(self, class_id: int, character: CharacterResolver, stats: dict[str, Any], spells: dict[str, Any],
                       gear: dict[str, Any], base_table: BaseStatTable | None, unresolved_items: list[dict[str, Any]]) -> dict[str, Any]:
        snap = self.snap
        powers: dict[str, Any] = {}
        for p in snap.class_powers.get(class_id, []):
            pt = snap.power_types.get(p)
            if pt is None:
                continue
            if p == 0:
                try:
                    max_base = character.base_stats.base_mana(class_id, stats["value"]["base"]["level"] if stats["value"] else 0) if stats["value"] else None
                except CharacterSourceError:
                    max_base = None
                max_src = "Unit::GetCreateMana (StatSystem.cpp:92-93) <- BaseMp.txt via ObjectMgr::GetPlayerClassLevelInfo"
            else:
                max_base = pt["max_base"]
                max_src = "PowerType.MaxBasePower (Unit::GetCreatePowerValue StatSystem.cpp:95-96)"
            rule = INIT_STATS_FOR_LEVEL_POWER.get(p)
            init_value = rule[0] if rule and rule[1] < 3000 else "untouched (keeps its current value)"
            create_after = ("full" if pt["flags"] & POWER_TYPE_FLAG_SET_TO_MAX_ON_INITIAL_LOGIN else init_value)
            powers[POWER_NAMES.get(p, str(p))] = {
                "power_type": p, "max_base": max_base, "max_source": max_src,
                "max_consumer": "Player::UpdateMaxPower (StatSystem.cpp:331-345): (BASE_VALUE + create) * BASE_PCT + TOTAL_VALUE) * TOTAL_PCT, std::lroundf",
                "initial_on_init_stats_for_level": init_value,
                "initial_on_init_stats_for_level_coordinate": "Player.cpp:2487-2493",
                "initial_on_create": create_after,
                "initial_on_create_coordinate": "Player::Create: InitStatsForLevel (Player.cpp:490) then every PowerType with "
                                                "SetToMaxOnInitialLogIn is filled (Player.cpp:500-502)",
                "initial_on_level_up": "full" if pt["flags"] & POWER_TYPE_FLAG_SET_TO_MAX_ON_LEVEL_UP else "unchanged",
                "initial_on_level_up_coordinate": "Player::GiveLevel (Player.cpp:2254-2257, PowerTypeFlags::SetToMaxOnLevelUp)",
                "initial_on_login": ("0 (Player.cpp:18725)" if p == 8 else
                                     "saved value clamped to max (Player::LoadFromDB Player.cpp:18709-18723)"),
                "flags": pt["flags"], "flag_set_to_max_on_initial_login": bool(pt["flags"] & POWER_TYPE_FLAG_SET_TO_MAX_ON_INITIAL_LOGIN),
                "regen_peace": pt["regen_peace"], "regen_combat": pt["regen_combat"],
                "note": "regen values are db2 facts; regeneration itself is runtime state (encounter)",
            }
        max_health = stats["value"]["max_health"] if stats["value"] else None
        # SPELL_AURA_MOD_MAX_CHARGES (411) of passives active at preparation raise GetMaxCharges (SpellHistory.cpp:964-973)
        active = set(spells["value"]["passives_active_at_prep"]) | set(spells["value"]["default_skill_spells"]["passives"])
        bonus: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for sid in sorted(active):
            for cat, amount in snap.max_charges_auras.get(sid, []):
                bonus[cat].append({"spell_id": sid, "base_points": amount})
        charges = []
        for a in spells["value"]["acquired"]:
            if not (a["charges"] and a["charges"]["max_charges"]):
                continue
            cat = a["charges"]["category"]
            extra = bonus.get(cat, [])
            charges.append({"spell_id": a["spell_id"], "category": cat, "category_max_charges": a["charges"]["max_charges"],
                            "aura_bonus": extra,
                            "max_charges": a["charges"]["max_charges"] + int(sum(e["base_points"] for e in extra)),
                            "evidence_class": "structural-inference" if extra else "db2-fact"})
        value = {
            "health": {"value": max_health,
                       "rule": "full at the prepared maximum: an encounter convention, not a Trinity construction path. "
                               "Player::Create fills health before items are applied (Player.cpp:497-498), Unit::SetMaxHealth "
                               "never refills (Unit.cpp:10016-10030), Player::LoadFromDB restores the saved value clamped to max "
                               "(Player.cpp:18708); only Player::GiveLevel fills after stats (Player.cpp:2254)",
                       "login_note": "Player::LoadFromDB restores the saved value clamped to max (Player.cpp:18708); a saved 0 means dead (:18692-18694)",
                       "evidence_class": "structural-inference" if max_health is not None else "unresolved"},
            "powers": powers,
            "runes": ({"max": 6, "note": "LoadFromDB starts recharge for missing runes (Player.cpp:18727-18735)"} if 5 in snap.class_powers.get(class_id, []) else None),
            "cooldowns": {"rule": "all ready: SpellHistory::LoadFromDB only replays persisted rows (SpellHistory.cpp:147-179); "
                                  "a fresh history is empty. Items loaded by _LoadInventory use QuickEquipItem (no equip cooldown); "
                                  "equipping in game (Player::EquipItem Player.cpp:11567) starts a 30 s cooldown on on-use item "
                                  "spells unless ITEM_FLAG_NO_EQUIP_COOLDOWN (Player::ApplyEquipCooldown Player.cpp:25146-25186)",
                          "evidence_class": "trinity-consumer"},
            "charges": {"rule": "full: SpellHistory::GetMaxCharges = SpellCategory.MaxCharges + SPELL_AURA_MOD_MAX_CHARGES "
                                "(SpellHistory.cpp:964-973); no ChargeEntry consumed at start", "spells_with_charges": charges,
                        "evidence_class": "trinity-consumer"},
            "auras": {"passives_active": spells["value"]["passives_active_at_prep"],
                      "passives_gated": spells["value"]["passives_gated"],
                      "default_skill_passives": spells["value"]["default_skill_spells"]["passives"],
                      "set_bonus_spells": sorted(b["spell_id"] for b in gear["value"]["set_bonuses"]),
                      "marker_dummy_auras": spells["value"]["marker_dummy_auras"],
                      "rule": "passives cast on learn (Player::AddSpell Player.cpp:2792-2794 / :2916-2929); equipment-gated passives added by "
                              "ApplyItemDependentAuras (:8280); set spells by Player::ApplyEquipSpell",
                      "evidence_class": "trinity-consumer"},
            "swing": {"rule": "InitStatsForLevel sets BASE_ATTACK_TIME for every attack type (Player.cpp:2394-2395); the weapon delay "
                              "arrives when the item is applied; m_attackTimer starts at 0 (Unit.cpp:326) = ready",
                      "owner": "Track B (weapon_combat.swing)", "evidence_class": "trinity-consumer"},
            "controlled_units": {"owner": "Track A", "evidence_class": derived_evidence(None)},
            "rng": {"required_for_preparation": False, "note": "no preparation step consumes randomness; reserved options.rng"},
            "not_character_preparation": [
                "target / position / facing", "raid buffs, flasks, food, potions, augment runes (auras applied at pull)",
                "temporary weapon enchants (TEMP_ENCHANTMENT_SLOT, timed)", "bloodlust / pull timers",
                "PvP item levels (Player::IsUsingPvpItemLevels Player.h:2754; Player.cpp:30928)",
                "PvP talent activation (Player::TogglePvpTalents Player.cpp:27778)",
                "difficulty: NOT encounter state -- it is the ItemContext inside each equipment entry (identity input)",
                "MinItemLevel/MaxItemLevel (UnitData, area-based scaling Player::UpdateItemLevelAreaBasedScaling Player.cpp:25111)",
            ],
        }
        if max_health is None:
            unresolved_items.append(unresolved("/derived/initial_state/health", "max health needs base stats (stamina)", "resolve /derived/stats"))
        return node(value, "trinity-consumer",
                    [{"step": "initial-state", "consumer": "Player::InitStatsForLevel (Player.cpp:2323-2498), Player::Create (:387-, stats "
                                                           ":490-506), Player::GiveLevel (:2185-2257), "
                                                           "Player::LoadFromDB (:18690-18735), SpellHistory::LoadFromDB (SpellHistory.cpp:147-179)"}],
                    ["/derived/stats", "/derived/spells", "/derived/gear", "/derived/identity"])

    # ------------------------------------------------------------------
    def _blocked(self, spec: Any, spells: dict[str, Any], stats: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        mastery = stats["value"]["mastery"] if stats["value"] else None
        if mastery:
            for s in mastery.get("spells", []):
                for e in s.get("effects", []):
                    if e.get("effect_aura") == 4 and e.get("participates_in_mastery"):
                        out.append({"item": f"/derived/stats/value/mastery ({s['spell_id']})", "class": "script-dependent",
                                    "why": "mastery effect is SPELL_AURA_DUMMY (charstats report blocker 2: 17 script-dependent masteries)",
                                    "reopen_condition": "spell-specific rule from the dummy_semantics bindings for this mastery spell"})
                        break
        for a in spells["value"]["acquired"]:
            if a["marker"] and a["marker"]["newer_than_trinity"]:
                out.append({"item": f"/derived/spells ({a['spell_id']})", "class": "build-skew",
                            "why": "spell newer than Trinity's supported client build (dummy-corpora/build-skew.json)",
                            "reopen_condition": "Trinity revision supporting build 12.1.0.69497"})
        if mastery and GENERIC_MASTERY_SPELL in spells["value"]["gate_spell_ids"]:
            out.append({"item": "/derived/stats/value/mastery_value", "class": "model-gap",
                        "why": "spell 114585 'Mastery' (passive SPELL_AURA_MASTERY, BasePoints 8) is learned through default "
                               "skill 183 and cast on learn (Player.cpp:2916-2929); Player::UpdateMastery adds "
                               "GetTotalAuraModifier(SPELL_AURA_MASTERY) (StatSystem.cpp:549-550), charstats uses the rating "
                               "term only, so mastery_value is 8 below Trinity's (agent F differential)",
                        "reopen_condition": "charstats adds the SPELL_AURA_MASTERY aura term"})
        out.append({"item": "/derived/stats aura stages", "class": "unmodelled", "why": "percentage aura stages (BASE_PCT/TOTAL_PCT other than "
                    "armour specialization) come from the passive auras and are not applied (charstats report blocker 6)",
                    "reopen_condition": "generic SPELL_AURA_MOD_*_PERCENT stage evaluation over passives_active_at_prep"})
        return out


def derived_evidence(value: Any) -> str:
    return "unresolved" if value is None else "structural-inference"


# ---------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------

def observations_report(compiled: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    counts = defaultdict(int)
    for o in observations:
        try:
            expected = resolve_pointer(compiled, o["path"])
            found = True
        except KeyError as error:
            expected, found = None, False
            missing_token = str(error)
        observed = o["value"]
        if not found:
            status = "path-missing"
        elif expected is None:
            status = "expected-unresolved"
        elif isinstance(expected, (int, float)) and isinstance(observed, (int, float)) and not isinstance(expected, bool):
            tol = float(o.get("precision") or 0.0)
            status = "match" if abs(float(expected) - float(observed)) <= tol else "mismatch"
        else:
            status = "match" if expected == observed else "mismatch"
        counts[status] += 1
        rows.append({"path": o["path"], "observer": o["observer"], "build": o["build"], "expected": expected, "observed": observed,
                     "units": o.get("units"), "precision": o.get("precision"), "status": status,
                     "delta": (float(observed) - float(expected)) if status in ("match", "mismatch")
                     and isinstance(expected, (int, float)) and isinstance(observed, (int, float)) and not isinstance(expected, bool) else None,
                     "notes": o.get("notes")})
    return {"count": len(rows), "status_counts": dict(sorted(counts.items())), "rows": rows}


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------

def explain(compiled: dict[str, Any], pointer: str) -> dict[str, Any]:
    """Provenance chain for one derived value: the nearest enclosing node and its ancestors."""
    if not pointer.startswith("/derived"):
        raise SourceError(f"{pointer!r}: explain paths start with /derived")
    try:
        value = resolve_pointer(compiled, pointer)
    except KeyError as error:
        raise SourceError(f"{pointer!r}: no such path (missing token {error})") from None
    tokens = pointer.lstrip("/").split("/")
    owner_path = None
    for i in range(len(tokens), 0, -1):
        cand = "/" + "/".join(tokens[:i])
        obj = resolve_pointer(compiled, cand)
        if isinstance(obj, dict) and "provenance" in obj and "evidence_class" in obj:
            owner_path = cand
            break
    if owner_path is None:
        raise SourceError(f"{pointer!r}: no provenance-bearing node encloses this path")
    chain = []
    seen: set[str] = set()
    frontier = [owner_path]
    while frontier:
        p = frontier.pop(0)
        if p in seen:
            continue
        seen.add(p)
        try:
            obj = resolve_pointer(compiled, p)
        except KeyError:
            chain.append({"path": p, "kind": "input"})
            continue
        if isinstance(obj, dict) and "provenance" in obj:
            chain.append({"path": p, "evidence_class": obj["evidence_class"], "provenance": obj["provenance"],
                          "derived_from": obj.get("derived_from", [])})
            frontier.extend(obj.get("derived_from", []))
        else:
            chain.append({"path": p, "kind": "input"})
    unresolved_here = [u for u in compiled.get("unresolved", []) if u["item"].startswith(owner_path)]
    return {"path": pointer, "value": value, "owner_node": owner_path, "chain": chain, "unresolved": unresolved_here}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _compile_args(args: Any) -> tuple[Compiler, dict[str, Any], Fixture]:
    fixture = Fixture.from_file(args.fixture)
    compiler = Compiler(Snapshot(args.tables) if getattr(args, "tables", None) else Snapshot())
    compiled = compiler.compile(fixture, base_stats_file=Path(args.base_stats) if getattr(args, "base_stats", None) else None,
                                world_db_corpus=Path(args.world_db_corpus) if getattr(args, "world_db_corpus", None) else None)
    return compiler, compiled, fixture


def summary_lines(compiled: dict[str, Any]) -> list[str]:
    d = compiled["derived"]
    v = compiled["validation"]
    lines = [f"validation: {'ok' if v['ok'] else 'FAILED'} errors={len(v['errors'])} warnings={len(v['warnings'])}"]
    lines.extend(f"  error: {e}" for e in v["errors"])
    if "identity" in d:
        i = d["identity"]["value"]
        lines.append(f"identity: {i['race']['name']} {i['class']['name']} / {i['spec']['name']} level {i['level']}")
    if "traits" in d:
        t = d["traits"]["value"]
        lines.append(f"traits: {len(t['entries'])} entries in trees {t['tree_ids']} hero={t['hero_subtree']} spells={len(t['spell_ids'])}")
    if "gear" in d:
        g = d["gear"]["value"]
        lines.append(f"gear: {len(g['slots'])} slots stats={g['stat_totals']} ratings={g['rating_totals']} sets={[(b['item_set_id'], b['threshold']) for b in g['set_bonuses']]}")
    if "stats" in d:
        s = d["stats"]
        if s["value"]:
            lines.append(f"stats: {s['value']['stats']} health={s['value']['max_health']} ap={s['value']['attack_power']} sp={s['value']['spell_power']} "
                         f"mastery={s['value']['mastery_value']} armorspec={s['value']['armor_specialization_applied'] is not None}")
        else:
            lines.append(f"stats: unresolved ({s['provenance'][-1]})")
    if "spells" in d:
        sp = d["spells"]["value"]
        lines.append(f"spells: {sp['count']} acquired {sp['root_kind_counts']} passives_active={len(sp['passives_active_at_prep'])} "
                     f"gated={len(sp['passives_gated'])} procs={len(sp['proc_providers'])} markers={len(sp['marker_dummy_auras'])} gates={sp['gates']}")
    lines.append(f"unresolved: {len(compiled['unresolved'])} blocked: {len(compiled['blocked'])} "
                 f"observations: {compiled['observations_report']['status_counts']}")
    for u in compiled["unresolved"]:
        lines.append(f"  unresolved {u['item']}: {u['reason']}")
    return lines


def cmd_compile(args: Any) -> int:
    _, compiled, _ = _compile_args(args)
    if args.output:
        Path(args.output).write_text(canonical_json(compiled), encoding="utf-8")
    if args.json and not args.output:
        print(canonical_json(compiled), end="")
    else:
        print("\n".join(summary_lines(compiled)))
        if args.output:
            print(f"wrote {args.output}")
    return 0 if compiled["validation"]["ok"] else 1


def cmd_explain(args: Any) -> int:
    _, compiled, _ = _compile_args(args)
    try:
        out = explain(compiled, args.path)
    except SourceError as error:
        print(error)
        return 1
    if args.json:
        print(canonical_json(out), end="")
        return 0
    print(f"{out['path']} = {json.dumps(out['value'], default=str)[:400]}")
    print(f"owner node: {out['owner_node']}")
    for link in out["chain"]:
        if "provenance" in link:
            print(f"- {link['path']} [{link['evidence_class']}] derived_from={link['derived_from']}")
            for step in link["provenance"]:
                print(f"    {json.dumps(step, default=str)[:300]}")
        else:
            print(f"- {link['path']} (input)")
    for u in out["unresolved"]:
        print(f"unresolved: {u}")
    return 0


def compile_all(compiler: Compiler, fixtures_dir: Path = FIXTURES_DIR, *, world_db_corpus: Path | None = None,
                write: bool = True) -> dict[str, Any]:
    """Compile every ``fixtures/*.json`` (not ``*.compiled.json``) and write the index with sha256s."""
    entries = []
    for path in sorted(fixtures_dir.glob("*.json")):
        if path.name.endswith(".compiled.json") or path.name == "index.json":
            continue
        fixture = Fixture.from_file(path)
        compiled = compiler.compile(fixture, world_db_corpus=world_db_corpus)
        out = path.with_name(path.stem + ".compiled.json")
        text = canonical_json(compiled)
        if write:
            out.write_text(text, encoding="utf-8")
        entries.append({
            "name": path.stem, "fixture": path.name, "fixture_sha256": fixture.sha256,
            "compiled": out.name, "compiled_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "identity": fixture.identity, "validation_ok": compiled["validation"]["ok"],
            "errors": len(compiled["validation"]["errors"]), "warnings": len(compiled["validation"]["warnings"]),
            "unresolved": [u["item"] for u in compiled["unresolved"]],
            "level_90_base_stat_gap": any("level" in u["reason"] and u["item"] == "/derived/stats" for u in compiled["unresolved"]),
            "base_stats_fill_rule": compiled["provenance"]["base_stats_source"].get("fill_rule", "none"),
            "base_stats_filled": any(u["item"] == "/derived/stats/value/base" for u in compiled["unresolved"]),
        })
    index = {"provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "world_database": TDB_RELEASE,
                            "generator": "python3 character_prep.py compile-all", "fixture_count": len(entries)},
             "fixtures": entries}
    if write:
        (fixtures_dir / "index.json").write_text(canonical_json(index), encoding="utf-8")
    return index


def cmd_compile_all(args: Any) -> int:
    compiler = Compiler(Snapshot(args.tables) if getattr(args, "tables", None) else Snapshot())
    index = compile_all(compiler, Path(args.fixtures_dir) if args.fixtures_dir else FIXTURES_DIR,
                        world_db_corpus=Path(args.world_db_corpus) if args.world_db_corpus else None)
    for e in index["fixtures"]:
        print(f"{e['name']}: {'ok' if e['validation_ok'] else 'FAILED'} errors={e['errors']} warnings={e['warnings']} "
              f"unresolved={e['unresolved']} lvl90gap={e['level_90_base_stat_gap']} filled={e['base_stats_filled']}")
    return 0 if all(e["validation_ok"] for e in index["fixtures"]) else 1


def register(subparsers: Any) -> None:
    c = subparsers.add_parser("compile", help="compile a fixture into prepared Player state (fail closed)")
    c.add_argument("--fixture", required=True)
    c.add_argument("--base-stats", default=None, help="charstats BaseStatTable JSON (overrides the fixture section)")
    c.add_argument("--world-db-corpus", default=None, help="world-db-corpora/player-base-stats.json")
    c.add_argument("--tables", default=None)
    c.add_argument("--json", action="store_true")
    c.add_argument("--output", default=None)
    c.set_defaults(func=cmd_compile)
    a = subparsers.add_parser("compile-all", help="compile every fixture under character-prep-corpora/fixtures and write index.json")
    a.add_argument("--fixtures-dir", default=None)
    a.add_argument("--world-db-corpus", default=None)
    a.add_argument("--tables", default=None)
    a.set_defaults(func=cmd_compile_all)
    e = subparsers.add_parser("explain", help="print the provenance chain of one derived value")
    e.add_argument("--fixture", required=True)
    e.add_argument("--path", required=True, help="JSON pointer, e.g. /derived/stats/value/stats/Strength")
    e.add_argument("--base-stats", default=None)
    e.add_argument("--world-db-corpus", default=None)
    e.add_argument("--tables", default=None)
    e.add_argument("--json", action="store_true")
    e.set_defaults(func=cmd_explain)
