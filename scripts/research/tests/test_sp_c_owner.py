"""Track C: owner sidecars and the immutable-preparation lifetime policy.

Pins the vocabulary in :mod:`selected_package.sidecars` against the snapshot: the
five reference providers must come out verdict-clean, every negative class must have
a real witness, the attribute-416 divergence from Core must be the deliberate one,
and an unrecognised spell-keyed table must fail closed rather than be ignored.
"""

from __future__ import annotations

import json

import pytest

from gearing.tables import DEFAULT_TABLES


@pytest.fixture(scope="module")
def source():
    if not DEFAULT_TABLES.is_dir():
        pytest.skip(f"no table snapshot at {DEFAULT_TABLES}")
    from procs.source import Source
    return Source()


@pytest.fixture(scope="module")
def census(source):
    from selected_package.sidecars import SidecarCensus
    return SidecarCensus(source)


@pytest.fixture(scope="module")
def provenance(source):
    from selected_package.provenance import Provenance
    return Provenance(source=source)


@pytest.fixture(scope="module")
def corpora():
    from selected_package import CORPORA
    policy = CORPORA / "owner-policy.json"
    negatives = CORPORA / "owner-negatives.json"
    if not policy.exists() or not negatives.exists():
        pytest.skip("owner-policy.json / owner-negatives.json not generated yet")
    return (json.loads(policy.read_text(encoding="utf-8")),
            json.loads(negatives.read_text(encoding="utf-8")))


#: entry id -> (effective rank, provider spell, name).  Entries, not providers: the
#: provider is derived, so a provenance regression shows up here too.
REFERENCE = {
    101510: (1, 231602, "Improved Vivify"),
    117409: (1, 429638, "Martial Expert"),
    115483: (2, 406154, "Heart of the Crusader"),
    137000: (1, 1269312, "Phalanx"),
    136808: (1, 426563, "Ephemeral Bond"),
}


# ---------------------------------------------------------------------------
# the vocabulary itself
# ---------------------------------------------------------------------------

def test_every_spell_keyed_table_has_a_rule(census):
    """The census is found by header inspection, never from a hand-kept list."""
    from selected_package.sidecars import SIDECAR_POLICY, POLICY_CLASSES

    found = census.spell_keyed_tables()
    assert set(found) == set(SIDECAR_POLICY)
    # 72 tables carry a literal SpellID column; Spell and SpellName are keyed by ID.
    assert len(found) == 74
    assert sum(1 for key in found.values() if key == "SpellID") == 72
    assert found["Spell"] == found["SpellName"] == "ID"
    for rule in SIDECAR_POLICY.values():
        assert rule.policy_class in POLICY_CLASSES
        assert rule.justification.strip()
        assert not (rule.inert and rule.blocking)
        assert not (rule.presence_blocks and rule.blocking)


def test_inertness_follows_the_declared_class(census):
    """A/B/H are inert by definition; D/E/F/G never are; class C states its own case."""
    from selected_package.sidecars import SIDECAR_POLICY, INERT_BY_CLASS, NEVER_INERT

    for rule in SIDECAR_POLICY.values():
        if rule.policy_class in INERT_BY_CLASS:
            assert rule.inert, rule.table
        if rule.policy_class in NEVER_INERT:
            assert not rule.inert, rule.table
        if rule.policy_class == "C":
            # every class-C rule here is an inbound grant reference, and says so
            assert rule.inert and "grant" in rule.justification or "acquisition" in rule.justification


def test_pinned_headers_match_the_snapshot(census):
    """Header drift fails closed instead of silently reclassifying a column."""
    from selected_package.sidecars import SIDECAR_POLICY

    for table in census.tables:
        rows = census.rows(table)          # raises FailClosed on drift
        assert isinstance(rows, dict)
        assert SIDECAR_POLICY[table].key in SIDECAR_POLICY[table].columns


# ---------------------------------------------------------------------------
# the five reference providers
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
@pytest.mark.parametrize("entry", sorted(REFERENCE))
def test_reference_providers_are_verdict_clean(entry, census, provenance):
    """No admitted reference package carries a non-inert sidecar."""
    from selected_package.provenance import TraitSpellError

    rank, spell, name = REFERENCE[entry]
    resolved = provenance.resolve(entry, rank)
    assert not isinstance(resolved, TraitSpellError), (entry, resolved)
    assert resolved.spell == spell, name

    verdict = census.classify(spell)
    assert verdict.blockers == [], (name, verdict.blockers)
    assert verdict.inert
    # the verdict is a fold over real rows, not an empty census
    assert "SpellMisc" in verdict.tables() and "SpellEffect" in verdict.tables()


@pytest.mark.snapshot
def test_heart_and_phalanx_are_clean_only_through_a_proof(census):
    """Both carry a SpellAuraOptions row Core admits only by naming them.

    They come out inert here because Trinity generates no ``SpellProcEntry`` for
    either -- none of their effects is a trigger aura (SpellMgr.cpp:1807-1820) -- so
    ``Aura::CheckProc`` is unreachable (SpellAuras.cpp:1007).  Withdraw the proof and
    both must block, otherwise the rule is admitting them for free.
    """
    for spell in (406154, 1269312):
        assert census.rows("SpellAuraOptions").get(spell), spell
        empty, reason = census.proc_generation_is_empty(spell)
        assert empty, (spell, reason)
        assert census.classify(spell, proc_inert=True).blockers == []
        blocked = census.classify(spell, proc_inert=False)
        assert any("SpellAuraOptions" in b for b in blocked.blockers), spell


# ---------------------------------------------------------------------------
# one witness per negative class
# ---------------------------------------------------------------------------

#: spell -> (negative class, the substring its blocker must contain).
NEGATIVE_WITNESS = {
    392931: ("caster_aura_state", "SpellAuraRestrictions"),      # Cruelty, CasterAuraState 17
    373456: ("caster_aura_state", "SpellAuraRestrictions"),      # Unwavering Will, state 23
    375542: ("caster_aura_state", "SpellAuraRestrictions"),      # Exuberance, state 23
    139: ("finite_duration", "SpellMisc"),                       # Renew, DurationIndex 8
    772: ("equipped_items", "SpellEquippedItems"),               # Rend
    528: ("shapeshift", "SpellShapeshift"),                      # Dispel Magic
    774: ("aura_interrupt", "SpellInterrupts"),                  # Rejuvenation
    633: ("cooldown", "SpellCooldowns"),                         # Lay on Hands
    370: ("power_cost", "SpellPower"),                           # Purge
    365729: ("reagents", "SpellReagents"),                       # Primal Molten Shortblade
}


@pytest.mark.snapshot
@pytest.mark.parametrize("spell", sorted(NEGATIVE_WITNESS))
def test_negative_witness_blocks(spell, census):
    _klass, table = NEGATIVE_WITNESS[spell]
    verdict = census.classify(spell)
    assert verdict.blockers, spell
    assert any(table in blocker for blocker in verdict.blockers), (spell, verdict.blockers)


@pytest.mark.snapshot
def test_cruelty_is_the_caster_aura_state_witness(census):
    """The mission names Cruelty; it is spell 392931 with ``CasterAuraState`` 17."""
    rows = census.rows("SpellAuraRestrictions")[392931]
    assert len(rows) == 1
    assert int(rows[0]["CasterAuraState"]) == 17
    assert int(rows[0]["DifficultyID"]) == 0


@pytest.mark.snapshot
def test_a_non_passive_provider_blocks(census, provenance):
    """A provider without attribute raw 6 is an active spell, not a preparation."""
    from selected_package.sidecars import attribute_ids

    misc = [row for row in census.rows("SpellMisc")[118]         # Polymorph
            if int(row["DifficultyID"]) == 0]
    assert 6 not in attribute_ids(misc[0])
    assert any("Passive raw 6" in b for b in census.classify(118).blockers)


@pytest.mark.snapshot
def test_uncatalogued_attribute_is_build_skew_not_silence(census):
    """An attribute Core's catalog does not mention blocks as class I, never as inert."""
    verdict = census.classify(118)                                # Polymorph carries raw 30
    assert any("uncatalogued" in b for b in verdict.blockers), verdict.blockers
    skew = [hit for hit in verdict.hits if hit.policy_class == "I"]
    assert skew and all(not hit.inert for hit in skew)


# ---------------------------------------------------------------------------
# attribute 416
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_attribute_416_is_allow_class_ability_procs(census):
    """416 is ``AllowClassAbilityProcs``, catalogued ``disabled`` by Core."""
    from selected_package import coreref

    entry = coreref.spell_attributes()[416]
    assert entry.name == "AllowClassAbilityProcs"
    assert entry.support == "disabled"
    assert coreref.spell_attributes()[415].name == "OnlyProcFromClassAbilities"


@pytest.mark.snapshot
def test_attribute_416_is_inert_for_a_passive_owner(census):
    """The deliberate divergence from Core, and the only one on the attribute path.

    Trinity reads 416 off the *triggering* spell inside ``Aura::CheckProc``
    (SpellAuras.cpp:1859).  A permanently applied passive is never that spell, so the
    attribute cannot change its application or its prepared amounts.
    """
    from selected_package.sidecars import PASSIVE_INERT_ATTRIBUTES, attribute_ids

    assert set(PASSIVE_INERT_ATTRIBUTES) == {415, 416}
    for policy_class, why, consumer in PASSIVE_INERT_ATTRIBUTES.values():
        assert policy_class == "G" and why and consumer.startswith("src/server/game/")

    # 56377 Splitting Ice: a real selected provider carrying 416, clean under this rule
    # and blocked by owner.py's verbatim port of Core.  382 providers change verdict
    # this way; 145 effect-clean variants carry 416, 85 of them blocked by nothing else.
    misc = [row for row in census.rows("SpellMisc")[56377] if int(row["DifficultyID"]) == 0]
    assert 416 in attribute_ids(misc[0])
    verdict = census.classify(56377)
    assert verdict.blockers == [], verdict.blockers
    hits = [hit for hit in verdict.hits if hit.table == "SpellMisc.Attributes[416]"]
    assert len(hits) == 1 and hits[0].inert and "Passive owner" in hits[0].reason

    # and Core's own catalog still calls it disabled, so this is a divergence on purpose
    from selected_package.owner import Owners

    assert any("416" in blocker for blocker in Owners(census.source).policy(56377).blockers)


# ---------------------------------------------------------------------------
# fail closed
# ---------------------------------------------------------------------------

def test_unknown_sidecar_table_fails_closed(monkeypatch, source):
    """A new spell-keyed table in data/tables must stop the census, not be ignored."""
    from selected_package import FailClosed
    from selected_package.sidecars import SidecarCensus

    real = SidecarCensus.spell_keyed_tables
    monkeypatch.setattr(
        SidecarCensus, "spell_keyed_tables",
        staticmethod(lambda: {**real(), "SpellSomethingNew": "SpellID"}))
    with pytest.raises(FailClosed, match="no SIDECAR_POLICY rule"):
        SidecarCensus(source)


def test_rows_of_an_unruled_table_fail_closed(census):
    from selected_package import FailClosed

    with pytest.raises(FailClosed, match="no SIDECAR_POLICY rule"):
        census.rows("SpellDuration")


def test_header_drift_fails_closed(census, source):
    """A pinned header that no longer matches the CSV stops the census."""
    import dataclasses

    from selected_package import FailClosed
    from selected_package.sidecars import SIDECAR_POLICY, SidecarCensus

    fresh = SidecarCensus(source)
    drifted = dataclasses.replace(
        SIDECAR_POLICY["SpellShapeshift"],
        columns=SIDECAR_POLICY["SpellShapeshift"].columns + ("NotAColumn",))
    try:
        SIDECAR_POLICY["SpellShapeshift"] = drifted
        with pytest.raises(FailClosed, match="drifted"):
            fresh.rows("SpellShapeshift")
    finally:
        SIDECAR_POLICY["SpellShapeshift"] = dataclasses.replace(
            drifted, columns=drifted.columns[:-1])


def test_a_bad_rule_is_rejected_at_construction():
    from selected_package import FailClosed
    from selected_package.sidecars import SidecarRule

    with pytest.raises(FailClosed, match="may not be inert"):
        SidecarRule(table="X", key="SpellID", policy_class="E", inert=True,
                    presence_blocks=False, blocking=(), columns=("ID", "SpellID"),
                    justification="x", evidence="source")
    with pytest.raises(FailClosed, match="absent from the header"):
        SidecarRule(table="X", key="SpellID", policy_class="E", inert=False,
                    presence_blocks=False, blocking=("Nope",), columns=("ID", "SpellID"),
                    justification="x", evidence="source")
    with pytest.raises(FailClosed, match="unknown evidence class"):
        SidecarRule(table="X", key="SpellID", policy_class="A", inert=True,
                    presence_blocks=False, blocking=(), columns=("ID", "SpellID"),
                    justification="x", evidence="vibes")


# ---------------------------------------------------------------------------
# the corpora
# ---------------------------------------------------------------------------

@pytest.mark.snapshot
def test_owner_policy_corpus_matches_the_module(corpora, census):
    from selected_package.sidecars import SIDECAR_POLICY

    policy, _negatives = corpora
    assert policy["spell_keyed_tables"] == len(SIDECAR_POLICY) == 74
    assert policy["tables_used_by_population"] == 52
    assert policy["population"] == {"provider_spells": 5143, "provider_entries": 8279,
                                    "provider_variants": 8846}
    recorded = {row["table"]: row for row in policy["tables"]}
    assert set(recorded) == set(SIDECAR_POLICY)
    for table, row in recorded.items():
        assert row["policy_class"] == SIDECAR_POLICY[table].policy_class
        assert row["inert"] is SIDECAR_POLICY[table].inert
        if row["providers_with_row"]:
            assert row["distinct_nonzero_shapes"]
    # no table may be left unclassified
    assert all(row["policy_class"] != "I" for row in policy["tables"])


@pytest.mark.snapshot
def test_owner_negatives_corpus_counts(corpora):
    _policy, negatives = corpora
    expected = {
        "caster_aura_state": 3, "aura_restriction_any": 46, "not_passive": 1191,
        "finite_duration": 408, "shapeshift": 157, "equipped_items": 122,
        "aura_interrupt": 226, "cooldown": 623, "power_cost": 298, "reagents": 444,
        "attribute_unsupported": 2634, "attribute_uncatalogued": 1464,
        "live_proc_entry": 994,
    }
    assert {k: v["providers"] for k, v in negatives["classes"].items()} == expected
    for klass, row in negatives["classes"].items():
        assert row["witnesses"], klass
        assert row["reopen"], klass
        for witness in row["witnesses"]:
            assert witness["evidence"] == "source"
            assert witness["entries"] and witness["source"]["table"]
    ids = [w["spell"] for w in negatives["classes"]["caster_aura_state"]["witnesses"]]
    assert ids == [373456, 375542, 392931]
