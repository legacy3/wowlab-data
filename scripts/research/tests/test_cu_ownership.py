"""Track A2: ownership / attribution consumers and per-unit topology."""

from __future__ import annotations

import pytest

from controlled_units.ownership import (CONSUMERS, charmer_or_owner_or_own_guid, is_charmed_owned_by_player_or_player,
                                        per_category_topology, pet_owner_hooks, topology)
from controlled_units.population import Context
from cu_a_helpers import context, coordinates_in, trinity_line, trinity_root


def _topologies(spell: int) -> list[dict]:
    ctx = context()
    info = ctx.b.catalog.get(spell)
    scope = ctx.scope if spell in ctx.scope.reach else ctx.extended
    return [topology(ctx.record_for(spell, e, scope, "default")) for e in info.effects if Context.is_population_effect(e)]


def test_get_charmer_or_owner_or_own_guid_regression_truth_table():
    # pinned body (Object.cpp:1600-1602) returns the unit's own GUID exactly when a charmer/owner exists
    assert charmer_or_owner_or_own_guid("Pet-1", "Player-1") == "Pet-1"
    assert charmer_or_owner_or_own_guid("Player-1", None) is None
    assert charmer_or_owner_or_own_guid("Pet-1", "Player-1", pinned=False) == "Player-1"
    assert charmer_or_owner_or_own_guid("Player-1", None, pinned=False) == "Player-1"
    # consequences for Unit::IsCharmedOwnedByPlayerOrPlayer (Unit.h:1216)
    assert not is_charmed_owned_by_player_or_player("Player-1", None)          # plain player: false (was true)
    assert not is_charmed_owned_by_player_or_player("Pet-1", "Player-1")       # player pet: false (was true)
    assert is_charmed_owned_by_player_or_player("Player-1", "Creature-9")      # charmed player: true (was false)
    assert is_charmed_owned_by_player_or_player("Player-1", None, pinned=False)
    assert is_charmed_owned_by_player_or_player("Pet-1", "Player-1", pinned=False)


def test_regression_is_present_in_the_pinned_checkout():
    path = "src/server/game/Entities/Object/Object.cpp"
    assert "GetCharmerOrOwnerOrOwnGUID" in trinity_line(path, 1597)
    assert trinity_line(path, 1600).strip() == "if (!guid.IsEmpty())"
    assert trinity_line(path, 1601).strip() == "guid = GetGUID();"
    assert "GetCharmerOrOwnerOrOwnGUID().IsPlayer()" in trinity_line("src/server/game/Entities/Unit/Unit.h", 1216)
    assert "IsCharmedOwnedByPlayerOrPlayer" in trinity_line("src/server/game/Spells/Spell.cpp", 2774)


def test_consumer_table_is_well_formed_and_coordinates_exist():
    root = trinity_root()
    kinds = set()
    for c in CONSUMERS:
        assert {"function", "coordinate", "returns", "kind", "evidence_class"} <= set(c)
        kinds.add(c["kind"])
        coords = coordinates_in(c["coordinate"])
        assert coords, c["function"]
        for path, a, b in coords:
            n = len((root / path).read_text(encoding="utf-8", errors="replace").splitlines())
            assert 0 < a <= n and b <= n, (c["function"], path, a, b)
    assert {"collapse", "distinct", "collapse-pet-totem-only", "snapshot"} <= kinds


def test_spell_mod_owner_and_kill_proc_collapse_only_for_pet_and_totem():
    assert "creature->IsPet() || creature->IsTotem()" in trinity_line("src/server/game/Entities/Object/Object.cpp", 1656)
    assert "attacker->IsPet() || attacker->IsTotem()" in trinity_line("src/server/game/Entities/Unit/Unit.cpp", 11383)
    cats = per_category_topology()
    assert cats["totem"]["spell_mod_owner"].startswith("owner player")
    for cat in ("guardian", "controllable-guardian", "companion-minion", "puppet", "ally-summon", "wild-summon"):
        assert cats[cat]["spell_mod_owner"].startswith("none"), cat
        assert "not forwarded" in cats[cat]["kill_proc"], cat


def test_per_category_topology_controlled_membership():
    cats = per_category_topology()
    for cat in ("guardian", "controllable-guardian", "totem", "companion-minion", "puppet", "vehicle"):
        assert cats[cat]["in_owner_m_controlled"].startswith("yes"), cat
    for cat in ("ally-summon", "wild-summon", "vehicle-summon"):
        assert cats[cat]["in_owner_m_controlled"].startswith("no"), cat
    assert cats["ally-summon"]["owner"].startswith("caster") and cats["wild-summon"]["owner"] == "none"
    assert "MINION|GUARDIAN" in cats["guardian"]["threat_list"]


def test_spell_proc_actor_is_original_caster():
    fn = {c["function"]: c for c in CONSUMERS}
    row = fn["Spell::TargetInfo::DoDamageAndTriggers (spell hit procs)"]
    assert row["kind"] == "distinct (original-caster)"
    assert "m_originalCaster ? spell->m_originalCaster" in trinity_line("src/server/game/Spells/Spell.cpp", 2842)
    assert "ProcSkillsAndAuras(m_originalCaster" in trinity_line("src/server/game/Spells/Spell.cpp", 4223)


@pytest.mark.snapshot
def test_permanent_pet_topology_felguard():
    (t,) = _topologies(30146)
    assert t["cxx_class"] == "Pet" and t["category"] == "permanent-class-pet"
    assert t["spell_mod_owner"].startswith("owner player") and "also on GetOwner()" in t["kill_proc"]
    assert t["summoner_guid"].startswith("n/a") and "pet/totem" in t["threat_list"]


@pytest.mark.snapshot
def test_guardian_topology_raise_dead_and_charm_topology_mind_control():
    (g,) = _topologies(46585)
    assert g["cxx_class"] == "Guardian" and g["spell_mod_owner"].startswith("none")
    assert g["in_owner_m_controlled"].startswith("yes") and "Minion::InitStats" in g["owner"]
    charm = [t for t in _topologies(605)]
    assert charm and all(t["category"] == "possessed" and "Aura::GetCaster" in t["charmer"] for t in charm)
    assert all(t["owner"].startswith("unchanged") for t in charm)


@pytest.mark.snapshot
def test_pet_owner_forward_cast_witnesses():
    hooks = pet_owner_hooks(context())
    spells = {h["spell"] for h in hooks}
    assert {8092, 32379, 136511} <= spells
    assert all(h["evidence_class"] == "structural-inference" for h in hooks)


@pytest.mark.snapshot
def test_every_player_scope_record_has_topology_with_evidence():
    ctx = context()
    for r in ctx.records(ctx.scope, "default"):
        t = topology(r)
        assert t["category"] == r.category and t["evidence_class"]
        if r.category not in ("no-consumer", "unresolved", "lifecycle-op"):
            assert "unit" in t or "cxx_class" in t
