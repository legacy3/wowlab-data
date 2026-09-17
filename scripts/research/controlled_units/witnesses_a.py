"""Track A witnesses: population / ownership / lifecycle view of named controlled units.

``classify --spell ID`` prints, for any spell, its population records (branch,
category), ownership topology and lifecycle policy in one view.  ``witnesses``
writes ``witnesses-a.json`` for the fixed witness set below; ``all-a``
regenerates every Track A corpus.

A witness whose client effect is ``SPELL_EFFECT_DUMMY`` / ``SPELL_AURA_DUMMY``
and that has no ``spell_script_names`` binding creating a unit is reported as
``unresolved`` (server-side semantics not implemented in the pinned checkout),
never given a category.
"""

from __future__ import annotations

import json
from typing import Any

from . import CORPORA
from .lifecycle import policy
from .ownership import topology
from .population import Context, Record, provenance

#: (label, class, spells, notes).  Spells listed are the cast spell first, then known children
#: that carry the summon effect (out of authored reach when the parent is a Dummy).
WITNESSES: list[dict[str, Any]] = [
    {"id": "hunter-call-pet", "label": "Hunter: Call Pet 1 / Tame Beast / Eyes of the Beast", "class": "Hunter", "spells": [883, 1515, 13481, 321297],
     "notes": ["883/1515 are class-skill-line spells (SkillLineAbility), outside the default scope by convention",
               "identity of the tamed pet is character-DB state (character_pet: PetNumber, CreatureId, CreatedBySpellId, SpecializationId, Level, ReactState, ActionBar) -> Track C caller input"]},
    {"id": "warlock-felguard", "label": "Warlock: Summon Felguard / Grimoire: Felguard", "class": "Warlock", "spells": [30146, 111898]},
    {"id": "warlock-imp", "label": "Warlock: Summon Imp", "class": "Warlock", "spells": [688], "notes": ["class-skill-line spell (extended scope)"]},
    {"id": "warlock-wild-imps", "label": "Warlock: Hand of Gul'dan -> Wild Imp", "class": "Warlock", "spells": [105174, 104317]},
    {"id": "warlock-dreadstalkers", "label": "Warlock: Call Dreadstalkers", "class": "Warlock", "spells": [104316, 193332]},
    {"id": "warlock-vilefiend-tyrant", "label": "Warlock: Summon Vilefiend / Demonic Tyrant", "class": "Warlock", "spells": [264119, 265187]},
    {"id": "dk-raise-dead", "label": "Death Knight: Raise Dead", "class": "Death Knight", "spells": [46585]},
    {"id": "dk-army-apocalypse", "label": "Death Knight: Army of the Dead / Apocalypse", "class": "Death Knight", "spells": [42650, 42651, 275699]},
    {"id": "dk-drw", "label": "Death Knight: Dancing Rune Weapon", "class": "Death Knight", "spells": [49028]},
    {"id": "dk-control-undead", "label": "Death Knight: Control Undead", "class": "Death Knight", "spells": [111673]},
    {"id": "mage-water-elemental", "label": "Mage: Summon Water Elemental / Mirror Image / Ring of Frost", "class": "Mage", "spells": [31687, 55342, 113724]},
    {"id": "priest-shadowfiend", "label": "Priest: Shadowfiend / Mindbender / Divine Image", "class": "Priest", "spells": [34433, 200174, 392988, 392990]},
    {"id": "priest-mind-control", "label": "Priest: Mind Control / Dominate Mind", "class": "Priest", "spells": [605, 205364]},
    {"id": "shaman-elementals", "label": "Shaman: Fire / Earth Elemental, Feral Spirit", "class": "Shaman", "spells": [198067, 198103, 51533]},
    {"id": "shaman-totems", "label": "Shaman: Healing Stream Totem / Capacitor Totem", "class": "Shaman", "spells": [5394, 192058]},
    {"id": "druid-force-of-nature", "label": "Druid: Force of Nature", "class": "Druid", "spells": [205636, 248280]},
    {"id": "monk-celestials", "label": "Monk: Invoke Xuen / Chi-Ji", "class": "Monk", "spells": [123904, 325197]},
    {"id": "paladin-goak", "label": "Paladin: Guardian of Ancient Kings", "class": "Paladin", "spells": [86659]},
]
SPELL_EFFECT_DUMMY, SPELL_AURA_DUMMY = 3, 4

#: A1.3 -- identity of a tamed/called hunter pet is character-DB state; what a caller (Track C fixture) must supply
HUNTER_PET_CALLER_INPUT: dict[str, Any] = {
    "evidence_class": "trinity-consumer",
    "storage": "characters DB `character_pet` (sql/base/characters_database.sql) loaded into PetStable::PetInfo (src/server/game/Entities/Pet/PetDefines.h:147-164)",
    "fields": [
        {"PetInfo": "PetNumber", "column": "id", "consumer": "GetLoadPetInfo / CharmInfo::SetPetNumber (Pet.cpp:105-155, 279)", "role": "identity across summons (not the GUID)"},
        {"PetInfo": "CreatureId", "column": "entry", "consumer": "Pet::Create(petInfo->CreatureId) (Pet.cpp:253); IsTameable check for HUNTER_PET (Pet.cpp:231-240)", "role": "creature template -> family, type, stats (Track B)"},
        {"PetInfo": "DisplayId", "column": "modelid", "consumer": "SetDisplayId (Pet.cpp:281)", "role": "cosmetic"},
        {"PetInfo": "CreatedBySpellId", "column": "CreatedBySpell", "consumer": "SetCreatedBySpell (Pet.cpp:260); isTemporarySummon = GetDuration() > 0 (Pet.cpp:225-229)", "role": "tame spell (13481 family) or summon spell"},
        {"PetInfo": "Type", "column": "PetType", "consumer": "setPetType (Pet.cpp:258): HUNTER_PET -> class WARRIOR, pet flags (Pet.cpp:293-299)", "role": "discriminant"},
        {"PetInfo": "Level", "column": "level", "consumer": "InitStatsForLevel(petlevel) (Pet.cpp:309) then SynchronizeLevelWithOwner (Pet.cpp:312) -> owner level", "role": "overwritten by owner level"},
        {"PetInfo": "Experience", "column": "exp", "consumer": "SetPetExperience (Pet.cpp:310)", "role": "legacy-only (pet XP)"},
        {"PetInfo": "ReactState", "column": "Reactstate", "consumer": "SetReactState (Pet.cpp:325)", "role": "control state"},
        {"PetInfo": "Name / WasRenamed", "column": "name / renamed", "consumer": "SetName, pet flags (Pet.cpp:285, 297)", "role": "cosmetic"},
        {"PetInfo": "Health / Mana", "column": "curhealth / curmana", "consumer": "Pet.cpp:328-341 (HUNTER_PET with 0 health loads dead)", "role": "initial resource state"},
        {"PetInfo": "LastSaveTime", "column": "savetime", "consumer": "_LoadAuras timediff (Pet.cpp:395-396)", "role": "aura remaining time"},
        {"PetInfo": "ActionBar", "column": "abdata", "consumer": "CharmInfo::LoadPetActionBar unless temporary (Pet.cpp:376-377)", "role": "autocast / action bar state"},
        {"PetInfo": "SpecializationId", "column": "specialization", "consumer": "remapped by ChrSpecialization OrderIndex to class 0 or PET_SPEC_OVERRIDE_CLASS_INDEX when owner has SPELL_AURA_OVERRIDE_PET_SPECS (Pet.cpp:413-417) -> SetSpecialization (Pet.cpp:1904-1928)", "role": "pet spec (db2 ChrSpecialization 74/79/81 or 535-537)"},
        {"PetInfo": "(slot)", "column": "slot", "consumer": "PetStable ActivePets[0..4] / StabledPets / UnslottedPets; Call Pet N passes PetSaveMode(N-1) from the effect value (SpellEffects.cpp:2714-2716; SpellEffect 883/83242-83245 base points 0..4)", "role": "which pet Call Pet N loads"},
    ],
    "side_tables": ["pet_aura / pet_aura_effect", "pet_spell", "pet_spell_cooldown", "pet_spell_charges", "character_pet_declinedname"],
    "side_table_coordinate": "src/server/database/Database/Implementation/CharacterDatabase.cpp:751-761",
    "note": "none of this is in DB2 or the world DB: a fixture must supply at least CreatureId, Type, CreatedBySpellId, SpecializationId and the active slot; the rest is cosmetic, legacy-only or runtime state",
}


def classify(ctx: Context, spell_id: int) -> dict[str, Any]:
    info = ctx.b.catalog.get(spell_id)
    if info is None:
        return {"spell": spell_id, "error": "spell not in snapshot", "evidence_class": "unresolved"}
    in_default = spell_id in ctx.scope.reach
    scope = ctx.scope if in_default else ctx.extended
    scope_name = "default" if in_default else ("class-skill" if spell_id in scope.reach else "out-of-authored-reach")
    recs: list[Record] = [ctx.record_for(spell_id, e, scope, scope_name) for e in info.effects if Context.is_population_effect(e)]
    bindings = ctx.b.script_names.get(spell_id, [])
    hooks = [h for h in (ctx.families_corpus or {}).get("player_hooks", []) if h["spell_id"] == spell_id] if ctx.families_corpus else []
    dummy = [e.index for e in info.effects if e.effect == SPELL_EFFECT_DUMMY or (e.is_aura and e.aura == SPELL_AURA_DUMMY)]
    out: dict[str, Any] = {"spell": spell_id, "name": ctx.b.name(spell_id), "scope": scope_name,
                           "build_skew_added": ctx.b.skew.is_newer_than_trinity(spell_id), "duration_ms": info.duration_ms,
                           "effects": [{"index": e.index, "effect": e.effect, "aura": e.aura, "trigger": e.trigger_spell, "misc0": e.misc0, "misc1": e.misc1} for e in info.effects],
                           "script_bindings": bindings,
                           "player_hooks": [{"script": h["script"], "handler": h["handler"], "line": h["line"], "family": h["family"], "children": h.get("children", []), "actions": h.get("actions", [])} for h in hooks],
                           "records": []}
    for r in recs:
        out["records"].append({"population": r.to_dict(), "topology": topology(r), "lifecycle": policy(r)})
    # authored trigger children (one level): TRIGGER_SPELL / PERIODIC_TRIGGER_SPELL etc. carry the unit-creating effect
    children = []
    for e in info.effects:
        child = int(e.trigger_spell or 0)
        cinfo = ctx.b.catalog.get(child) if child else None
        if cinfo is None:
            continue
        cats = []
        for ce in cinfo.effects:
            if Context.is_population_effect(ce):
                cr = ctx.record_for(child, ce, scope, scope_name)
                cats.append({"effect_index": ce.index, "effect": ce.effect, "aura": ce.aura, "category": cr.category,
                             "cxx_class": cr.branch.get("cxx_class"), "creature_entry": cr.creature_entry, "tdb_status": cr.tdb_status})
        if cats:
            children.append({"parent_effect_index": e.index, "parent_effect": e.effect, "parent_aura": e.aura, "child": child,
                             "child_name": ctx.b.name(child), "child_records": cats})
    out["trigger_children"] = children
    out["dummy_effects"] = dummy
    if not recs and children:
        out["category"] = sorted({c["category"] for ch in children for c in ch["child_records"]})
        out["reason"] = "the unit is created by an authored trigger child (effect TriggerSpell), classified from the child's own effect rows"
        out["evidence_class"] = "trinity-consumer"
    elif not recs:
        if dummy:
            summon_hooks = [h for h in hooks if "summon" in h.get("actions", []) or any(ctx.b.catalog.get(c) and any(x.effect in (28, 56, 153) for x in ctx.b.catalog.get(c).effects) for c in h.get("children", []))]
            out["category"] = "script-cast" if summon_hooks else "unresolved"
            out["reason"] = (f"client effect(s) {dummy} are Dummy; the unit is created by the bound script" if summon_hooks else
                             f"client effect(s) {dummy} are Dummy (server-side semantics); spell_script_names binding: {bindings or 'none'}; no bound script creates a unit in the pinned checkout")
            out["reopen_condition"] = "a TrinityCore revision binding a script to this spell that calls SummonCreature/CastSpell of a summon spell"
            out["evidence_class"] = "structural-inference" if summon_hooks else "unresolved"
        else:
            out["category"] = "not-a-controlled-unit-effect"
            out["reason"] = "no unit-creating, control or pet-lifecycle effect on this spell (DIFFICULTY_NONE)"
            out["evidence_class"] = "db2-fact"
    else:
        out["category"] = sorted({r.category for r in recs})
        out["evidence_class"] = sorted({r.evidence_class for r in recs})
    return out


def witnesses_corpus(ctx: Context) -> dict[str, Any]:
    items = []
    for w in WITNESSES:
        item = dict(w)
        item["spells"] = [classify(ctx, s) for s in w["spells"]]
        item["categories"] = sorted({c for s in item["spells"] for c in (s.get("category") if isinstance(s.get("category"), list) else [s.get("category")]) if c})
        items.append(item)
    return {"provenance": provenance("python3 controlled_units.py witnesses --write", world_db=ctx.tdb.present), "witnesses": items,
            "hunter_pet_caller_input": HUNTER_PET_CALLER_INPUT,
            "summary": {"witness_sets": len(items), "spells": sum(len(w["spells"]) for w in items),
                        "unresolved_spells": [s["spell"] for w in items for s in w["spells"] if s.get("category") == "unresolved"],
                        "trigger_child_spells": [s["spell"] for w in items for s in w["spells"] if s.get("trigger_children") and not s.get("records")],
                        "build_skew_spells": [s["spell"] for w in items for s in w["spells"] if s.get("build_skew_added")]}}


def register(sub) -> None:
    p = sub.add_parser("classify", help="population + ownership + lifecycle view of one spell")
    p.add_argument("--spell", type=int, required=True)
    p.set_defaults(func=cmd_classify)
    w = sub.add_parser("witnesses", help="Track A witness set; --write for controlled-unit-corpora/witnesses-a.json")
    w.add_argument("--write", action="store_true")
    w.set_defaults(func=cmd_witnesses)
    a = sub.add_parser("all-a", help="regenerate every Track A corpus (vocabulary, population, census, ownership, lifecycle, witnesses-a)")
    a.set_defaults(func=cmd_all)


def cmd_classify(args) -> int:
    ctx = Context()
    print(json.dumps(classify(ctx, args.spell), indent=1))
    return 0


def cmd_witnesses(args) -> int:
    ctx = Context()
    corpus = witnesses_corpus(ctx)
    if args.write:
        CORPORA.mkdir(parents=True, exist_ok=True)
        path = CORPORA / "witnesses-a.json"
        path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
        print(path)
    else:
        print(json.dumps({w["id"]: {s["spell"]: s.get("category") for s in w["spells"]} for w in corpus["witnesses"]}, indent=1))
    return 0


def cmd_all(args) -> int:
    from .census import Census
    from .lifecycle import lifecycle_corpus
    from .ownership import ownership_corpus
    from .population import population_corpus
    from .vocabulary import vocabulary_corpus
    ctx = Context()
    CORPORA.mkdir(parents=True, exist_ok=True)
    for name, corpus in (("vocabulary.json", vocabulary_corpus()), ("population.json", population_corpus(ctx)), ("census.json", Census(ctx).corpus()),
                         ("ownership.json", ownership_corpus(ctx)), ("lifecycle.json", lifecycle_corpus(ctx)), ("witnesses-a.json", witnesses_corpus(ctx))):
        path = CORPORA / name
        path.write_text(json.dumps(corpus, indent=1) + "\n", encoding="utf-8")
        print(path, path.stat().st_size)
    return 0
