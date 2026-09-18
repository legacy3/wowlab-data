"""Track E: real-data tests (snapshot 12.1.0.69497 + pinned Trinity scripts) via ``al_ctx``."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, FailClosed, providers
from aura_lifecycle import dispel as D
from aura_lifecycle import lifetime as L
from aura_lifecycle import removal as R


@pytest.fixture(scope="module")
def pops(al_ctx):
    return providers.populations(al_ctx)


@pytest.fixture(scope="module")
def script_rows(al_ctx):
    try:
        return [r for r in R.script_classes_reading_mode(al_ctx.bundle) if "class" in r]
    except FailClosed:
        pytest.skip("TrinityCore scripts absent")


def test_lifebloom_bloom_is_expire_or_dispel_only(al_ctx, script_rows) -> None:
    """Player witness for 'death != expiry': Lifebloom's remove handler tests EXPIRE and ENEMY_SPELL only."""
    e = R.explain(al_ctx, 33763, script_rows)
    readers = e["script_mode_readers"]
    assert readers and readers[0]["class"] == "spell_dru_lifebloom"
    assert readers[0]["comparisons"] == ["== ENEMY_SPELL", "== EXPIRE"]
    assert e["death_class"] == "removed-death"


def test_death_branch_witnesses(al_ctx, script_rows) -> None:
    by = {r["class"]: r for r in script_rows}
    assert by["spell_warl_haunt"]["comparisons"] == ["== DEATH"] and 48181 in by["spell_warl_haunt"]["spells"]
    assert by["spell_rog_rupture"]["comparisons"] == ["!= DEATH"] and 1943 in by["spell_rog_rupture"]["spells"]
    # spell_priest.cpp is not in the Dummy-pass index (parse error): lexical fallback still binds Guardian Spirit
    assert 47788 in by["spell_pri_guardian_spirit"]["spells"] and by["spell_pri_guardian_spirit"]["index"] is False


def test_lexical_classes_agree_with_index(al_ctx) -> None:
    """The lexical fallback finds the same AuraScript/SpellScript line ranges as the tree-sitter index on an indexed file."""
    rel = "src/server/scripts/Spells/spell_druid.cpp"
    lex = {(n, a) for n, _, a, _ in R._lexical_classes(rel)}
    idx = {(c.name, c.line) for c in al_ctx.bundle.index.classes.values()
           if c.file == rel and ("AuraScript" in c.bases or "SpellScript" in c.bases)}
    assert idx and idx == lex


def test_earth_shield_survives_but_disabled_while_dead(al_ctx) -> None:
    f = R.spell_removal_facts(al_ctx, 974)
    assert f["death_class"] == "survives-death-persistent+disabled-while-dead"
    assert D.explain(al_ctx, 974)["aura"]["dispel_all_stacks"] is True


def test_rupture_is_dispel_type_11(al_ctx) -> None:
    e = D.explain(al_ctx, 1943)
    assert e["aura"]["dispel_type"] == 11 and 3 in e["aura"]["periodic_aura_types"]
    cf = D.explain(al_ctx, 374251)
    assert any(x["kind"] == "dispel" and x["misc"] == 11 for x in cf["dispeller"])


def test_census_death_classes_partition(al_ctx, pops, script_rows) -> None:
    c = R.census(al_ctx, {"player": pops["player"], "controlled": pops["controlled"]}, script_rows)
    for pop, row in c.items():
        classes = sum(v for k, v in row["counts"].items() if k.startswith("death_class:"))
        assert classes + row["no_spellmisc_row"] == row["spells"], pop
    assert c["player"]["counts"]["script_reads_remove_mode"] == 15


def test_lifetime_explain_channel_and_dynobj(al_ctx) -> None:
    mf = L.explain(al_ctx, 15407)
    assert mf["channelled"] and "CANCEL" in mf["caster_death"]
    rof = L.explain(al_ctx, 5740)
    assert any(e["owner_shape"] == "dynamic-object" for e in rof["effects"])


def test_not_a_provider_fails_closed(al_ctx) -> None:
    with pytest.raises(FailClosed):
        R.explain(al_ctx, 527, [])          # Purify: dispeller, no aura effect


@pytest.mark.parametrize("name", ["removal", "dispel", "lifetime"])
def test_corpus_matches_builder(al_ctx, pops, name) -> None:
    """Committed corpora are byte-stable outputs of the registered commands."""
    from aura_lifecycle import cmd_e
    path = CORPORA / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{path.name} not generated")
    built = json.loads(json.dumps(getattr(cmd_e, f"build_{name}")(al_ctx, pops), sort_keys=True))
    assert built == json.loads(path.read_text(encoding="utf-8"))
