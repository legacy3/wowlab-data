#!/usr/bin/env python3
"""Extract TrinityCore's *server-side spell semantic* world-database layer.

Companion to ``tdb_proc_overlay.py`` (which covers proc policy).  This tool
replays the pinned TDB dump plus every ``sql/updates/world/master`` file with
the schema-driven interpreter in :mod:`tdb_replay` and emits the tables that
supply behaviour the client data does not encode.  Loader / consumer per table
(TrinityCore ``7f3d43b``):

  table                              loader                                consumer
  ---------------------------------  ------------------------------------  -------------------------------------------
  spell_script_names                 ObjectMgr::LoadSpellScriptNames       ScriptMgr::CreateSpell/AuraScripts
  serverside_spell(+_effect)         SpellMgr::LoadSpellInfoServerside     SpellInfo store (server-only spells)
  spell_linked_spell                 SpellMgr::LoadSpellLinked             Spell::finish (CAST), Spell::DoTriggersOnSpellHit (HIT),
                                                                           Aura::HandleAuraSpecificMods (AURA/REMOVE)
  spell_area                         SpellMgr::LoadSpellAreas              SpellInfo::CheckLocation, Player::UpdateAreaDependentAuras,
                                                                           Aura::HandleAuraSpecificMods
  spell_target_position              SpellMgr::LoadSpellTargetPositions    Spell::SelectImplicitDestTargets (TARGET_DEST_DB)
  spell_group / _stack_rules         SpellMgr::LoadSpellGroups/StackRules  Unit::_IsNoStackAuraDueToAura, AddAura stacking
  spell_learn_spell                  SpellMgr::LoadSpellLearnSpells        Player::LearnSpell chain
  spell_pet_auras                    SpellMgr::LoadSpellPetAuras           Spell::EffectDummy, AuraEffect::HandleAuraDummy -> Player::AddPetAura
  spell_required                     SpellMgr::LoadSpellRequired           Player::LearnSpell/RemoveSpell
  spell_threat                       SpellMgr::LoadSpellThreats            ThreatManager, Spell::HandleThreatSpells
  spell_custom_attr                  SpellMgr::LoadSpellInfoCustomAttributes  AttributesCu
  conditions (spell sources)         ConditionMgr::LoadConditions          Spell::CheckCast (17), implicit targets (13),
                                                                           Aura::CanProc (24), spellclick (18), vehicle (21),
                                                                           skill line ability (35)
  areatrigger_create_properties      AreaTriggerDataStore::LoadAreaTriggerTemplates  AreaTrigger::CreateAreaTrigger (ScriptName -> AreaTriggerAI)
  areatrigger_template(+_actions)    same                                  AreaTrigger actions (cast/add aura/teleport)
  creature_template (projected)      ObjectMgr::LoadCreatureTemplates      pet/guardian AI script binding (ScriptName/AIName)
  creature_template_spell            ObjectMgr::LoadCreatureTemplateSpells  pet action bars
  spell_scripts                      **no loader in this revision**        table exists in the dump only

Nothing here interprets a row.  Rows are Trinity's authoring, reported as such.

Usage::

    python3 scripts/research/tools/tdb_server_overlay.py \\
        --tdb ../../bag/tdb/TDB_full_world_1200.26021_2026_02_06.sql
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tdb_replay import Replay, parse_create_tables, replay_dump_lines, replay_updates  # noqa: E402

HERE = Path(__file__).resolve()
WOWLAB_DATA = HERE.parents[3]
WORKSPACE_PARENT = WOWLAB_DATA.parent
DEFAULT_OUT = WOWLAB_DATA / "docs/research/dummy-corpora/trinity-server-overlay.json"

FULL_TABLES = [
    "spell_script_names", "serverside_spell", "serverside_spell_effect",
    "spell_linked_spell", "spell_area", "spell_target_position", "spell_group",
    "spell_group_stack_rules", "spell_learn_spell", "spell_pet_auras",
    "spell_required", "spell_threat", "spell_custom_attr", "conditions",
    "areatrigger_create_properties", "areatrigger_template",
    "areatrigger_template_actions", "creature_template_spell", "spell_scripts",
]
PROJECTED = {
    "creature_template": ["entry", "name", "unit_class", "family", "type", "AIName", "ScriptName"],
}
#: ConditionMgr.h source types whose SourceEntry is a SpellID (or whose group is)
SPELL_CONDITION_SOURCES = {
    13: "CONDITION_SOURCE_TYPE_SPELL_IMPLICIT_TARGET",
    17: "CONDITION_SOURCE_TYPE_SPELL",
    18: "CONDITION_SOURCE_TYPE_SPELL_CLICK_EVENT",
    21: "CONDITION_SOURCE_TYPE_VEHICLE_SPELL",
    24: "CONDITION_SOURCE_TYPE_SPELL_PROC",
    35: "CONDITION_SOURCE_TYPE_SKILL_LINE_ABILITY",
}


def git(tc_root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tc_root), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tdb", type=Path, required=True)
    parser.add_argument("--tc-root", type=Path, default=WORKSPACE_PARENT / "TrinityCore")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    tc_root = args.tc_root.resolve()
    pinned = re.search(r'DATABASE_FULL_DATABASE\s+"([^"]+)"',
                       (tc_root / "revision_data.h.in.cmake").read_text()).group(1)
    if args.tdb.name != pinned:
        print(f"refusing: checkout pins {pinned}, got {args.tdb.name}", file=sys.stderr)
        return 2

    wanted = set(FULL_TABLES) | set(PROJECTED)
    base_text = args.tdb.read_text(encoding="utf-8", errors="replace")
    base_sha = hashlib.sha256(base_text.encode("utf-8", errors="replace")).hexdigest()
    schemas = parse_create_tables(base_text, wanted)
    missing = sorted(wanted - set(schemas))
    replay = Replay(schemas)
    unparsed: list[dict] = []
    counter: Counter = Counter()
    replay_dump_lines(replay, base_text, unparsed, counter, args.tdb.name)
    base_counts = {name: len(t.rows) for name, t in replay.state.items()}
    del base_text

    touched_files, total_files = replay_updates(replay, tc_root / "sql/updates/world/master", unparsed, counter)

    tables: dict[str, dict] = {}
    for name in FULL_TABLES:
        if name not in replay.state:
            continue
        st = replay.state[name]
        cols = st.columns
        if name == "conditions":
            # spell-keyed sources, reference rows, and every CONDITION_AURA (type 1) row from any source:
            # an aura condition on a gossip/smart/loot source is still a server-side read of that aura
            rows = [r for r in st.rows.values()
                    if r["SourceTypeOrReferenceId"] in SPELL_CONDITION_SOURCES
                    or r["SourceTypeOrReferenceId"] < 0
                    or r["ConditionTypeOrReference"] == 1]
        else:
            rows = list(st.rows.values())
        rows.sort(key=lambda r: tuple((0, v) if isinstance(v, (int, float)) else (1, str(v))
                                      for v in (r[k] for k in st.key)))
        tables[name] = {"columns": cols, "key": list(st.key),
                        "row_count_total": len(st.rows), "rows": [[r[c] for c in cols] for r in rows]}
    for name, cols in PROJECTED.items():
        if name not in replay.state:
            continue
        st = replay.state[name]
        # keep only rows with a non-default AI binding: the rest have no script layer
        rows = sorted((r for r in st.rows.values() if r["ScriptName"] or r["AIName"]),
                      key=lambda r: r[st.key[0]])
        tables[name] = {"columns": cols, "key": list(st.key), "projected": True,
                        "row_filter": "ScriptName != '' or AIName != ''",
                        "row_count_total": len(st.rows), "rows": [[r[c] for c in cols] for r in rows]}

    payload = {
        "provenance": {
            "trinitycore_commit": git(tc_root, "rev-parse", "HEAD"),
            "base_world_database": pinned,
            "base_world_database_sha256": base_sha,
            "tdb_release": "TDB1200.26021 (TDB_full_1200.26021_2026_02_06.7z, "
                           "sha256 48f0e2af7620ca70ec7054ff19d356255bc50e11d7829932015f28918f195588)",
            "update_directory": "sql/updates/world/master",
            "update_files_total": total_files,
            "update_files_touching_tracked_tables": touched_files,
            "base_row_counts": base_counts,
            "final_row_counts": {name: len(t.rows) for name, t in replay.state.items()},
            "applied": {" / ".join(k): v for k, v in sorted(counter.items())},
            "unparsed_statement_count": len(unparsed),
            "tables_missing_from_dump": missing,
            "conditions_filter": {str(k): v for k, v in SPELL_CONDITION_SOURCES.items()},
            "conditions_filter_extra": "all rows with ConditionTypeOrReference == 1 (CONDITION_AURA) regardless of source",
            "note": "schema-driven restricted replay; see tools/tdb_replay.py",
        },
        "schemas": {name: s.to_dict() for name, s in sorted(schemas.items())},
        "unparsed": unparsed,
        "tables": tables,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n", encoding="utf-8")
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({size / 1e6:.1f} MB); unparsed={len(unparsed)}; "
          f"rows={ {n: t['row_count_total'] for n, t in tables.items()} }")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
