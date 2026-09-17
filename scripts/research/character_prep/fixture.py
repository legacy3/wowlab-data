"""The neutral character fixture: format, schema and fail-closed validation.

A fixture describes one character's *source identities* -- never a simulator
answer.  It is provider independent: a Retail export, an addon string, a SimC
profile or a hand-written JSON all reduce to the same sections.  Every field is
classified as one of

``identity``   must be supplied; nothing can derive it (race, level, the set of
               selected trait entries, which ItemID sits in which slot ...);
``server``     a Trinity-authored world-database fact the snapshot lacks (base
               primary stats); supplied by reference to a corpus, never silently;
``derived``    never accepted from the caller -- the compiler derives it from
               the snapshot and the existing research (item level, stats, sets,
               reachable spells ...).  Such values live only in observations;
``provenance`` opaque bookkeeping the compiler carries through untouched;
``hook``       an extensible section owned by another track (controlled units).

Validation here is structural and fails closed: unknown keys anywhere, a wrong
type, a missing required identity input, a duplicate JSON key or a slot name
outside Trinity's ``EquipmentSlots`` enum are all errors.  Snapshot-dependent
checks (does the ItemID exist, is the trait entry in the spec's tree ...) belong
to :mod:`character_prep.compiler`.

Slot vocabulary: ``EquipmentSlots`` in
``src/server/game/Entities/Player/Player.h:729-749``.  Which inventory types a
slot accepts mirrors ``Player::FindEquipSlot``
(``src/server/game/Entities/Player/Player.cpp:9194-9290``).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import SNAPSHOT_BUILD, SourceError

FIXTURE_VERSION = 1


class FixtureError(SourceError):
    """The fixture is malformed; the message lists every problem found."""


# ---------------------------------------------------------------------------
# slot vocabulary (Trinity EquipmentSlots, Player.h:729-749)
# ---------------------------------------------------------------------------

EQUIPMENT_SLOTS: tuple[str, ...] = (
    "HEAD", "NECK", "SHOULDERS", "BODY", "CHEST", "WAIST", "LEGS", "FEET", "WRISTS",
    "HANDS", "FINGER1", "FINGER2", "TRINKET1", "TRINKET2", "BACK", "MAINHAND",
    "OFFHAND", "RANGED", "TABARD",
)
SLOT_INDEX = {name: i for i, name in enumerate(EQUIPMENT_SLOTS)}

#: The eight armour slots ``HasItemFitToSpellRequirements`` demands when
#: ``SPELL_ATTR8_REQUIRES_EQUIPPED_INV_TYPES`` is set (Player.cpp:26204).
ARMOR_SPECIALIZATION_SLOTS: tuple[str, ...] = (
    "HEAD", "SHOULDERS", "CHEST", "WAIST", "LEGS", "FEET", "WRISTS", "HANDS")

#: InventoryType -> slots it may occupy, per ``Player::FindEquipSlot``.  A
#: ``dual_wield`` / ``titan_grip`` tag marks the conditional off-hand cases.
#: Values are InventoryType ids (``gearing.enums.INVTYPE_*``).
SLOT_RULES: dict[int, dict[str, Any]] = {
    1: {"slots": ("HEAD",), "line": 9199},
    2: {"slots": ("NECK",), "line": 9202},
    3: {"slots": ("SHOULDERS",), "line": 9205},
    4: {"slots": ("BODY",), "line": 9208},
    5: {"slots": ("CHEST",), "line": 9211},
    20: {"slots": ("CHEST",), "line": 9214},
    6: {"slots": ("WAIST",), "line": 9217},
    7: {"slots": ("LEGS",), "line": 9220},
    8: {"slots": ("FEET",), "line": 9223},
    9: {"slots": ("WRISTS",), "line": 9226},
    10: {"slots": ("HANDS",), "line": 9229},
    11: {"slots": ("FINGER1", "FINGER2"), "line": 9232},
    12: {"slots": ("TRINKET1", "TRINKET2"), "line": 9236},
    16: {"slots": ("BACK",), "line": 9240},
    13: {"slots": ("MAINHAND", "OFFHAND"), "offhand_requires": "dual_wield", "line": 9243},
    14: {"slots": ("OFFHAND",), "line": 9253},
    15: {"slots": ("MAINHAND",), "line": 9256},
    17: {"slots": ("MAINHAND", "OFFHAND"), "offhand_requires": "titan_grip", "line": 9259},
    19: {"slots": ("TABARD",), "line": 9264},
    21: {"slots": ("MAINHAND",), "line": 9267},
    22: {"slots": ("OFFHAND",), "line": 9270},
    23: {"slots": ("OFFHAND",), "line": 9273},
    26: {"slots": ("MAINHAND",), "line": 9276},
}


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def _f(type_: str, cls: str, description: str, *, required: bool = False, **extra: Any) -> dict[str, Any]:
    out = {"type": type_, "class": cls, "required": required, "description": description}
    out.update(extra)
    return out


EQUIPMENT_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "One equipped item instance; identical to gearing.loadout.LoadoutEntry "
                   "(the slot is the object key).",
    "properties": {
        "item_id": _f("integer", "identity", "ItemSparse.ID", required=True),
        "context": _f("integer", "identity", "ItemContext (difficulty/source); default 0 = none. "
                      "Carries the difficulty *inside* the loadout, so difficulty is never encounter state."),
        "bonus_list_ids": _f("array<integer>", "identity", "explicit ItemBonusListIDs (upgrade steps, "
                             "sockets, crafted-stat choices are all bonus lists)"),
        "gems": _f("array<integer|object>", "identity", "gem item ids by socket index, or "
                   "{socket_index, gem_item_id, bonus_list_ids}"),
        "enchant_ids": _f("array<integer>", "identity", "SpellItemEnchantment ids (permanent enchants)"),
        "mythic_plus_keystone_level": _f("integer|null", "identity", "keystone level for M+ contexts"),
        "pvp_tier": _f("integer|null", "identity", "PvP tier for PvP contexts"),
        "label": _f("string|null", "provenance", "free text"),
        "auto_bonus_lists": _f("boolean", "identity", "let gearing derive the context bonus lists (default true)"),
    },
}

OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "An externally observed value attached to a derived-state path.  Designed so a "
                   "future Retail client / WASM oracle can be compared without changing the format.",
    "properties": {
        "observer": _f("string", "provenance", "who observed: retail-client | trinity-probe | core | addon | other",
                       required=True),
        "build": _f("string", "provenance", "client/server build the observation was taken on", required=True),
        "captured_at": _f("string|null", "provenance", "ISO-8601 timestamp"),
        "path": _f("string", "provenance", "JSON pointer into the compiled output, e.g. /derived/stats/value/Strength",
                   required=True),
        "value": _f("any", "provenance", "observed value", required=True),
        "units": _f("string|null", "provenance", "points | percent | fraction | ms | seconds | count | id"),
        "precision": _f("number|null", "provenance", "absolute tolerance used by the comparison (default exact)"),
        "notes": _f("string|null", "provenance", "free text"),
    },
}

FIXTURE_SCHEMA: dict[str, Any] = {
    "$comment": "JSON-Schema-like description validated by character_prep.fixture (stdlib only).",
    "fixture_version": FIXTURE_VERSION,
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fixture_version": _f("integer", "provenance", f"must equal {FIXTURE_VERSION}", required=True),
        "provenance": {
            "type": "object", "class": "provenance", "required": True,
            "description": "where the description came from; carried through untouched",
            "properties": {
                "snapshot_build": _f("string", "provenance", "data snapshot the ids refer to", required=True),
                "source_kind": _f("string", "provenance",
                                  "synthetic | retail-export | addon-export | simc-profile | armory | manual",
                                  required=True),
                "captured_at": _f("string|null", "provenance", "ISO-8601 capture time"),
                "external_ids": _f("object", "provenance", "opaque ids in the source system"),
                "notes": _f("array<string>", "provenance", "free text"),
                "generator": _f("string|null", "provenance", "tool that produced the fixture"),
            },
        },
        "identity": {
            "type": "object", "class": "identity", "required": True,
            "properties": {
                "race_id": _f("integer", "identity", "ChrRaces.ID", required=True),
                "class_id": _f("integer", "identity", "ChrClasses.ID", required=True),
                "spec_id": _f("integer", "identity", "ChrSpecialization.ID", required=True),
                "level": _f("integer", "identity", "player level (1..MaxLevel)", required=True),
            },
        },
        "traits": {
            "type": "object", "class": "identity", "required": True,
            "properties": {
                "entries": {
                    "type": "array<object>", "class": "identity", "required": True,
                    "description": "selected (TraitNodeEntryID, rank) pairs; the tree and node are derived",
                    "items": {"node_entry_id": _f("integer", "identity", "TraitNodeEntry.ID", required=True),
                              "rank": _f("integer", "identity", "purchased ranks (1..MaxRanks)", required=True)},
                },
                "hero_subtree_id": _f("integer|null", "identity", "TraitSubTree.ID of the active hero tree"),
                "loadout_string": _f("string|null", "provenance",
                                     "the addon/Blizzard export string, kept opaque; never decoded here"),
            },
        },
        "pvp_traits": {
            "type": "array<object>", "class": "identity", "required": False,
            "description": "optional PvP talents; only acquired when options.enable_pvp_talents",
            "items": {"pvp_talent_id": _f("integer", "identity", "PvpTalent.ID", required=True),
                      "slot": _f("integer", "identity", "0..MAX_PVP_TALENT_SLOTS-1", required=True)},
        },
        "learned_spells": {
            "type": "array<integer>", "class": "identity", "required": False,
            "description": "SpellIDs the character learned through a path no automatic grant reproduces (trainer, quest, "
                           "item, SkillLineAbility AcquireMethod 0 'Learned', e.g. 296087 Dual Wield for an Arms warrior); "
                           "character_spell rows restored by Player::_LoadSpells (Player.cpp:18609) -> AddSpell. "
                           "Spells every character of the identity gets automatically are derived, never listed here",
        },
        "equipment": {
            "type": "object", "class": "identity", "required": True,
            "description": "slot name (Trinity EquipmentSlots) -> equipment entry",
            "keys": list(EQUIPMENT_SLOTS),
            "values": EQUIPMENT_ENTRY_SCHEMA,
        },
        "server_inputs": {
            "type": "object", "class": "server", "required": True,
            "description": "Trinity-authored world-database facts, always explicit",
            "properties": {
                "base_stats": {
                    "type": "object", "class": "server", "required": True,
                    "properties": {
                        "source": _f("string", "server", "world-db-corpus | inline | unsupplied", required=True),
                        "path": _f("string|null", "server", "corpus path (world-db-corpus)"),
                        "class_level_stats": _f("object", "server", "inline: {class: {level: [str,agi,sta,int,spi]}}"),
                        "race_stat_modifiers": _f("object", "server", "inline: {race: [str,agi,sta,int,spi]}"),
                        "provenance": _f("string|null", "server", "where inline values came from"),
                        "fill_rule": _f("string|null", "server",
                                        "none (default) | trinity: explicit opt-in to ObjectMgr::LoadPlayerInfo's gap fill "
                                        "(ObjectMgr.cpp:4349-4356, a copy of the previous level's cell) for world-db-corpus "
                                        "base stats; filled cells are Trinity authoring, never Retail truth"),
                    },
                },
            },
        },
        "controlled_units": {
            "type": "object", "class": "hook", "required": False,
            "description": "extensible; each key is an object owned by the controlled-unit research "
                           "(hunter pet family/spec/creature, warlock active demon ...)",
        },
        "options": {
            "type": "object", "class": "identity", "required": False,
            "properties": {
                "enable_pvp_talents": _f("boolean", "identity", "acquire pvp_traits (default false)"),
                "include_class_skill_lines": _f("boolean", "identity",
                                                "list the non-race-gated default-skill spells (class line, weapon / armour / "
                                                "language / generic lines) in derived.spells (default true); reporting only -- "
                                                "they always feed the dual-wield / titan-grip / proficiency / armour-spec checks"),
                "rng": _f("null", "provenance", "reserved; preparation needs no RNG identity"),
            },
        },
        "observations": {
            "type": "array<object>", "class": "provenance", "required": False,
            "items": OBSERVATION_SCHEMA,
        },
    },
}

TOP_LEVEL_KEYS = tuple(FIXTURE_SCHEMA["properties"])
PROVENANCE_KEYS = tuple(FIXTURE_SCHEMA["properties"]["provenance"]["properties"])
IDENTITY_KEYS = tuple(FIXTURE_SCHEMA["properties"]["identity"]["properties"])
TRAITS_KEYS = tuple(FIXTURE_SCHEMA["properties"]["traits"]["properties"])
EQUIPMENT_ENTRY_KEYS = tuple(EQUIPMENT_ENTRY_SCHEMA["properties"])
BASE_STATS_KEYS = tuple(FIXTURE_SCHEMA["properties"]["server_inputs"]["properties"]["base_stats"]["properties"])
OPTIONS_KEYS = tuple(FIXTURE_SCHEMA["properties"]["options"]["properties"])
OBSERVATION_KEYS = tuple(OBSERVATION_SCHEMA["properties"])
BASE_STATS_SOURCES = ("world-db-corpus", "inline", "unsupplied")
BASE_STATS_FILL_RULES = ("none", "trinity")
OBSERVERS = ("retail-client", "trinity-probe", "core", "addon", "other")
SOURCE_KINDS = ("synthetic", "retail-export", "addon-export", "simc-profile", "armory", "manual")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_optional_str(obj: dict, key: str, where: str, errors: list[str]) -> None:
    if obj.get(key) is not None and not isinstance(obj[key], str):
        errors.append(f"{where}.{key}: expected a string or null, got {obj[key]!r}")


def _check_keys(obj: Any, allowed: tuple[str, ...], where: str, errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object, got {type(obj).__name__}")
        return False
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        errors.append(f"{where}: unknown keys {unknown}; refusing to guess what they mean")
    return True


def _check_int(obj: dict, key: str, where: str, errors: list[str], *, required: bool,
               minimum: int | None = None, nullable: bool = False) -> None:
    if key not in obj:
        if required:
            errors.append(f"{where}.{key}: required identity input is missing")
        return
    v = obj[key]
    if v is None and nullable:
        return
    if not _is_int(v):
        errors.append(f"{where}.{key}: expected an integer, got {v!r}")
        return
    if minimum is not None and v < minimum:
        errors.append(f"{where}.{key}: {v} is below the minimum {minimum}")


def _check_int_list(obj: dict, key: str, where: str, errors: list[str]) -> None:
    if key not in obj:
        return
    v = obj[key]
    if not isinstance(v, list) or not all(_is_int(x) for x in v):
        errors.append(f"{where}.{key}: expected a list of integers, got {v!r}")


def validate_equipment_entry(raw: Any, where: str, errors: list[str]) -> None:
    if not _check_keys(raw, EQUIPMENT_ENTRY_KEYS, where, errors):
        return
    _check_int(raw, "item_id", where, errors, required=True, minimum=1)
    _check_int(raw, "context", where, errors, required=False, minimum=0)
    _check_int_list(raw, "bonus_list_ids", where, errors)
    _check_int_list(raw, "enchant_ids", where, errors)
    _check_int(raw, "mythic_plus_keystone_level", where, errors, required=False, nullable=True)
    _check_int(raw, "pvp_tier", where, errors, required=False, nullable=True)
    if "label" in raw and raw["label"] is not None and not isinstance(raw["label"], str):
        errors.append(f"{where}.label: expected a string")
    if "auto_bonus_lists" in raw and not isinstance(raw["auto_bonus_lists"], bool):
        errors.append(f"{where}.auto_bonus_lists: expected a boolean")
    gems = raw.get("gems", [])
    if not isinstance(gems, list):
        errors.append(f"{where}.gems: expected a list")
    else:
        for i, gem in enumerate(gems):
            if _is_int(gem):
                continue
            if isinstance(gem, dict):
                if _check_keys(gem, ("socket_index", "gem_item_id", "bonus_list_ids"), f"{where}.gems[{i}]", errors):
                    _check_int(gem, "gem_item_id", f"{where}.gems[{i}]", errors, required=True, minimum=1)
                    _check_int(gem, "socket_index", f"{where}.gems[{i}]", errors, required=False, minimum=0)
                    _check_int_list(gem, "bonus_list_ids", f"{where}.gems[{i}]", errors)
            else:
                errors.append(f"{where}.gems[{i}]: expected an integer or object, got {gem!r}")


def validate_observation(raw: Any, where: str, errors: list[str]) -> None:
    if not _check_keys(raw, OBSERVATION_KEYS, where, errors):
        return
    for key in ("observer", "build", "path"):
        if key not in raw or not isinstance(raw[key], str) or not raw[key]:
            errors.append(f"{where}.{key}: required non-empty string")
    if "value" not in raw:
        errors.append(f"{where}.value: required")
    if raw.get("observer") not in OBSERVERS and isinstance(raw.get("observer"), str):
        errors.append(f"{where}.observer: {raw['observer']!r} is not one of {list(OBSERVERS)}")
    path = raw.get("path")
    if isinstance(path, str) and (not path.startswith("/derived/") or path == "/derived/"):
        errors.append(f"{where}.path: {path!r} must be a JSON pointer under /derived/ naming a node")
    if raw.get("precision") is not None and not (_is_number(raw["precision"]) and raw["precision"] >= 0):
        errors.append(f"{where}.precision: expected a finite non-negative number, got {raw['precision']!r}")
    if isinstance(raw.get("value"), float) and not math.isfinite(raw["value"]):
        errors.append(f"{where}.value: non-finite number")
    for key in ("captured_at", "units", "notes"):
        _check_optional_str(raw, key, where, errors)


def _validate_inline_base_stats(base: dict, errors: list[str]) -> None:
    where = "$.server_inputs.base_stats"
    _check_optional_str(base, "provenance", where, errors)
    cls = base.get("class_level_stats")
    if not isinstance(cls, dict) or not cls:
        errors.append(f"{where}.class_level_stats: required non-empty object for inline base stats")
        cls = {}

    def row_ok(v: Any) -> bool:
        return isinstance(v, list) and len(v) == 5 and all(_is_int(x) for x in v)

    for c, levels in cls.items():
        if not str(c).isdigit() or not isinstance(levels, dict):
            errors.append(f"{where}.class_level_stats.{c}: expected class id -> {{level: [str, agi, sta, int, spi]}}")
            continue
        for lvl, v in levels.items():
            if not str(lvl).isdigit() or not row_ok(v):
                errors.append(f"{where}.class_level_stats.{c}.{lvl}: expected level -> five integers")
    mods = base.get("race_stat_modifiers", {})
    if not isinstance(mods, dict):
        errors.append(f"{where}.race_stat_modifiers: expected race id -> five integers")
    else:
        for r, v in mods.items():
            if not str(r).isdigit() or not row_ok(v):
                errors.append(f"{where}.race_stat_modifiers.{r}: expected race id -> five integers")


def validate_fixture(raw: Any) -> list[str]:
    """Structural validation; returns every problem (empty list = valid)."""
    errors: list[str] = []
    if not _check_keys(raw, TOP_LEVEL_KEYS, "$", errors):
        return errors
    for key in ("fixture_version", "provenance", "identity", "traits", "equipment", "server_inputs"):
        if key not in raw:
            errors.append(f"$.{key}: required section is missing")
    if "fixture_version" in raw and (not _is_int(raw["fixture_version"]) or raw["fixture_version"] != FIXTURE_VERSION):
        errors.append(f"$.fixture_version: {raw['fixture_version']!r} != {FIXTURE_VERSION}")

    prov = raw.get("provenance")
    if prov is not None and _check_keys(prov, PROVENANCE_KEYS, "$.provenance", errors):
        for key in ("snapshot_build", "source_kind"):
            if not isinstance(prov.get(key), str) or not prov.get(key):
                errors.append(f"$.provenance.{key}: required non-empty string")
        if isinstance(prov.get("source_kind"), str) and prov["source_kind"] not in SOURCE_KINDS:
            errors.append(f"$.provenance.source_kind: {prov['source_kind']!r} is not one of {list(SOURCE_KINDS)}")
        if "notes" in prov and (not isinstance(prov["notes"], list)
                                or not all(isinstance(n, str) for n in prov["notes"])):
            errors.append("$.provenance.notes: expected a list of strings")
        if "external_ids" in prov and not isinstance(prov["external_ids"], dict):
            errors.append("$.provenance.external_ids: expected an object")
        for key in ("captured_at", "generator"):
            _check_optional_str(prov, key, "$.provenance", errors)

    ident = raw.get("identity")
    if ident is not None and _check_keys(ident, IDENTITY_KEYS, "$.identity", errors):
        for key in IDENTITY_KEYS:
            _check_int(ident, key, "$.identity", errors, required=True, minimum=1)

    traits = raw.get("traits")
    if traits is not None and _check_keys(traits, TRAITS_KEYS, "$.traits", errors):
        entries = traits.get("entries")
        if not isinstance(entries, list):
            errors.append("$.traits.entries: required list of {node_entry_id, rank}")
        else:
            seen: set[int] = set()
            for i, entry in enumerate(entries):
                where = f"$.traits.entries[{i}]"
                if _check_keys(entry, ("node_entry_id", "rank"), where, errors):
                    _check_int(entry, "node_entry_id", where, errors, required=True, minimum=1)
                    _check_int(entry, "rank", where, errors, required=True, minimum=1)
                    if _is_int(entry.get("node_entry_id")):
                        if entry["node_entry_id"] in seen:
                            errors.append(f"{where}: node_entry_id {entry['node_entry_id']} listed twice")
                        seen.add(entry["node_entry_id"])
        _check_int(traits, "hero_subtree_id", "$.traits", errors, required=False, nullable=True, minimum=1)
        if traits.get("loadout_string") is not None and not isinstance(traits["loadout_string"], str):
            errors.append("$.traits.loadout_string: expected a string (kept opaque)")

    if "pvp_traits" in raw:
        pvp = raw["pvp_traits"]
        if not isinstance(pvp, list):
            errors.append("$.pvp_traits: expected a list")
        else:
            slots: set[int] = set()
            for i, entry in enumerate(pvp):
                where = f"$.pvp_traits[{i}]"
                if _check_keys(entry, ("pvp_talent_id", "slot"), where, errors):
                    _check_int(entry, "pvp_talent_id", where, errors, required=True, minimum=1)
                    _check_int(entry, "slot", where, errors, required=True, minimum=0)
                    if _is_int(entry.get("slot")):
                        if entry["slot"] in slots:
                            errors.append(f"{where}: slot {entry['slot']} used twice")
                        slots.add(entry["slot"])

    if "learned_spells" in raw:
        learned = raw["learned_spells"]
        if not isinstance(learned, list):
            errors.append("$.learned_spells: expected a list of SpellIDs")
        else:
            seen_spells: set[int] = set()
            for i, spell in enumerate(learned):
                if not _is_int(spell) or spell < 1:
                    errors.append(f"$.learned_spells[{i}]: expected a positive integer SpellID, got {spell!r}")
                elif spell in seen_spells:
                    errors.append(f"$.learned_spells[{i}]: SpellID {spell} listed twice")
                else:
                    seen_spells.add(spell)

    equipment = raw.get("equipment")
    if equipment is not None:
        if not isinstance(equipment, dict):
            errors.append("$.equipment: expected an object keyed by slot name")
        else:
            for slot, entry in equipment.items():
                if slot not in SLOT_INDEX:
                    errors.append(f"$.equipment.{slot}: not a Trinity EquipmentSlots name {list(EQUIPMENT_SLOTS)}")
                    continue
                validate_equipment_entry(entry, f"$.equipment.{slot}", errors)

    server = raw.get("server_inputs")
    if server is not None and _check_keys(server, ("base_stats",), "$.server_inputs", errors):
        base = server.get("base_stats")
        if base is None:
            errors.append("$.server_inputs.base_stats: required (use {\"source\": \"unsupplied\"} to say so explicitly)")
        elif _check_keys(base, BASE_STATS_KEYS, "$.server_inputs.base_stats", errors):
            src = base.get("source")
            if src not in BASE_STATS_SOURCES:
                errors.append(f"$.server_inputs.base_stats.source: {src!r} is not one of {list(BASE_STATS_SOURCES)}")
            if src == "inline":
                _validate_inline_base_stats(base, errors)
            path = base.get("path")
            if path is not None:
                if src != "world-db-corpus":
                    errors.append(f"$.server_inputs.base_stats.path: only meaningful for source world-db-corpus, not {src!r}")
                elif not isinstance(path, str) or not path:
                    errors.append("$.server_inputs.base_stats.path: expected a non-empty string")
                elif path.startswith(("/", "\\")) or ".." in Path(path).parts or ":" in path:
                    errors.append(f"$.server_inputs.base_stats.path: {path!r} must be a repository-relative path "
                                  "(no absolute path, no '..'); host paths are never fixture inputs")
            fill = base.get("fill_rule")
            if fill is not None:
                if fill not in BASE_STATS_FILL_RULES:
                    errors.append(f"$.server_inputs.base_stats.fill_rule: {fill!r} is not one of {list(BASE_STATS_FILL_RULES)}")
                elif fill != "none" and src != "world-db-corpus":
                    errors.append("$.server_inputs.base_stats.fill_rule: the Trinity fill rule applies to "
                                  "source world-db-corpus only")

    if "controlled_units" in raw:
        cu = raw["controlled_units"]
        if not isinstance(cu, dict) or not all(isinstance(v, dict) for v in cu.values()):
            errors.append("$.controlled_units: expected an object whose values are objects")

    if "options" in raw and _check_keys(raw["options"], OPTIONS_KEYS, "$.options", errors):
        for key in ("enable_pvp_talents", "include_class_skill_lines"):
            if key in raw["options"] and not isinstance(raw["options"][key], bool):
                errors.append(f"$.options.{key}: expected a boolean")
        if raw["options"].get("rng") is not None:
            errors.append("$.options.rng: reserved; preparation requires no RNG identity (must be null)")

    if "observations" in raw:
        obs = raw["observations"]
        if not isinstance(obs, list):
            errors.append("$.observations: expected a list")
        else:
            for i, o in enumerate(obs):
                validate_observation(o, f"$.observations[{i}]", errors)
    return errors


# ---------------------------------------------------------------------------
# parsed fixture
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise FixtureError(f"duplicate JSON key {key!r}; a slot or section listed twice is ambiguous")
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise FixtureError(f"non-finite JSON constant {name} is not a valid fixture value")


def load_fixture_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        raise FixtureError(f"not valid JSON: {error}") from None


@dataclass
class Fixture:
    raw: dict[str, Any]
    path: Path | None = None
    sha256: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], path: Path | None = None, text: str | None = None) -> "Fixture":
        errors = validate_fixture(raw)
        if errors:
            raise FixtureError("fixture rejected:\n  " + "\n  ".join(errors))
        digest = hashlib.sha256((text if text is not None else canonical_json(raw)).encode("utf-8")).hexdigest()
        return cls(raw=raw, path=path, sha256=digest)

    @classmethod
    def from_file(cls, path: Path | str) -> "Fixture":
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        return cls.from_dict(load_fixture_json(text), path=path, text=text)

    # -- accessors ---------------------------------------------------------
    @property
    def identity(self) -> dict[str, int]:
        return self.raw["identity"]

    @property
    def level(self) -> int:
        return int(self.raw["identity"]["level"])

    @property
    def trait_entries(self) -> list[tuple[int, int]]:
        return [(int(e["node_entry_id"]), int(e["rank"])) for e in self.raw["traits"]["entries"]]

    @property
    def hero_subtree_id(self) -> int | None:
        v = self.raw["traits"].get("hero_subtree_id")
        return int(v) if v else None

    @property
    def pvp_traits(self) -> list[dict[str, int]]:
        return list(self.raw.get("pvp_traits", []))

    @property
    def learned_spells(self) -> list[int]:
        return [int(s) for s in self.raw.get("learned_spells", [])]

    @property
    def equipment(self) -> dict[str, dict[str, Any]]:
        return {slot: self.raw["equipment"][slot] for slot in EQUIPMENT_SLOTS if slot in self.raw["equipment"]}

    @property
    def base_stats_spec(self) -> dict[str, Any]:
        return self.raw["server_inputs"]["base_stats"]

    @property
    def controlled_units(self) -> dict[str, dict[str, Any]]:
        return dict(self.raw.get("controlled_units", {}))

    @property
    def options(self) -> dict[str, Any]:
        opts = {"enable_pvp_talents": False, "include_class_skill_lines": True, "rng": None}
        opts.update(self.raw.get("options", {}))
        return opts

    @property
    def observations(self) -> list[dict[str, Any]]:
        return list(self.raw.get("observations", []))

    @property
    def provenance(self) -> dict[str, Any]:
        return dict(self.raw["provenance"])


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, indent=1, sort_keys=False, ensure_ascii=False) + "\n"


def schema_document() -> dict[str, Any]:
    """The committed ``fixture-schema.json``."""
    classes = {
        "identity": "must be supplied by the caller; nothing derives it",
        "server": "Trinity-authored world-database fact absent from the DB2 snapshot; supplied by explicit "
                  "reference or inline with provenance; never defaulted",
        "derived": "never accepted as input; the compiler derives it (see input-inventory.json)",
        "provenance": "opaque bookkeeping carried through untouched",
        "hook": "extensible section whose contents another research track owns",
    }
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "fixture_version": FIXTURE_VERSION,
                       "generator": "python3 character_prep.py schema"},
        "field_classes": classes,
        "equipment_slots": list(EQUIPMENT_SLOTS),
        "slot_rules": {str(k): v for k, v in SLOT_RULES.items()},
        "schema": FIXTURE_SCHEMA,
    }


def empty_fixture(race_id: int, class_id: int, spec_id: int, level: int, *, source_kind: str = "manual") -> dict[str, Any]:
    return {
        "fixture_version": FIXTURE_VERSION,
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "source_kind": source_kind, "notes": []},
        "identity": {"race_id": race_id, "class_id": class_id, "spec_id": spec_id, "level": level},
        "traits": {"entries": [], "hero_subtree_id": None},
        "learned_spells": [],
        "equipment": {},
        "server_inputs": {"base_stats": {"source": "unsupplied"}},
        "controlled_units": {},
        "options": {},
        "observations": [],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_validate(args: Any) -> int:
    text = Path(args.fixture).read_text(encoding="utf-8")
    try:
        raw = load_fixture_json(text)
    except FixtureError as error:
        print(f"{args.fixture}: {error}")
        return 1
    errors = validate_fixture(raw)
    if errors:
        print(f"{args.fixture}: {len(errors)} error(s)")
        for e in errors:
            print(f"  {e}")
        return 1
    print(f"{args.fixture}: valid fixture_version={raw['fixture_version']} "
          f"slots={len(raw['equipment'])} trait_entries={len(raw['traits']['entries'])}")
    return 0


def cmd_schema(args: Any) -> int:
    from . import CORPORA
    doc = schema_document()
    out = Path(args.output) if args.output else CORPORA / "fixture-schema.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(canonical_json(doc), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("validate", help="structurally validate a fixture (fail closed)")
    p.add_argument("--fixture", required=True)
    p.set_defaults(func=cmd_validate)
    s = subparsers.add_parser("schema", help="write character-prep-corpora/fixture-schema.json")
    s.add_argument("--output", default=None)
    s.set_defaults(func=cmd_schema)
