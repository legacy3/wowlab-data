"""Dependency graph, input inventory and initial-state inventory."""

from __future__ import annotations

import json

import pytest

from cp_e_helpers import compiled, fixture_names
from character_prep import CORPORA, EVIDENCE_CLASSES, SourceError
from character_prep import graph as graph_mod
from character_prep.fixture import FIXTURE_SCHEMA, canonical_json
from character_prep.graph import DERIVED, DERIVED_FROM, Graph, build_graph
from character_prep.inputs import INITIAL_STATE_CLASSES, INPUT_INVENTORY, INPUT_KINDS, input_inventory_document


def test_graph_is_acyclic_and_complete():
    g = build_graph()
    order = g.topological_order()
    assert sorted(order) == sorted(g.nodes)
    pos = {n: i for i, n in enumerate(order)}
    assert all(pos[e["from"]] < pos[e["to"]] for e in g.edges)


def test_every_derived_node_has_owner_and_evidence():
    g = build_graph()
    derived = [n for n in g.nodes.values() if n["kind"] == "derived"]
    assert {n["id"] for n in derived} == set(DERIVED)
    for n in g.nodes.values():
        assert n["evidence_class"] in EVIDENCE_CLASSES
        assert n.get("owner")
    for n in derived:
        assert n["consumer"]


@pytest.mark.snapshot
def test_compiler_dependencies_match_graph_edges():
    edges = {(a, b) for a, b in DERIVED_FROM}
    for name in fixture_names():
        deps = {(e["from"], e["to"]) for e in compiled(name)["dependencies"]}
        assert deps == edges, (name, deps ^ edges)
        for key, node in compiled(name)["derived"].items():
            assert {(src, f"/derived/{key}") for src in node["derived_from"]} <= edges, key


def test_upstream_of_stats_reaches_the_inputs():
    ups = build_graph().upstream("/derived/stats")
    assert {"/fixture/equipment", "/fixture/traits", "/server_inputs/base_stats", "/fixture/identity",
            "research:charstats", "research:gearing"} <= set(ups)


def test_missing_research_degrades_downstream(monkeypatch):
    bogus = [dict(r) for r in graph_mod.RESEARCH]
    for r in bogus:
        if r["id"] == "research:gearing":
            r["module"] = "gearing_does_not_exist"
    monkeypatch.setattr(graph_mod, "RESEARCH", bogus)
    g = build_graph()
    assert g.nodes["research:gearing"]["status"] == "unresolved"
    assert g.nodes["/derived/gear"]["evidence_class"] == "unresolved"
    assert g.nodes["/derived/identity"]["status"] == "resolved"


def test_missing_hook_attribute_is_unresolved(monkeypatch):
    bogus = [dict(r) for r in graph_mod.RESEARCH]
    for r in bogus:
        if r["id"] == "hook:weapon_combat":
            r["attribute"] = "NO_SUCH_HOOK"
    monkeypatch.setattr(graph_mod, "RESEARCH", bogus)
    g = build_graph()
    assert g.nodes["hook:weapon_combat"]["status"] == "unresolved"
    assert g.nodes["/derived/weapon"]["status"] == "unresolved"


def test_cycle_is_detected():
    g = Graph()
    g.add_node("a", kind="derived", evidence_class="db2-fact")
    g.add_node("b", kind="derived", evidence_class="db2-fact")
    g.add_edge("a", "b", "derived-from")
    g.add_edge("b", "a", "derived-from")
    with pytest.raises(SourceError, match="cycle"):
        g.topological_order()


def test_edge_to_unknown_node_rejected():
    g = Graph()
    g.add_node("a", kind="input", evidence_class="db2-fact")
    with pytest.raises(SourceError):
        g.add_edge("a", "missing", "derived-from")


def test_dot_output_lists_every_node():
    g = build_graph()
    dot = g.to_dot()
    assert dot.startswith("digraph character_prep {")
    assert all(f'"{n}"' in dot for n in g.nodes)


@pytest.mark.snapshot
def test_committed_graph_is_current():
    assert (CORPORA / "dependency-graph.json").read_text(encoding="utf-8") == canonical_json(build_graph().to_dict())


# -- input inventory (C1) ---------------------------------------------------------------------------------------------

def test_inventory_classification_is_valid():
    doc = input_inventory_document()
    assert set(doc["counts"]) == set(INPUT_KINDS)
    names = [r["name"] for r in INPUT_INVENTORY]
    assert len(names) == len(set(names))
    for r in INPUT_INVENTORY:
        if r["kind"] == "derived":
            assert r["required"] == "never" and r["derived_path"], r["name"]
        if r["kind"] == "identity":
            # an identity input the format cannot carry yet is an explicit, unresolved format gap (closeout review R3)
            assert r["fixture_path"] or r["evidence_class"] == "unresolved", r["name"]


def test_inventory_fixture_paths_exist_in_schema():
    top = set(FIXTURE_SCHEMA["properties"])
    for r in INPUT_INVENTORY:
        if r["fixture_path"]:
            assert r["fixture_path"].split("/")[1] in top, r["name"]


@pytest.mark.snapshot
def test_inventory_derived_paths_exist_in_compiled_output():
    from character_prep.compiler import resolve_pointer
    out = compiled("arms-warrior-plate-2h")
    for r in INPUT_INVENTORY:
        if r["derived_path"]:
            resolve_pointer(out, r["derived_path"])


def test_inventory_required_classes_present():
    kinds = {r["name"]: r["kind"] for r in INPUT_INVENTORY}
    assert kinds["appearance / customization"] == "excluded"
    assert kinds["legacy azerite (Heart of Azeroth, empowered powers, essences)"] == "excluded"
    assert kinds["PvP item-level context"] == "encounter"
    assert kinds["item context (difficulty / source)"] == "identity"
    assert kinds["set membership and thresholds"] == "derived"
    assert kinds["base primary stats (player_classlevelstats + player_racestats)"] == "server"


@pytest.mark.snapshot
def test_committed_inventory_is_current():
    assert (CORPORA / "input-inventory.json").read_text(encoding="utf-8") == canonical_json(input_inventory_document())


# -- initial state (C3) -----------------------------------------------------------------------------------------------

def test_initial_state_classes_cover_c3():
    classes = {c["class"] for c in INITIAL_STATE_CLASSES}
    assert classes == {"immutable-facts", "compiled-program", "mutable-combat-state", "active-at-start-passives",
                       "cooldowns-and-charges", "weapon-swing-state", "pet-state", "rng-identity", "encounter-only"}
    assert all(c["evidence_class"] in EVIDENCE_CLASSES for c in INITIAL_STATE_CLASSES)


@pytest.mark.snapshot
def test_committed_initial_state_is_current_and_has_power_facts():
    doc = json.loads((CORPORA / "initial-state.json").read_text(encoding="utf-8"))
    rows = {r["name"]: r for r in doc["powers"]["power_types"]}
    assert rows["Rage"]["max_base"] == 1000 and rows["Energy"]["max_base"] == 100
    assert rows["SoulShards"]["center"] == 30
    assert doc["powers"]["class_powers"]["1"] == [1, 10, 23, 24, 25]
    from character_prep.inputs import initial_state_document
    from cp_e_helpers import compiler
    assert canonical_json(doc) == canonical_json(initial_state_document(compiler().snap))
