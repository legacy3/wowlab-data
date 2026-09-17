"""Track I: real-data census of AreaTrigger-creating effects and the extended (script/AT/linked) scope."""

from __future__ import annotations

import json
import re

import pytest

from targeting import CORPORA, TC_ROOT, cmd_i
from targeting import areatriggers as at

CLASSES = {"understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved", "n/a"}


@pytest.fixture(scope="module")
def census(tg_ctx):
    return cmd_i.build_areatriggers()


@pytest.fixture(scope="module")
def extended(tg_ctx):
    return cmd_i.build_extended_scope()


def test_effect_population(census, tg_ctx):
    """Every current-player CREATE_AREATRIGGER(_2) / aura 395 effect is classified exactly once."""
    keys = set()
    n179 = 0
    for s in tg_ctx.scope.reach:
        for e in tg_ctx.data.effects(s):
            if e.effect in at.AT_EFFECTS or (e.effect and e.aura == at.SPELL_AURA_AREA_TRIGGER):
                keys.add(f"{s}:{e.index}")
                n179 += e.effect == 179
    assert set(census["effect_classes"]) == keys
    assert n179 == 51 and census["counts"]["by_creator"]["effect-179"] == 51
    assert census["counts"]["by_creator"]["effect-353"] == 2
    for v in census["effect_classes"].values():
        assert v["class"] in CLASSES and "areatrigger" in v["tags"] and isinstance(v["build_skew"], bool)
        assert v["tags"] == sorted(v["tags"])


def test_missing_rows_are_blocked(census, tg_ctx):
    """A MiscValue without a world-DB row is 'blocked' (AreaTrigger::Create fails), never guessed."""
    w = at.ATWorld(tg_ctx.bundle.world)
    for r in census["rows"]:
        loaded = w.create_properties(r["create_properties_id"]) is not None
        assert loaded == (r["create_properties"] is not None)
        if not loaded:
            assert r["class"] == "blocked" and "missing-create-properties" in r["tags"]
    assert census["counts"]["missing_create_properties_build_skew"] == 3


def test_world_db_consumers_are_verified(census):
    """Every ScriptName bound to an in-scope create-properties row is in the hand-verified table, and every
    table entry cites a line that defines/aliases that script in the pinned checkout."""
    names = {r["create_properties"]["script_name"] for r in census["rows"]
             if r["create_properties"] and r["create_properties"]["script_name"]}
    assert names <= set(at.AT_SCRIPTS)
    if not (TC_ROOT / "src/server/scripts/Spells/spell_mage.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    for name, s in at.AT_SCRIPTS.items():
        path, line = s["file_line"].rsplit(":", 1)
        text = (TC_ROOT / path).read_text(encoding="utf-8").splitlines()
        assert re.search(r"struct (areatrigger|at)_\w+", text[int(line) - 1]), (name, text[int(line) - 1])
        full = "\n".join(text)
        assert re.search(rf"\b{name}\b", full), name
    for e in at.EXTERNAL_CONSUMERS.values():
        path, _line = e["file_line"].rsplit(":", 1)
        assert (TC_ROOT / path).exists()


def test_script_cast_ids_match_index_constants(tg_ctx):
    """The verified table's child ids equal the script-index constants of the same file (no typos)."""
    consts = tg_ctx.bundle.index.constants
    for s in at.AT_SCRIPTS.values():
        file = s["file_line"].rsplit(":", 1)[0]
        known = set(consts.get(file, {}).values())
        for phase in ("enter", "update", "remove"):
            for item in s.get(phase, []):
                for sid in re.findall(r"\d{3,}", str(item.get("cast", ""))):
                    assert int(sid) in known, (s["file_line"], sid)


def test_no_flags_or_conditions_in_scope(census):
    """The template-gated filters are inert for current-player ATs (no action-set flags, no source-28 conditions)."""
    c = census["counts"]
    assert c["with_action_set_flags"] == 0 and c["with_conditions"] == 0
    assert c["with_template_actions"] == 1
    row = next(r for r in census["rows"] if r["key"] == "62618:0")
    assert row["create_properties"]["actions"] == [{"type": "ADDAURA", "param": 81782, "target": "FRIEND"}]


def test_position_only_children_reselect(census, extended):
    """Blizzard / Consecration damage / Earthquake children select by their own DEST area selectors."""
    rows = {r["spell"]: r for r in extended["rows"]}
    for child in (190357, 81297, 77478):
        assert child in rows, child
        assert rows[child]["reselects"], child
        assert "destination" in rows[child]["explicit_targets"]


def test_extended_scope_is_separate(extended, census, tg_ctx):
    """Children are outside reach and published only under extended_effect_classes."""
    reach = tg_ctx.scope.reach
    assert not {r["spell"] for r in extended["rows"]} & reach
    assert "effect_classes" not in extended
    for k, v in extended["extended_effect_classes"].items():
        assert int(k.split(":")[0]) not in reach
        assert v["class"] in CLASSES and "extended-scope" in v["tags"]
    assert not set(extended["extended_effect_classes"]) & set(census["effect_classes"])


def test_d_cross_reference(extended):
    """Track D's script-extended smart-heal spells (TG-D-13) are all in the extended population."""
    x = extended["d_cross_reference"]
    if not x["available"]:
        pytest.skip("smart-selection.json absent")
    assert x["missing_from_extended"] == []
    rows = {r["spell"]: r for r in extended["rows"]}
    for s in (73921, 204883, 155793):
        if str(s) in x["spells"]:
            assert rows[s]["in_d_script_extended"]


def test_linked_and_action_edges_carry_consumers(extended):
    for r in extended["rows"]:
        for e in r["sources"]:
            assert e["evidence"] in ("structural-inference", "script-consumer", "world-db-fact")
            if e["via"].startswith(("spell_linked_spell", "areatrigger")):
                assert e.get("file_line"), e


def test_corpora_are_current(census, extended):
    """The checked-in corpora equal a fresh build (regenerate with the cmd_i commands)."""
    for name, payload in (("areatriggers.json", census), ("extended-scope.json", extended)):
        on_disk = json.loads((CORPORA / name).read_text(encoding="utf-8"))
        assert on_disk == json.loads(json.dumps(payload)), name


def test_unordered_iteration_is_not_understood(census):
    """R1-06: an AT consumer that casts while iterating a GuidUnorderedSet (TG-I-03) is never 'understood'."""
    for r in census["rows"]:
        u = r.get("unordered_iteration")
        if u and u["recipient_order_depends"]:
            assert r["class"] != "understood" and "TG-I-03" in census["effect_classes"][r["key"]]["unknowns"]
        if r["class"] == "understood":
            assert "TG-I-03" not in census["effect_classes"][r["key"]]["unknowns"]
    assert census["effect_classes"]["5740:1"]["class"] == "fixture-dependent"
    assert census["effect_classes"]["109248:0"]["class"] == "fixture-dependent"
