"""Track B witnesses (stats/spells view) and the B world-db slice.

``world-b``
    Restricted TDB replay of ``creature_template`` (projected),
    ``creature_template_difficulty``, ``creature_template_resistance``,
    ``creature_template_spell`` and ``creature_classlevelstats`` for every
    creature entry summoned by a DIFFICULTY_NONE SUMMON(28)/SUMMON_PET(56)
    effect in the 12.1 snapshot (+ entry 1, the hunter-pet stats row) ->
    ``docs/research/world-db-corpora/pet-witness-templates.json``.

    It reuses :mod:`tools.tdb_replay` unchanged but filters rows *at insert*
    (the generic ``tdb_world_extract.py`` filters after replay, which makes the
    22,774 ``UPDATE creature_template_difficulty`` statements scan ~all rows).
    An UPDATE/DELETE whose every WHERE conjunction pins the filter column to
    values outside the keep set cannot touch a kept row and is skipped; an
    UPDATE that assigns the filter column itself is refused (it could move a
    row into the kept set).

``witnesses-b``
    ``controlled-unit-corpora/witnesses-b.json``.

``all-b``
    Regenerates stats.json, inheritance.json, spells.json, witnesses-b.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from controlled_units import CORPORA, TABLES, WORLD_DB_CORPORA, SourceError
from controlled_units import stats as st

RESEARCH = Path(__file__).resolve().parents[1]
WORKSPACE = Path(__file__).resolve().parents[4]
PINNED_TDB = "TDB_full_world_1200.26021_2026_02_06.sql"
WORLD_B = WORLD_DB_CORPORA / "pet-witness-templates.json"

WORLD_TABLES = {  # table -> (filter column, projection or None)
    "creature_template": ("entry", ["entry", "name", "unit_class", "family", "type", "Classification", "dmgschool",
                                    "BaseAttackTime", "RangeAttackTime", "BaseVariance", "flags_extra", "AIName",
                                    "ScriptName", "VerifiedBuild"]),
    "creature_template_difficulty": ("Entry", ["Entry", "DifficultyID", "LevelScalingDeltaMin", "LevelScalingDeltaMax",
                                               "ContentTuningID", "HealthScalingExpansion", "HealthModifier",
                                               "ManaModifier", "ArmorModifier", "DamageModifier", "CreatureDifficultyID",
                                               "StaticFlags1", "VerifiedBuild"]),
    "creature_template_resistance": ("CreatureID", None),
    "creature_template_spell": ("CreatureID", None),
    "creature_classlevelstats": (None, None),
}


def summoned_entries() -> list[int]:
    out = {1}
    with (TABLES / "SpellEffect.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["DifficultyID"] == "0" and r["Effect"] in ("28", "56") and int(r["EffectMiscValue_0"]):
                out.add(int(r["EffectMiscValue_0"]))
    return sorted(out)


def _filtered_state_class(base):
    class FilteredTableState(base):  # type: ignore[misc, valid-type]
        keep_column: str | None = None
        keep: set = set()
        skipped_updates = 0
        skipped_deletes = 0

        def insert(self, columns, tuples, mode):  # noqa: D401
            if self.keep_column is None:
                return super().insert(columns, tuples, mode)
            kc = self.keep_column
            lower = [self.col(c) for c in columns]
            idx = lower.index(kc) if kc in lower else None
            if idx is None:
                raise ValueError(f"{self.name}: INSERT without filter column {kc}")
            kept = [t for t in tuples if t[idx] in self.keep]
            return super().insert(columns, kept, mode)

        def _cannot_match(self, where) -> bool:
            kc = self.keep_column
            if kc is None:
                return False
            for conj in where:
                cols = {self.col(c): v for c, v in conj.items()}
                if kc not in cols or cols[kc] & self.keep:
                    return False
            return True

        def update(self, assigns, where):
            if self.keep_column is not None and any(self.col(c) == self.keep_column for c in assigns):
                raise ValueError(f"{self.name}: UPDATE assigns the filter column {self.keep_column}")
            if self._cannot_match(where):
                type(self).skipped_updates += 1
                return 0
            return super().update(assigns, where)

        def delete(self, where):
            if self._cannot_match(where):
                type(self).skipped_deletes += 1
                return 0
            return super().delete(where)

    return FilteredTableState


def cmd_world_b(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(RESEARCH / "tools"))
    import tdb_replay as tr  # noqa: E402

    tdb = Path(args.tdb)
    tc_root = WORKSPACE / "TrinityCore"
    pinned = re.search(r'DATABASE_FULL_DATABASE\s+"([^"]+)"', (tc_root / "revision_data.h.in.cmake").read_text()).group(1)
    if tdb.name != pinned or pinned != PINNED_TDB:
        raise SourceError(f"checkout pins {pinned}, got {tdb.name}")
    keep = set(summoned_entries())
    keep |= {str(v) for v in keep}
    base_text = tdb.read_text(encoding="utf-8", errors="replace")
    base_sha = hashlib.sha256(base_text.encode("utf-8", errors="replace")).hexdigest()
    schemas = tr.parse_create_tables(base_text, set(WORLD_TABLES))
    replay = tr.Replay(schemas)
    stats_by_table = {}
    for name, (kc, _) in WORLD_TABLES.items():
        cls = _filtered_state_class(tr.TableState)
        cls.keep_column = kc
        cls.keep = keep
        replay.state[name] = cls(schemas[name])
        stats_by_table[name] = cls
    unparsed: list[dict] = []
    from collections import Counter
    counter: Counter = Counter()
    tr.replay_dump_lines(replay, base_text, unparsed, counter, tdb.name)
    base_counts = {n: len(t.rows) for n, t in replay.state.items()}
    del base_text
    touched, total = tr.replay_updates(replay, tc_root / "sql/updates/world/master", unparsed, counter)
    tables = {}
    for name, (kc, proj) in WORLD_TABLES.items():
        stt = replay.state[name]
        rows = sorted(stt.rows.values(), key=lambda r: tuple((0, r[k]) if isinstance(r[k], (int, float)) else (1, str(r[k])) for k in stt.key))
        cols = list(dict.fromkeys(list(stt.key) + proj)) if proj else stt.columns
        tables[name] = {"columns": cols, "key": list(stt.key), "projected": bool(proj),
                        "row_filters": [{"column": kc, "values": len(keep) // 2, "applied": "at insert"}] if kc else [],
                        "row_count_emitted": len(rows), "rows": [[r[c] for c in cols] for r in rows]}
    head = subprocess.run(["git", "-C", str(tc_root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    payload = {
        "provenance": {
            "tool": "scripts/research/controlled_units/witnesses_b.py world-b (filter-at-insert over tools/tdb_replay.py)",
            "generator": f"python3 controlled_units.py world-b --tdb <pallet>/../bag/tdb/{PINNED_TDB}",
            "trinitycore_commit": head, "base_world_database": pinned, "base_world_database_sha256": base_sha,
            "update_directory": "sql/updates/world/master", "update_files_total": total,
            "update_files_touching_tracked_tables": touched,
            "keep_set": "creature entries summoned by DIFFICULTY_NONE SpellEffect 28/56 in the 12.1 snapshot, plus entry 1",
            "keep_set_size": len(keep) // 2, "snapshot_build": st.SNAPSHOT_BUILD,
            "base_row_counts_after_filter": base_counts,
            "final_row_counts_after_filter": {n: len(t.rows) for n, t in replay.state.items()},
            "skipped_statements": {n: {"updates": c.skipped_updates, "deletes": c.skipped_deletes} for n, c in stats_by_table.items()},
            "applied": {" / ".join(k): v for k, v in sorted(counter.items())},
            "unparsed_statement_count": len(unparsed),
            "note": "rows are Trinity authoring for the pinned TDB, not client data and not Retail truth",
        },
        "unparsed": unparsed[:200],
        "tables": tables,
    }
    out = Path(args.out) if args.out else WORLD_B
    out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB); rows={ {n: t['row_count_emitted'] for n, t in tables.items()} }; unparsed={len(unparsed)}")
    return 0


# ---------------------------------------------------------------------------
# witnesses
# ---------------------------------------------------------------------------

TC = st.TC

#: (label, cast spell, summon spell, owner class, spells to show, extra notes)
WITNESSES = [
    {"id": "hunter-pet", "label": "Hunter pet (Call Pet 1, tamed family Wolf 1)", "cast": 883, "summon": 883, "owner_class": 3,
     "family": 1, "tame": [1515, 13481]},
    {"id": "warlock-imp", "label": "Warlock Imp", "cast": 688, "summon": 688, "owner_class": 9},
    {"id": "warlock-felguard", "label": "Warlock Felguard", "cast": 30146, "summon": 30146, "owner_class": 9, "family": 29,
     "spotlight_spells": [30213, 89751, 89766, 30151, 134477, 117225]},
    {"id": "warlock-wild-imp", "label": "Warlock Wild Imp (Hand of Gul'dan)", "cast": 105174, "summon": 104317, "owner_class": 9},
    {"id": "dk-ghoul-talent", "label": "DK Raise Dead (46584) -> ghoul", "cast": 46584, "summon": 52150, "owner_class": 6,
     "alt_summons": [46585]},
    {"id": "dk-army-ghoul", "label": "DK Army of the Dead ghoul", "cast": 42650, "summon": 42651, "owner_class": 6},
    {"id": "mage-mirror-image", "label": "Mage Mirror Image", "cast": 55342, "summon": 321686, "owner_class": 8},
    {"id": "mage-water-elemental", "label": "Mage Water Elemental", "cast": 31687, "summon": 31687, "owner_class": 8},
    {"id": "priest-shadowfiend", "label": "Priest Shadowfiend", "cast": 34433, "summon": 1280172, "owner_class": 5},
    {"id": "priest-mindbender", "label": "Priest Mindbender", "cast": 123040, "summon": 123040, "owner_class": 5},
    {"id": "shaman-fire-elemental", "label": "Shaman Fire Elemental", "cast": 198067, "summon": 188592, "owner_class": 7,
     "alt_summons": [372335]},
    {"id": "shaman-earth-elemental", "label": "Shaman Earth Elemental", "cast": 198103, "summon": 188616, "owner_class": 7},
    {"id": "shaman-feral-spirit", "label": "Shaman Feral Spirit", "cast": 51533, "summon": 228562, "owner_class": 7},
    {"id": "shaman-healing-stream-totem", "label": "Shaman Healing Stream Totem", "cast": 5394, "summon": 5394, "owner_class": 7},
    {"id": "druid-treant", "label": "Druid Force of Nature treant", "cast": 205636, "summon": 248280, "owner_class": 11,
     "alt_summons": [102693]},
    {"id": "paladin-goak", "label": "Paladin Guardian of Ancient Kings", "cast": 86659, "summon": None, "owner_class": 2,
     "search_entry": 46499},
]


def _effects(spell: int) -> list[dict[str, Any]]:
    from controlled_units import inheritance
    return [{"index": int(e["EffectIndex"]), "effect": int(e["Effect"]), "aura": int(e["EffectAura"]),
             "trigger": int(e["EffectTriggerSpell"]), "misc0": int(e["EffectMiscValue_0"]), "misc1": int(e["EffectMiscValue_1"]),
             "base_points": float(e["EffectBasePointsF"])}
            for e in inheritance._db2()["effects"].get(spell, [])]


def _link(cast: int, summon: int | None, bundle, scope) -> dict[str, Any]:
    """How Trinity gets from the player's cast to the summon spell."""
    if summon is None:
        return {"status": "unresolved", "reason": "no summon spell identified"}
    if cast == summon:
        return {"status": "direct", "evidence_class": "db2-fact"}
    trig = [e for e in _effects(cast) if e["trigger"] == summon]
    if trig:
        return {"status": "authored-trigger", "effects": trig, "evidence_class": "db2-fact"}
    linked = [k for k, v in bundle.world.linked(bundle.catalog).items() if k[0] == cast and summon in v]
    if linked:
        return {"status": "spell_linked_spell", "rows": [list(k) for k in linked], "evidence_class": "world-db-fact"}
    scripts = bundle.script_names.get(cast, [])
    from controlled_units import spells as sp
    refs = sp.pet_script_constants().get(summon, [])
    idx = json.loads((CORPORA.parent / "dummy-corpora" / "bindings.json").read_text(encoding="utf-8"))
    hook_refs = []
    for b in idx["player_records"].get(str(cast), []):
        for h in b.get("hooks", []):
            if summon in (h.get("facts") or {}).get("refs", []):
                hook_refs.append({"script": b["script_name"], "hook": h["list"], "line": h["line"], "executes": h.get("executes")})
    live = [h for h in hook_refs if h.get("executes")]
    status = "script" if live else ("script-hook-inert" if hook_refs else "no-trinity-edge")
    return {"status": status,
            "script_bindings": scripts, "script_hook_refs": hook_refs, "pet_script_refs": refs,
            "summon_in_player_scope": summon in scope.reach, "scope_chain": scope.chain(summon) if summon in scope.reach else [],
            "cast_effects": _effects(cast),
            "evidence_class": "trinity-consumer" if live else "unresolved",
            "inert_reason": ("the bound hook's effect index/type does not match the 12.1 SpellEffect row, so Trinity never runs it "
                             "(SpellScript.cpp:292 'did not match dbc effect data'); dummy-corpora/bindings.json executes=false")
                            if hook_refs and not live else None,
            "reopen": "a spell_linked_spell / spell_script_names row or a script that casts the summon spell"}


def build_witnesses() -> dict[str, Any]:
    from controlled_units import inheritance
    from controlled_units import spells as sp
    bundle = sp._bundle()
    from dummy_semantics.scope import Scope
    scope = Scope(bundle, include_class_skills=False)
    db2, world = st.sources()
    ft = sp.family_tables()
    out = []
    for w in WITNESSES:
        rec: dict[str, Any] = {"id": w["id"], "label": w["label"], "cast_spell": w["cast"],
                               "cast_name": bundle.name(w["cast"]), "cast_in_player_scope": w["cast"] in scope.reach,
                               "summon_spell": w["summon"], "link": _link(w["cast"], w["summon"], bundle, scope)}
        runs = []
        for spell in [s for s in [w["summon"], *w.get("alt_summons", [])] if s]:
            res = inheritance.resolve_summon(spell, None, w["owner_class"])
            run: dict[str, Any] = {"spell": spell, "name": bundle.name(spell), "resolution": {
                k: res.get(k) for k in ("status", "entry", "trinity_unit_class", "reason", "summon_effect")}}
            if res.get("status") == "ok":
                owner = st.OwnerFacts(class_id=w["owner_class"], level=90, stats=inheritance.STANDARD_OWNER["stats"],
                                      armor=inheritance.STANDARD_OWNER["armor"],
                                      attack_power_melee=inheritance.STANDARD_OWNER["attack_power_melee"],
                                      attack_power_ranged=inheritance.STANDARD_OWNER["attack_power_ranged"],
                                      mod_damage_done_pos=(inheritance.STANDARD_OWNER["sp"],) * 7,
                                      spell_base_damage_bonus={k: inheritance.STANDARD_OWNER["sp"] for k in ("fire", "frost", "nature", "shadow")})
                try:
                    o = st.run_oracle(res["entry"], res["trinity_unit_class"], owner, db2=db2, world=world,
                                      use_creature_level=bool(res["summon_effect"].get("use_creature_level")))
                    trace = o.pop("trace")
                    run["oracle"] = {"status": "ok", **{k: o[k] for k in ("level", "pet_type", "pet_levelstats", "stats",
                                                                          "stat_from_owner", "create_health", "max_health",
                                                                          "max_health_undefined_behaviour", "armor",
                                                                          "attack_power", "bonus_spell_damage", "weapon_damage",
                                                                          "min_damage", "max_damage", "base_attack_time_ms",
                                                                          "max_power", "source_notes")},
                                     "trace": trace}
                    entry = res["entry"]
                    run["entry_switches"] = {
                        "health_multiplier": st.HEALTH_PER_STAMINA.get(entry, st.HEALTH_PER_STAMINA_DEFAULT),
                        "init_stats_entry_branch": st.INIT_ENTRY_SWITCH.get(entry, "default (ExpectedStat)" if o["pet_type"].startswith("MAX") else "pet type branch"),
                        "ghoul_or_wolf_ap_branch": entry in (st.PET_GHOUL, st.PET_SPIRIT_WOLF),
                    }
                except SourceError as exc:
                    run["oracle"] = {"status": "unresolved", "reason": str(exc)}
                tmpl = world.template.get(res["entry"]) or {}
                run["creature"] = {k: tmpl.get(k) for k in ("name", "unit_class", "family", "type", "AIName", "ScriptName", "BaseAttackTime")}
                run["ai"] = ("PetAI (IsPet, CreatureAISelector.cpp:88-91)" if res["trinity_unit_class"].startswith("Pet")
                             else f"ScriptName {tmpl.get('ScriptName')}" if tmpl.get("ScriptName")
                             else f"AIName {tmpl.get('AIName')}" if tmpl.get("AIName")
                             else "PetAI by permit (CONTROLABLE_GUARDIAN)" if res["summon_effect"].get("controllable")
                             else "highest-permit generic AI (not resolved here)")
                run["template_spells"] = [sp.spell_paths(s, entry=res["entry"]) for _, s in sorted(sp.creature_template_spells().get(res["entry"], {}).items()) if s]
            runs.append(run)
        rec["summons"] = runs
        fam = w.get("family")
        if fam:
            rec["family_spells"] = [r for r in ft["rows"] if r["family"] == fam]
        if w.get("spotlight_spells"):
            rec["spotlight"] = [sp.spell_paths(s) for s in w["spotlight_spells"]]
        if w.get("tame"):
            rec["tame_chain"] = [{"spell": s, "name": bundle.name(s), "effects": _effects(s),
                                  "script_bindings": bundle.script_names.get(s, [])} for s in w["tame"]]
        if w.get("search_entry"):
            e = w["search_entry"]
            hits = sorted({spell for spell, effs in inheritance._db2()["effects"].items() for x in effs
                           if x["Effect"] in ("28", "56") and int(x["EffectMiscValue_0"]) == e})
            rec["entry_search"] = {"entry": e, "summoning_spells_12_1": hits, "cast_effects": _effects(w["cast"]),
                                   "script_refs": "scripts/Spells/spell_paladin.cpp:83 names 86659 only as a cooldown target (:1482-1500)",
                                   "status": "unresolved" if not hits else "has-summon"}
        out.append(rec)
    # proc / modifier interaction witnesses
    hooks = json.loads((CORPORA.parent / "dummy-corpora" / "families.json").read_text(encoding="utf-8"))
    forward = [h for h in hooks.get("player_hooks", []) if "pet-owner-forward-cast" in json.dumps(h)]
    aura429 = next((a for a in sp.pet_aura_types() if a["aura"] == 429), None)
    interactions = {
        "pet_owner_forward_cast": {"rows": forward[:10], "count": len(forward),
                                   "trinity": "spell_pri_inescapable_torment (scripts/Spells/spell_priest.cpp:2593-2637): loads only with 373427; "
                                              "finds the Shadowfiend/Mindbender in m_Controlled by entry (19668/62982/224466, :316-318), "
                                              "makes it cast 373441 and extends its timer by 373427 EFFECT_1",
                                   "evidence_class": "trinity-consumer"},
        "pet_only_damage_multiplier": {"aura": aura429, "evidence_class": "trinity-consumer",
                                       "claim": "SPELL_AURA_MOD_SUMMON_DAMAGE (429) is HandleNULL: every 12.1 pet damage mastery / spec aura "
                                                "carrying it is inert in Trinity (unsupported by research != inert in Retail)"},
    }
    return {"provenance": {**st.provenance("python3 controlled_units.py witnesses-b"),
                           "world_database_corpora": world.sources,
                           "scope": "dummy_semantics.scope.Scope(Bundle(), include_class_skills=False)"},
            "standard_owner": {k: list(v) if isinstance(v, tuple) else v for k, v in inheritance.STANDARD_OWNER.items()},
            "witnesses": out, "interactions": interactions}


def cmd_witnesses(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else CORPORA / "witnesses-b.json"
    doc = build_witnesses()
    st.write_json(out, doc)
    print(f"wrote {out} ({len(doc['witnesses'])} witnesses)")
    return 0


def cmd_all_b(args: argparse.Namespace) -> int:
    from controlled_units import inheritance
    from controlled_units import spells as sp
    for fn in (st.cmd_stats, inheritance.cmd_inheritance, sp.cmd_spells, cmd_witnesses):
        rc = fn(argparse.Namespace(out=None, spell=None, entry=None))
        if rc:
            return rc
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("world-b", help="Track B world-db slice (pet-witness-templates.json)")
    p.add_argument("--tdb", default=str(WORKSPACE.parent / "bag" / "tdb" / PINNED_TDB))
    p.add_argument("--out")
    p.set_defaults(func=cmd_world_b)
    q = subparsers.add_parser("witnesses-b", help="write controlled-unit-corpora/witnesses-b.json")
    q.add_argument("--out")
    q.set_defaults(func=cmd_witnesses)
    r = subparsers.add_parser("all-b", help="regenerate stats/inheritance/spells/witnesses-b corpora (world-b is separate)")
    r.set_defaults(func=cmd_all_b)
