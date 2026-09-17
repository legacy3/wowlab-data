"""Population of controlled units reachable from current-player scope.

One record per reachable ``SpellEffect`` row that creates or controls a Unit:

* ``SPELL_EFFECT_SUMMON`` (28), ``SUMMON_PET`` (56), ``TAMECREATURE`` (55),
  ``CREATE_TAMED_PET`` (153) -- unit creating;
* ``DISMISS_PET`` (102) and the ``EffectNULL`` pet effects (135/168/188/199/260) -- lifecycle;
* ``SPELL_AURA_MOD_CHARM`` (6), ``MOD_POSSESS`` (2), ``AOE_CHARM`` (177),
  ``MOD_POSSESS_PET`` (378) -- control auras.

Object summons (``SUMMON_OBJECT_*``) are excluded: they do not create a Unit.

Each record is joined to its ``SummonProperties`` row, the creature entry
(``EffectMiscValue_0``), the pinned-TDB ``creature_template`` (when the
world-db corpus is present), the client-side ``Creature.db2`` row (identity
fallback for entries the 12.0.x TDB does not know), the spec attribution of
``dummy_semantics.scope.Scope`` and the branch ``Spell::EffectSummonType`` /
``Map::SummonCreature`` take (:mod:`controlled_units.vocabulary`).  The
category is *derived from the branch*, never from a name.

Scopes: ``default`` is the brief's convention (``include_class_skills=False``);
``class-skill`` records are the extra rows of the extended scope (class skill
lines carry the mount/companion/pet-family summons) and are reported
separately.  ``script-cast`` records are summon spells cast only by a bound
script in player scope (structural index, not authored data);
``serverside`` records are ``serverside_spell`` summon effects (none is
referenced from player scope in the pinned overlay).
"""

from __future__ import annotations

import csv
import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dummy_semantics import CORPORA as DUMMY_CORPORA
from dummy_semantics.loaders import Bundle
from dummy_semantics.scope import Scope
from procs.enums import aura_name, effect_name
from procs.spells import DIFFICULTY_NONE

from . import CORPORA, ROOT, SNAPSHOT_BUILD, TABLES, TDB_RELEASE, TRINITY_COMMIT, WORLD_DB_CORPORA
from .vocabulary import (CONTROL_AURAS, SPELL_EFFECT_APPLY_AURA, UNIT_CREATING_EFFECTS, UNIT_LIFECYCLE_EFFECTS,
                         SummonProps, effect_branch)

CREATURE_TEMPLATES = WORLD_DB_CORPORA / "creature-templates.json"
#: creature_template_difficulty columns carried into population records (identity/level only; stat columns are Track B)
DIFFICULTY_KEYS = ("DifficultyID", "LevelScalingDeltaMin", "LevelScalingDeltaMax", "ContentTuningID", "CreatureDifficultyID", "TypeFlags", "TypeFlags2", "StaticFlags1")
#: SharedDefines.h:5012-5019 / 158-164 -- inputs of Pet::IsPermanentPetFor (Pet.cpp:1657-1678)
CREATURE_TYPE_DEMON, CREATURE_TYPE_ELEMENTAL, CREATURE_TYPE_UNDEAD = 3, 4, 6
PERMANENT_PET_TYPE_BY_CLASS = {9: CREATURE_TYPE_DEMON, 6: CREATURE_TYPE_UNDEAD, 8: CREATURE_TYPE_ELEMENTAL}


def is_permanent_pet_for(owner_class: int, pet_type: str, creature_type: int | None) -> bool | None:
    """Mirrors ``Pet::IsPermanentPetFor`` (Pet.cpp:1657-1678).  ``None`` when the template type is unknown."""
    if pet_type == "HUNTER_PET":
        return True
    if pet_type != "SUMMON_PET":
        return False
    want = PERMANENT_PET_TYPE_BY_CLASS.get(owner_class)
    if want is None:
        return False
    if creature_type is None:
        return None
    return creature_type == want
FAMILIES = DUMMY_CORPORA / "families.json"
POPULATION_EFFECTS = UNIT_CREATING_EFFECTS | UNIT_LIFECYCLE_EFFECTS
CATEGORY_ORDER = (
    "permanent-class-pet", "hunter-pet", "controllable-guardian", "guardian", "minion", "totem", "lightwell-totem",
    "companion-minion", "puppet", "vehicle", "vehicle-summon", "ally-summon", "wild-summon", "wild-summon-via-SummonGuardian",
    "charmed", "possessed", "charmed-convert", "possessed-own-pet", "lifecycle-op", "no-consumer", "unresolved",
)


def git_head() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def provenance(generator: str, world_db: bool = False) -> dict[str, Any]:
    p = {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT}
    if world_db:
        p["world_database"] = TDB_RELEASE
    p["generator"] = generator
    p["wowlab_data_commit"] = git_head()
    return p


# ---------------------------------------------------------------------------
# world-db corpus (creature templates) -- tolerant loader
# ---------------------------------------------------------------------------

class CreatureTemplates:
    """``docs/research/world-db-corpora/creature-templates.json`` (tools/tdb_world_extract.py)."""

    def __init__(self, path: Path = CREATURE_TEMPLATES) -> None:
        self.path = path
        self.present = path.exists()
        self.provenance: dict[str, Any] = {}
        self.templates: dict[int, dict[str, Any]] = {}
        self.difficulty: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.addon: dict[int, dict[str, Any]] = {}
        self.summoned_data: dict[int, dict[str, Any]] = {}
        self.template_spells: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.summon_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.models: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.classlevelstats_rows = 0
        self.missing_tables: list[str] = []
        if not self.present:
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        self.provenance = data.get("provenance", {})
        self.missing_tables = list(self.provenance.get("tables_missing_from_dump", data.get("tables_missing_from_dump", [])))
        tables = data.get("tables", {})

        def rows(name: str) -> list[dict[str, Any]]:
            t = tables.get(name)
            if not t:
                return []
            return [dict(zip(t["columns"], r)) for r in t["rows"]]

        for r in rows("creature_template"):
            self.templates[int(r["entry"])] = r
        for r in rows("creature_template_difficulty"):
            self.difficulty[int(r["Entry"])].append(r)
        for r in rows("creature_template_addon"):
            self.addon[int(r["entry"])] = r
        for r in rows("creature_summoned_data"):
            self.summoned_data[int(r["CreatureID"])] = r
        for r in rows("creature_template_spell"):
            self.template_spells[int(r["CreatureID"])].append(r)
        for r in rows("creature_summon_groups"):
            self.summon_groups[int(r["summonerId"])].append(r)
        for r in rows("creature_template_model"):
            self.models[int(r["CreatureID"])].append(r)
        self.classlevelstats_rows = len(rows("creature_classlevelstats"))

    def template(self, entry: int) -> dict[str, Any] | None:
        return self.templates.get(entry)


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

@dataclass
class Record:
    spell_id: int
    spell_name: str
    effect_index: int
    row_id: int
    effect: int
    effect_name: str
    aura: int
    aura_name: str
    handler: str
    category: str
    creature_entry: int
    summon_properties_id: int
    summon_properties: dict[str, Any] | None
    branch: dict[str, Any]
    duration_ms: int | None
    scope: str                              # default | class-skill | script-cast | serverside
    root_kinds: list[str]
    chain: list[int]
    specs: list[int]
    classes: list[int]
    build_skew_added: bool
    tdb_template: dict[str, Any] | None
    tdb_status: str                         # present | absent | corpus-missing | not-applicable
    client_creature: dict[str, Any] | None
    via: dict[str, Any] = field(default_factory=dict)
    evidence_class: str = "trinity-consumer"
    notes: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[int, int]:
        return (self.spell_id, self.effect_index)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class Context:
    """Everything the package needs, loaded once (``Bundle`` + default ``Scope``)."""

    def __init__(self, bundle: Bundle | None = None) -> None:
        self.b = bundle or Bundle()
        self.scope = Scope(self.b)
        self._extended: Scope | None = None
        self.props: dict[int, SummonProps] = {}
        for r in csv.DictReader((TABLES / "SummonProperties.csv").open(encoding="utf-8")):
            self.props[int(r["ID"])] = SummonProps(int(r["ID"]), int(r["Control"]), int(r["Faction"]), int(r["Title"]),
                                                   int(r["Slot"]), int(r["Flags_0"]), int(r["Flags_1"]))
        self.families = {int(r["ID"]): r["Name_lang"] for r in csv.DictReader((TABLES / "CreatureFamily.csv").open(encoding="utf-8"))}
        self.creature_types = {int(r["ID"]): r["Name_lang"] for r in csv.DictReader((TABLES / "CreatureType.csv").open(encoding="utf-8"))}
        self.client_creatures: dict[int, dict[str, Any]] = {}
        for r in csv.DictReader((TABLES / "Creature.csv").open(encoding="utf-8")):
            self.client_creatures[int(r["ID"])] = {
                "id": int(r["ID"]), "name": r["Name_lang"], "classification": int(r["Classification"]),
                "creature_type": int(r["CreatureType"]), "creature_type_name": self.creature_types.get(int(r["CreatureType"]), ""),
                "creature_family": int(r["CreatureFamily"]), "creature_family_name": self.families.get(int(r["CreatureFamily"]), ""),
                "display_id_0": int(r["DisplayID_0"])}
        self.tdb = CreatureTemplates()
        self._spec_of_spell: dict[int, set[int]] | None = None
        self.families_corpus = json.loads(FAMILIES.read_text(encoding="utf-8")) if FAMILIES.exists() else None

    @property
    def extended(self) -> Scope:
        if self._extended is None:
            self._extended = Scope(self.b, include_class_skills=True)
        return self._extended

    def specs_of(self, spell: int, scope: Scope) -> set[int]:
        cache_key = id(scope)
        cache = getattr(self, "_spec_cache", {})
        if cache_key not in cache:
            rev: dict[int, set[int]] = defaultdict(set)
            for spec, spells in scope.specs_reach.items():
                for s in spells:
                    rev[s].add(spec)
            cache[cache_key] = rev
            self._spec_cache = cache
        return cache[cache_key].get(spell, set())

    # ------------------------------------------------------------------
    def record_for(self, spell_id: int, eff, scope: Scope, scope_name: str, via: dict[str, Any] | None = None) -> Record:
        info = self.b.catalog.get(spell_id)
        p = self.props.get(eff.misc1) if eff.effect == 28 else None
        br = effect_branch(eff.effect, eff.aura, eff.misc0, eff.misc1, info.duration_ms if info else None, p)
        entry = int(eff.misc0) if eff.effect in (28, 56, 153) else 0
        tdb_status, tdb_row = "not-applicable", None
        client = None
        if entry:
            client = self.client_creatures.get(entry)
            if not self.tdb.present:
                tdb_status = "corpus-missing"
            elif entry in self.tdb.templates:
                tdb_status = "present"
                t = self.tdb.templates[entry]
                tdb_row = {k: t.get(k) for k in ("entry", "name", "unit_class", "family", "type", "faction", "unit_flags", "flags_extra", "VehicleId", "AIName", "ScriptName", "VerifiedBuild")}
                diffs = sorted(self.tdb.difficulty.get(entry, []), key=lambda d: int(d.get("DifficultyID", 0)))
                tdb_row["difficulty_rows"] = len(diffs)
                tdb_row["difficulty_none"] = next(({k: d[k] for k in DIFFICULTY_KEYS if k in d} for d in diffs if int(d.get("DifficultyID", 0)) == 0), None)
                addon = self.tdb.addon.get(entry)
                tdb_row["addon_auras"] = addon.get("auras") if addon else None
                tdb_row["template_spells"] = [int(r["Spell"]) for r in sorted(self.tdb.template_spells.get(entry, []), key=lambda r: int(r["Index"]))]
                sd = self.tdb.summoned_data.get(entry)
                tdb_row["summoned_data"] = {k: sd[k] for k in ("CreatureIDVisibleToSummoner", "GroundMountDisplayID", "FlyingMountDisplayID", "DespawnOnQuestsRemoved")} if sd else None
            else:
                tdb_status = "absent"
        specs = self.specs_of(spell_id, scope) if scope_name in ("default", "class-skill") else set()
        classes = {scope.roots.class_of_spec[s] for s in specs if s in scope.roots.class_of_spec}
        notes: list[str] = list(br["branch"].get("notes", []))
        ev = br["branch"].get("evidence_class", "trinity-consumer")
        skew = self.b.skew.is_newer_than_trinity(spell_id)
        if skew:
            notes.append("spell id newer than the last client build the pinned Trinity supports (build-skew.json): generic handler still applies, per-spell scripts cannot exist")
        if entry and tdb_status == "absent":
            notes.append("creature entry absent from the pinned TDB: Map::SummonCreature -> Creature::Create fails without a template (build skew or unpopulated data)")
        dur = info.duration_ms if info else None
        if br["branch"].get("cxx_class") == "Totem" and (dur is None or dur <= 0):
            notes.append("Totem-class unit with SpellDuration <= 0: Totem::Update removes it on its first update (m_duration <= diff, Totem.cpp:42-46)")
            br["branch"]["totem_removed_on_first_update"] = True
        if eff.effect == 56:
            ctype = int(tdb_row["type"]) if tdb_row and tdb_row.get("type") is not None else None
            br["branch"]["creature_type"] = ctype
            br["branch"]["is_permanent_pet_for"] = {str(c): is_permanent_pet_for(c, "SUMMON_PET", ctype) for c in sorted(classes)}
            br["branch"]["is_permanent_pet_for_note"] = ("Pet::IsPermanentPetFor (Pet.cpp:1657-1678) gates the client pet number / stat tab (Pet.cpp:279 -> CharmInfo.cpp:238-245), "
                                                         "Pet::CastPetAuras (Pet.cpp:1734) and the spell list in PetSpellInitialize (Player.cpp:22443); "
                                                         "key = owner class id, null when the TDB creature type is unknown")
        chain = [c["spell"] for c in scope.chain(spell_id)] if spell_id in scope.reach else []
        return Record(spell_id, self.b.name(spell_id), eff.index, eff.row_id, eff.effect, effect_name(eff.effect), eff.aura,
                      aura_name(eff.aura) if eff.is_aura else "", br["handler"], br["category"], entry, int(eff.misc1) if eff.effect == 28 else 0,
                      p.to_dict() if p else None, br["branch"], info.duration_ms if info else None, scope_name,
                      sorted(scope.roots.by_spell.get(spell_id, ())), chain, sorted(specs), sorted(classes), skew, tdb_row, tdb_status,
                      client, via or {}, ev, notes)

    @staticmethod
    def is_population_effect(eff) -> bool:
        return eff.effect in POPULATION_EFFECTS or (eff.effect == SPELL_EFFECT_APPLY_AURA and eff.aura in CONTROL_AURAS)

    def records(self, scope: Scope, scope_name: str, exclude: set[int] | None = None) -> list[Record]:
        out: list[Record] = []
        for sid in sorted(scope.reach):
            if exclude and sid in exclude:
                continue
            info = self.b.catalog.get(sid)
            if info is None:
                continue
            for eff in info.effects:
                if self.is_population_effect(eff):
                    out.append(self.record_for(sid, eff, scope, scope_name))
        return out

    def other_difficulty_rows(self, spells: set[int]) -> int:
        n = 0
        for (sid, diff), effs in self.b.catalog.effects.items():
            if sid in spells and diff != DIFFICULTY_NONE:
                n += sum(1 for e in effs.values() if self.is_population_effect(e))
        return n

    # ------------------------------------------------------------------
    def script_cast_records(self) -> list[Record]:
        """Summons issued only by a bound script in player scope (families.json ``player_hooks``)."""
        if not self.families_corpus:
            return []
        seen: set[tuple[int, int]] = set()
        out: list[Record] = []
        for h in self.families_corpus.get("player_hooks", []):
            via = {"hook_spell": h["spell_id"], "hook_spell_name": self.b.name(h["spell_id"]), "script": h["script"],
                   "handler": h["handler"], "file": h["file"], "line": h["line"], "list": h["list"], "family": h["family"]}
            for child in h.get("children", []):
                info = self.b.catalog.get(child)
                if info is None:
                    continue
                for eff in info.effects:
                    if eff.effect in UNIT_CREATING_EFFECTS and (child, eff.index) not in seen:
                        seen.add((child, eff.index))
                        r = self.record_for(child, eff, self.scope, "script-cast", via)
                        r.evidence_class = "structural-inference"
                        r.notes.append("child reached through a script cast (structural index), not through an authored edge")
                        out.append(r)
            if "summon" in h.get("actions", []):
                via2 = dict(via, direct_summon_call=True)
                out.append(Record(h["spell_id"], self.b.name(h["spell_id"]), -1, 0, 0, "script SummonCreature", 0, "",
                                  "Map::SummonCreature (script)", "script-summon", 0, 0, None,
                                  {"branch": "script", "coordinates": [f"{h['file']}:{h['line']}"], "notes": ["properties = nullptr -> TempSummon (UNIT_MASK_SUMMON), no owner"]},
                                  self.b.catalog.get(h["spell_id"]).duration_ms if self.b.catalog.get(h["spell_id"]) else None,
                                  "script-cast", sorted(self.scope.roots.by_spell.get(h["spell_id"], ())), [], sorted(self.specs_of(h["spell_id"], self.scope)), [],
                                  self.b.skew.is_newer_than_trinity(h["spell_id"]), None, "not-applicable", None, via2, "structural-inference",
                                  ["Map::SummonCreature called with a hard-coded creature entry (script constant), read in the script"]))
        return out

    def serverside_records(self) -> list[dict[str, Any]]:
        kids: set[int] = set()
        if self.families_corpus:
            for h in self.families_corpus.get("player_hooks", []):
                kids.update(h.get("children", []))
        linked = self.b.world.linked(self.b.catalog)
        linked_targets = {s for v in linked.values() for s in v}
        out = []
        for (sid, diff), row in sorted(self.b.world.serverside_spells().items()):
            for e in row["_effects"]:
                eff = int(e["Effect"])
                aura = int(e["EffectAura"])
                if eff in UNIT_CREATING_EFFECTS or (eff == SPELL_EFFECT_APPLY_AURA and aura in CONTROL_AURAS):
                    misc1 = int(e["EffectMiscValue2"])
                    p = self.props.get(misc1) if eff == 28 else None
                    br = effect_branch(eff, aura, int(e["EffectMiscValue1"]), misc1, None, p)
                    out.append({"spell_id": sid, "difficulty": diff, "name": row.get("SpellName"), "effect_index": int(e["EffectIndex"]),
                                "effect": eff, "effect_name": effect_name(eff), "aura": aura, "creature_entry": int(e["EffectMiscValue1"]),
                                "summon_properties_id": misc1, "category": br["category"], "referenced_by_player_hook": sid in kids,
                                "linked_from_player_scope": sid in linked_targets and any(t in self.scope.reach for (typ, t), v in linked.items() if sid in v),
                                "tdb_status": ("present" if int(e["EffectMiscValue1"]) in self.tdb.templates else "absent") if self.tdb.present and eff in (28, 56, 153) else "n/a"})
        return out


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def compact(r: Record) -> dict[str, Any]:
    """Identity-only projection of a record (used for the large extended-scope list)."""
    br = r.branch
    return {"spell_id": r.spell_id, "spell_name": r.spell_name, "effect_index": r.effect_index, "row_id": r.row_id, "effect": r.effect,
            "aura": r.aura, "handler": r.handler, "category": r.category, "cxx_class": br.get("cxx_class"), "branch": br.get("branch"),
            "tempsummon_type": br.get("tempsummon_type"), "creature_entry": r.creature_entry, "summon_properties_id": r.summon_properties_id,
            "control_title_slot": ([r.summon_properties["control"], r.summon_properties["title"], r.summon_properties["slot"]] if r.summon_properties else None),
            "flags0": r.summon_properties["flags"]["value"] if r.summon_properties else None, "duration_ms": r.duration_ms,
            "classes": r.classes, "build_skew_added": r.build_skew_added, "tdb_status": r.tdb_status,
            "creature_name": (r.tdb_template or {}).get("name") or (r.client_creature or {}).get("name"),
            "evidence_class": r.evidence_class}


def population_corpus(ctx: Context) -> dict[str, Any]:
    default = ctx.records(ctx.scope, "default")
    default_spells = {r.spell_id for r in default}
    class_skill = ctx.records(ctx.extended, "class-skill", exclude=ctx.scope.reach)
    script_cast = ctx.script_cast_records()
    serverside = ctx.serverside_records()
    return {
        "provenance": provenance("python3 controlled_units.py population", world_db=ctx.tdb.present),
        "scope": {"default": ctx.scope.summary(), "extended": ctx.extended.summary(),
                  "population_effect_types": sorted(POPULATION_EFFECTS), "control_auras": sorted(CONTROL_AURAS)},
        "creature_templates_corpus": {"present": ctx.tdb.present, "path": str(CREATURE_TEMPLATES.relative_to(ROOT)),
                                      "templates": len(ctx.tdb.templates), "missing_tables": ctx.tdb.missing_tables,
                                      "provenance": ctx.tdb.provenance},
        "counts": {"default": len(default), "default_spells": len(default_spells), "class_skill": len(class_skill),
                   "class_skill_spells": len({r.spell_id for r in class_skill}), "script_cast": len(script_cast),
                   "serverside_summon_effects": len(serverside),
                   "serverside_referenced_from_player_scope": sum(1 for s in serverside if s["referenced_by_player_hook"] or s["linked_from_player_scope"]),
                   "other_difficulty_rows_default": ctx.other_difficulty_rows(default_spells)},
        "records": [r.to_dict() for r in default],
        "script_cast": [r.to_dict() for r in script_cast],
        "class_skill_note": "compact identities (full branch dicts are reproducible with `controlled_units.py classify --spell ID`)",
        "class_skill": [compact(r) for r in class_skill],
        "serverside": serverside,
    }


# ---------------------------------------------------------------------------
# creature-template id list (input of the world-db extraction)
# ---------------------------------------------------------------------------

CREATURE_IDS = WORLD_DB_CORPORA / "creature-templates.ids.json"
#: entries hard-coded in the pinned checkout (read by Track A/B consumers), with the coordinate of each
HARDCODED_ENTRIES: dict[int, str] = {
    1: "pet_levelstats creature 1 = hunter pets (Pet.cpp:891-899; ObjectMgr.cpp:3681-3779)",
    416: "PET_IMP (Entities/Creature/TemporarySummon.h:26)",
    691: "PET_FEL_HUNTER (TemporarySummon.h:27)",
    1860: "PET_VOID_WALKER (TemporarySummon.h:28)",
    1863: "PET_SUCCUBUS (TemporarySummon.h:29)",
    18540: "PET_DOOMGUARD (TemporarySummon.h:30)",
    30146: "PET_FELGUARD (TemporarySummon.h:31; a spell id in 12.1, kept as written)",
    184600: "PET_INCUBUS (TemporarySummon.h:32)",
    26125: "PET_GHOUL (TemporarySummon.h:35)",
    29264: "PET_SPIRIT_WOLF (TemporarySummon.h:38); Pet.cpp:1017",
    27893: "NPC_DK_DANCING_RUNE_WEAPON (scripts/Spells/spell_dk.cpp:117)",
    510: "ENTRY_WATER_ELEMENTAL (StatSystem.cpp:1130; Pet.cpp:958-962)",
    1964: "ENTRY_TREANT (StatSystem.cpp:1131; Pet.cpp InitStatsForLevel)",
    15352: "earth elemental (Pet.cpp:972)",
    15438: "ENTRY_FIRE_ELEMENTAL (StatSystem.cpp:1132; Pet.cpp InitStatsForLevel)",
    19668: "Shadowfiend (Pet.cpp:992; spell_priest.cpp:317)",
    19833: "Snake Trap Venomous Snake (Pet.cpp:1005)",
    19921: "Snake Trap Viper (Pet.cpp:1011)",
    31216: "Mirror Image (Pet.cpp:1034)",
    27829: "Ebon Gargoyle (Pet.cpp:1045)",
    28017: "ENTRY_BLOODWORM (StatSystem.cpp:1134)",
    62982: "NPC_PRIEST_MINDBENDER (spell_priest.cpp:316)",
    224466: "NPC_PRIEST_VOIDWRAITH (spell_priest.cpp:318)",
    198236: "NPC_PRIEST_DIVINE_IMAGE (spell_priest.cpp:315)",
}
#: Track B witness entries (frozen copy of the Track B session list; stats.py / witnesses_b.py consume them)
TRACK_B_WITNESS_ENTRIES: tuple[int, ...] = (
    1, 299, 416, 417, 1860, 1863, 184600, 17252, 55659, 143622, 26125, 24207, 31216, 510, 78116, 19668, 62982, 15438,
    15352, 61029, 61056, 95061, 95072, 77936, 29264, 100820, 3527, 1964, 54983, 103822, 46499, 63508, 27829, 28017,
    135816, 103673, 98035, 135002, 47319, 250289)


def creature_id_components(ctx: Context) -> dict[str, list[int]]:
    """The id set ``creature-templates.json`` was extracted with, by derivation component."""
    extended: set[int] = set()
    for sid in ctx.extended.reach:
        info = ctx.b.catalog.get(sid)
        if info is None:
            continue
        for eff in info.effects:
            if eff.effect in (28, 56, 153) and int(eff.misc0):
                extended.add(int(eff.misc0))
    serverside = {s["creature_entry"] for s in ctx.serverside_records() if s["effect"] in (28, 56, 153) and s["creature_entry"]}
    return {"extended_scope_misc0": sorted(extended), "serverside_misc0": sorted(serverside),
            "hardcoded_trinity_entries": sorted(HARDCODED_ENTRIES), "track_b_witness_entries": sorted(set(TRACK_B_WITNESS_ENTRIES))}


def creature_ids_corpus(ctx: Context) -> dict[str, Any]:
    comp = creature_id_components(ctx)
    ids = sorted(set().union(*comp.values()))
    return {
        "provenance": provenance("python3 controlled_units.py creature-ids"),
        "purpose": "--keep id set of world-db-corpora/creature-templates.json (tools/tdb_world_extract.py); "
                   "EffectMiscValue_0 of DIFFICULTY_NONE effects 28/56/153 in the extended scope (include_class_skills=True, "
                   "a superset of the default scope) and in serverside_spell, plus Trinity's hard-coded entries and the Track B witness entries",
        "count": len(ids),
        "component_counts": {k: len(v) for k, v in comp.items()},
        "hardcoded_coordinates": {str(k): v for k, v in sorted(HARDCODED_ENTRIES.items())},
        "components": comp,
        "ids": ids,
    }


def register(sub) -> None:
    p = sub.add_parser("population", help="write controlled-unit-corpora/population.json")
    p.set_defaults(func=cmd_population)
    c = sub.add_parser("creature-ids", help="write world-db-corpora/creature-templates.ids.json (the extraction id set)")
    c.add_argument("--out", type=Path, default=CREATURE_IDS)
    c.add_argument("--keep-file", type=Path, default=None,
                   help="also write the bare JSON id list for tools/tdb_world_extract.py --keep ...=@FILE")
    c.set_defaults(func=cmd_creature_ids)


def cmd_creature_ids(args) -> int:
    corpus = creature_ids_corpus(Context())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
    print(args.out, corpus["count"])
    if args.keep_file:
        args.keep_file.write_text(json.dumps(corpus["ids"]) + "\n", encoding="utf-8")
        print(args.keep_file)
    return 0


def cmd_population(args) -> int:
    ctx = Context()
    CORPORA.mkdir(parents=True, exist_ok=True)
    path = CORPORA / "population.json"
    path.write_text(json.dumps(population_corpus(ctx), indent=1) + "\n", encoding="utf-8")
    print(path)
    return 0
