"""Track A1: controlled-unit vocabulary, branch mirrors, population and census."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from controlled_units import CORPORA, EVIDENCE_CLASSES
from controlled_units.population import CATEGORY_ORDER, is_permanent_pet_for
from controlled_units.vocabulary import (SUMMON_PROPERTIES_FLAGS, SummonProps, decode_summon_flags,
                                         default_branch_tempsummon_type, effect_branch,
                                         initstats_tempsummon_type, summon_branch, unit_mask_for,
                                         vocabulary_corpus)
from cu_a_helpers import census, context, trinity_line

JOIN = SUMMON_PROPERTIES_FLAGS["JoinSummonerSpawnGroup"][0]
DEMON_TIMEOUT = SUMMON_PROPERTIES_FLAGS["UseDemonTimeout"][0]


def props(control: int, title: int, slot: int = 0, flags0: int = 0) -> SummonProps:
    return SummonProps(1, control, 0, title, slot, flags0, 0)


# ---------------------------------------------------------------------------
# vocabulary / branch mirrors (no snapshot needed)
# ---------------------------------------------------------------------------

def test_summon_properties_flags_are_distinct_single_bits_covering_32_bits():
    bits = [b for b, _ in SUMMON_PROPERTIES_FLAGS.values()]
    assert len(set(bits)) == len(bits) == 32
    assert all(b & (b - 1) == 0 for b in bits)
    assert sum(bits) == 0xFFFFFFFF


def test_flags_decode_reads_flags0_only_and_flags_force_of_nature_join():
    d = decode_summon_flags(4213250)  # SummonProperties 3097 (Force of Nature child 248280)
    assert "JoinSummonerSpawnGroup" in d["names"] and d["unknown_bits"] == 0
    p = SummonProps(1, 1, 0, 1, 0, 0, JOIN)  # the bit only in Flags[1] is ignored (DB2Structure.h:4326)
    assert not p.has("JoinSummonerSpawnGroup")


@pytest.mark.parametrize("control,title,flags0,expected", [
    (2, 0, 0, ("UNIT_MASK_GUARDIAN", "Guardian")),     # Object.cpp:1200-1202
    (3, 0, 0, ("UNIT_MASK_PUPPET", "Puppet")),         # :1203-1205
    (4, 0, 0, ("UNIT_MASK_MINION", "Minion")),         # :1206-1209
    (5, 0, 0, ("UNIT_MASK_MINION", "Minion")),
    (1, 3, 0, ("UNIT_MASK_GUARDIAN", "Guardian")),     # Title Minion -> GUARDIAN, not MINION (:1215)
    (1, 4, 0, ("UNIT_MASK_TOTEM", "Totem")),
    (0, 11, 0, ("UNIT_MASK_TOTEM", "Totem")),          # Lightwell (:1221)
    (1, 9, 0, ("UNIT_MASK_SUMMON", "TempSummon")),
    (1, 5, 0, ("UNIT_MASK_MINION", "Minion")),         # Companion (:1228-1229)
    (1, 1, 0, ("UNIT_MASK_SUMMON", "TempSummon")),     # Title Pet without Join -> default arm (:1231-1234)
    (1, 1, JOIN, ("UNIT_MASK_GUARDIAN", "Guardian")),
    (6, 0, 0, (None, "nullptr")),                      # :1238-1239
])
def test_unit_mask_mirror(control, title, flags0, expected):
    assert unit_mask_for(props(control, title, flags0=flags0)) == expected
    assert unit_mask_for(None) == ("UNIT_MASK_SUMMON", "TempSummon")


def test_tempsummon_type_default_arm_differs_from_initstats_for_permanent_duration():
    p = props(1, 0)
    assert default_branch_tempsummon_type(0, p) == "TEMPSUMMON_DEAD_DESPAWN"
    assert default_branch_tempsummon_type(-1, p) == "TEMPSUMMON_MANUAL_DESPAWN"      # SpellEffects.cpp:2004-2005
    assert initstats_tempsummon_type(-1, p) == "TEMPSUMMON_DEAD_DESPAWN"             # TemporarySummon.cpp:197-198
    assert default_branch_tempsummon_type(5000, props(1, 0, flags0=DEMON_TIMEOUT)) == "TEMPSUMMON_TIMED_DESPAWN_OUT_OF_COMBAT"
    assert initstats_tempsummon_type(5000, None) == "TEMPSUMMON_TIMED_DESPAWN"


def test_summon_branch_fail_closed_cases():
    assert summon_branch(props(1, 2), 0, 1000, 0).category == "unresolved"          # entry 0
    assert summon_branch(None, 5, 1000, 999999).category == "unresolved"            # no SummonProperties row
    b = summon_branch(props(7, 2), 5, 1000, 0)
    assert b.category == "unresolved" and b.cxx_class == "nullptr" and b.evidence_class == "unresolved"


def test_ally_default_arm_sets_owner_without_controlled_and_wild_sets_demon_creator():
    ally = summon_branch(props(1, 0), 5, 1000, 0)
    wild = summon_branch(props(0, 0), 5, 1000, 0)
    assert ally.category == "ally-summon" and ally.owner_guid.startswith("caster") and "none" in ally.creator_guid
    assert any("NOT inserted in owner->m_Controlled" in n for n in ally.notes)
    assert wild.category == "wild-summon" and wild.owner_guid == "none" and wild.demon_creator.startswith("caster")


def test_title_pet_without_join_is_plain_tempsummon_via_summonguardian():
    b = summon_branch(props(1, 1), 5, 1000, 0)
    assert b.summon_path == "SummonGuardian" and b.cxx_class == "TempSummon"
    assert b.category == "wild-summon-via-SummonGuardian" and b.owner_guid.startswith("none")
    g = summon_branch(props(2, 2), 5, 1000, 0)
    assert g.category == "controllable-guardian" and g.controllable_guardian


@settings(max_examples=300, deadline=None)
@given(control=st.integers(-1, 8), title=st.integers(0, 50), slot=st.integers(-1, 6),
       flags0=st.integers(0, 0xFFFFFFFF), duration=st.one_of(st.none(), st.integers(-1, 10**7)), entry=st.integers(0, 3))
def test_branch_is_total_and_consistent_with_mask(control, title, slot, flags0, duration, entry):
    p = props(control, title, slot, flags0)
    b = summon_branch(p, entry, duration, 0)
    assert b.category in CATEGORY_ORDER
    assert b.evidence_class in EVIDENCE_CLASSES
    if b.category != "unresolved":
        assert b.cxx_class == unit_mask_for(p)[1]
        assert b.tempsummon_type is not None and b.coordinates


def test_vocabulary_corpus_is_deterministic_and_branch_table_complete():
    a, b = json.dumps(vocabulary_corpus()), json.dumps(vocabulary_corpus())
    assert a == b
    table = vocabulary_corpus()["branch_table"]
    assert len(table) == 7 * 11 * 2
    assert {r["category"] for r in table} <= set(CATEGORY_ORDER)


def test_effect_branch_null_handlers_are_no_consumer():
    for eff in (135, 168, 188, 199, 260):
        assert effect_branch(eff, 0, 0, 0, None, None)["category"] == "no-consumer"
    assert effect_branch(6, 6, 0, 0, None, None)["category"] == "charmed"
    assert effect_branch(6, 378, 0, 0, None, None)["category"] == "possessed-own-pet"
    assert effect_branch(6, 999, 0, 0, None, None)["category"] == "unresolved"


def test_is_permanent_pet_for_mirror():
    assert is_permanent_pet_for(9, "SUMMON_PET", 3) is True        # warlock demon
    assert is_permanent_pet_for(8, "SUMMON_PET", 4) is True        # mage elemental
    assert is_permanent_pet_for(6, "SUMMON_PET", 3) is False       # DK needs undead
    assert is_permanent_pet_for(5, "SUMMON_PET", 3) is False       # other classes never
    assert is_permanent_pet_for(9, "SUMMON_PET", None) is None     # fail closed without the template
    assert is_permanent_pet_for(1, "HUNTER_PET", None) is True


def test_trinity_anchors_for_branch_mirrors():
    assert "void Spell::EffectSummonType()" in trinity_line("src/server/game/Spells/SpellEffects.cpp", 1867)
    assert "JoinSummonerSpawnGroup" in trinity_line("src/server/game/Entities/Object/Object.cpp", 1232)
    assert "summon->SetCreatorGUID(caster->GetGUID())" in trinity_line("src/server/game/Spells/SpellEffects.cpp", 2082)
    assert "SummonTitle::Pet" in trinity_line("src/server/game/Entities/Creature/TemporarySummon.cpp", 518)
    assert "Flags[0]" in trinity_line("src/server/game/DataStores/DB2Structure.h", 4326)


# ---------------------------------------------------------------------------
# population / census (snapshot)
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_pinned_default_population_counts():
    c = census().counts(census().default)
    assert (c["records"], c["spells"], c["creature_entries"]) == (62, 61, 53)
    assert c["by_category"] == {"permanent-class-pet": 2, "controllable-guardian": 4, "guardian": 25, "totem": 17,
                                "companion-minion": 1, "ally-summon": 5, "wild-summon": 3, "charmed": 2,
                                "possessed": 1, "possessed-own-pet": 1, "no-consumer": 1}
    assert c["by_cxx_class"] == {"-": 5, "Guardian": 29, "Minion": 1, "Pet": 2, "TempSummon": 8, "Totem": 17}
    assert c["build_skew_added_spells"] == 2


@pytest.mark.snapshot
def test_build_skew_spells_in_default_population():
    assert {r.spell_id for r in census().default if r.build_skew_added} == {1304581, 1308399}


@pytest.mark.snapshot
def test_every_default_record_has_a_branch_or_is_unresolved():
    for r in census().default:
        assert r.category in CATEGORY_ORDER
        assert r.evidence_class in EVIDENCE_CLASSES
        if r.category == "unresolved":
            assert r.evidence_class == "unresolved" and r.branch.get("notes")
        else:
            assert r.handler != "?" and r.branch.get("coordinates"), r.spell_id
        assert r.scope == "default" and r.spell_id in context().scope.reach


@pytest.mark.snapshot
def test_population_is_difficulty_none_only_and_census_deterministic():
    ctx = context()
    spells = {r.spell_id for r in census().default}
    assert ctx.other_difficulty_rows(spells) == 0
    again = [r.to_dict() for r in ctx.records(ctx.scope, "default")]
    assert json.dumps(again, sort_keys=True) == json.dumps([r.to_dict() for r in census().default], sort_keys=True)


@pytest.mark.snapshot
def test_tdb_join_status_is_explicit():
    ctx = context()
    statuses = {r.tdb_status for r in census().default if r.creature_entry}
    if not ctx.tdb.present:
        assert statuses == {"corpus-missing"}
        return
    assert statuses <= {"present", "absent"}
    absent = {r.spell_id for r in census().default if r.tdb_status == "absent"}
    unresolved = {u["spell"] for u in census().unresolved(census().default)}
    assert absent <= unresolved
    for r in census().default:
        if r.tdb_status == "present":
            assert r.tdb_template["entry"] == r.creature_entry


@pytest.mark.snapshot
def test_pinned_tdb_join_counts():
    ctx = context()
    if not ctx.tdb.present:
        pytest.skip("creature-templates.json not generated")
    assert len(ctx.tdb.templates) == 1780
    assert census().counts(census().default)["tdb_template"] == {"absent": 2, "not-applicable": 5, "present": 55}
    absent = {r.spell_id: r.build_skew_added for r in census().default if r.tdb_status == "absent"}
    assert absent == {1304581: True, 1308399: True}   # in default scope, TDB absence == build skew exactly
    cs = census().unresolved(census().class_skill)
    from collections import Counter
    assert Counter(u["absence_class"] for u in cs if u["tdb"] == "absent") == {"build-skew": 42, "world-db-gap": 22}
    felguard = next(r for r in census().default if r.spell_id == 30146)
    assert felguard.branch["creature_type"] == 3 and felguard.branch["is_permanent_pet_for"] == {"9": True}


@pytest.mark.snapshot
def test_script_cast_and_serverside_are_separate_and_not_player_referenced():
    c = census()
    assert {r.spell_id for r in c.script_cast} >= {392990}
    assert all(r.evidence_class == "structural-inference" for r in c.script_cast)
    assert not any(s["referenced_by_player_hook"] or s["linked_from_player_scope"] for s in c.serverside)


@pytest.mark.snapshot
def test_trigger_child_classification_for_tame_and_force_of_nature():
    from controlled_units.witnesses_a import classify
    tame = classify(context(), 1515)
    assert tame["category"] == ["hunter-pet"] and tame["trigger_children"][0]["child"] == 13481
    fon = classify(context(), 205636)
    assert fon["category"] == ["controllable-guardian"] and fon["dummy_effects"]
    fire = classify(context(), 198067)  # Dummy, no bound script creating a unit
    assert fire["category"] == "unresolved" and fire["evidence_class"] == "unresolved" and fire["reopen_condition"]


def test_written_corpora_have_provenance_first_and_size_limits():
    for name in ("vocabulary", "population", "census", "ownership", "lifecycle", "witnesses-a"):
        path = CORPORA / f"{name}.json"
        if not path.exists():
            pytest.skip(f"{path} not generated")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert next(iter(data)) == "provenance"
        prov = data["provenance"]
        assert prov["snapshot_build"] == "12.1.0.69497" and prov["trinitycore_commit"].startswith("7f3d43b7")
        assert "generator" in prov
        assert path.stat().st_size < 5_000_000, name


@pytest.mark.snapshot
def test_creature_id_list_is_committed_reproducible_and_covers_every_census_and_witness_entry():
    """creature-templates.json was extracted with --keep ...=@ids; the id set must be derivable and complete."""
    from controlled_units import WORLD_DB_CORPORA
    from controlled_units.population import creature_ids_corpus
    from controlled_units.witnesses_b import WITNESSES
    path = WORLD_DB_CORPORA / "creature-templates.ids.json"
    if not path.exists():
        pytest.skip("creature-templates.ids.json not generated")
    committed = json.loads(path.read_text(encoding="utf-8"))
    assert next(iter(committed)) == "provenance"
    fresh = creature_ids_corpus(context())
    assert fresh["ids"] == committed["ids"] and committed["count"] == len(committed["ids"]) == 1847
    ids = set(committed["ids"])
    c = census()
    for r in list(c.default) + list(c.class_skill) + list(c.script_cast):
        if r.creature_entry:
            assert r.creature_entry in ids, (r.spell_id, r.creature_entry)
    for s in c.serverside:
        if s["effect"] in (28, 56, 153) and s["creature_entry"]:
            assert s["creature_entry"] in ids
    # Track B witness summons: every creature entry of a DIFFICULTY_NONE 28/56 effect of a witness spell
    ctx = context()
    for w in WITNESSES:
        for spell in [w["summon"], *w.get("alt_summons", [])]:
            info = ctx.b.catalog.get(spell) if spell else None
            for eff in (info.effects if info else []):
                if eff.effect in (28, 56) and int(eff.misc0):
                    assert int(eff.misc0) in ids, (w["id"], spell, eff.misc0)
        if w.get("search_entry"):
            assert w["search_entry"] in ids
    if ctx.tdb.present:
        assert set(ctx.tdb.templates) <= ids
