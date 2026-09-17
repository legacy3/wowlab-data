"""Track C / agent F: Python oracle vs extracted TrinityCore probe.

Skips cleanly when the probe binary is absent; build with
``make -C scripts/research/tools/tc_prep_probe``.  Live comparisons re-run
the probe; corpus assertions read differential.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from character_prep import CORPORA, SourceError  # noqa: E402
from character_prep import basestats_tdb as bt  # noqa: E402
from character_prep import differential as df  # noqa: E402
from charstats.character import CharacterResolver  # noqa: E402
from charstats.identity import STAT_STAMINA, STAT_STRENGTH  # noqa: E402

PROBE_DIR = RESEARCH_ROOT / "tools" / "tc_prep_probe"
DIFFERENTIAL = CORPORA / "differential.json"


@pytest.fixture(scope="module")
def probe():
    # build (or refresh) through the Makefile; skip only when that is impossible here
    try:
        subprocess.run(["make", "-C", str(PROBE_DIR)], check=False, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not df.PROBE.exists():
        pytest.skip(f"probe not built; run: make -C {PROBE_DIR}")
    p = df.Probe()
    yield p
    p.close()


@pytest.fixture(scope="module")
def table():
    if not bt.CORPUS_PATH.exists():
        pytest.skip("player-base-stats.json missing")
    pairs = bt.playercreateinfo_pairs()
    if pairs is None:
        pytest.skip("base-stat-gap.json pairs missing")
    return bt.load().table(bt.FILL_RULE_TRINITY, pairs=pairs)


@pytest.fixture(scope="module")
def resolver(tables, table):
    return CharacterResolver(tables, table)


@pytest.fixture(scope="module")
def corpus():
    if not DIFFERENTIAL.exists():
        pytest.skip("differential.json not generated")
    return json.loads(DIFFERENTIAL.read_text(encoding="utf-8"))


# -- probe protocol facts ----------------------------------------------------------

def test_probe_extracted_inc_is_generated_from_pinned_checkout():
    inc = PROBE_DIR / "tc_extracted.inc"
    if not inc.exists():
        pytest.skip("tc_extracted.inc not generated")
    text = inc.read_text(encoding="utf-8")
    for needle in ("float Unit::GetTotalStatValue(Stats stat) const", "void Player::UpdateMaxHealth()",
                   "float Player::GetRatingBonusValue(CombatRating cr) const",
                   "void ObjectMgr::BuildPlayerLevelInfo(", "ObjectMgr_FillPlayerLevelGaps("):
        assert needle in text


def test_totalstat_truncates_not_rounds(probe):
    raw, whole = probe.ask("totalstat 17647 10000 100 1 0 1.05")
    assert float(raw) == pytest.approx(29029.35, abs=0.01)
    assert int(whole) == 29029                       # int32() truncation


def test_maxpower_uses_lroundf_half_away(probe):
    assert int(probe.ask("maxpower 5 0 1 0 0.5")[0]) == 3      # 2.5 -> 3
    assert round(2.5) == 2                                     # Python half-to-even differs
    assert int(probe.ask("maxpower 3 0 1 0 0.5")[0]) == 2      # 1.5 -> 2


def test_maxhealth_fallback_and_floor(probe):
    assert int(probe.ask("maxhealth 0 0 1 0 1 5 1 -1")[0]) == 50     # no HpPerSta row -> 10.0f
    assert int(probe.ask("maxhealth 0 0 1 0 1 0 80 20")[0]) == 1     # SetMaxHealth floors 0 to 1
    assert int(probe.ask("maxhealth 0 0 1 0 1 101452 80 20")[0]) == 2029040


def test_applyrating_accumulates_in_int16(probe):
    assert int(probe.ask("applyrating 30000 5000")[0]) == -30536
    assert int(probe.ask("applyrating 30000 2000")[0]) == 32000


def test_fill_rule_probe_copies_and_aborts(probe):
    reply = probe.ask("fillgaps 90 90 2 1 1 2 3 4 5 80 17647 12176 86452 12000 0")
    assert int(reply[0]) == 88 and [int(x) for x in reply[1:]] == [17647, 12176, 86452, 12000, 0]
    assert probe.ask("fillgaps 90 90 1 80 17647 12176 86452 12000 0") == ["ABORT"]
    reply = probe.ask("fillgaps 90 5 2 1 3 4 6 6 0 10 30 40 60 60 0")
    assert [int(x) for x in reply[1:]] == [3, 4, 6, 6, 0]


def test_build_player_level_info_differs_from_copy(probe):
    reply = [int(x) for x in probe.ask("buildlevelinfo 1 80 90 17647 12176 86452 12000 0")]
    assert reply == [17669, 12187, 86474, 12005, 0]          # Classic switch, reachable only if MaxPlayerLevel < level
    assert reply != [17647, 12176, 86452, 12000, 0]


def test_mastery_amount_double_accumulates_float_product(probe):
    raw, whole = probe.ask("masteryamount 0 172.5 1.39999997616")
    assert float(raw) == pytest.approx(241.5, abs=1e-4)
    assert int(whole) == 241


def test_calcpct_is_float(probe):
    assert float(probe.ask("calcpct 10000 100")[0]) == 10000.0
    assert float(probe.ask("calcpct 12345.678 33.3")[0]) == pytest.approx(4111.11, abs=0.01)


# -- live differential --------------------------------------------------------------

@pytest.mark.parametrize("witness", df.default_witnesses(), ids=lambda w: w.name)
def test_witness_matches_except_generic_mastery(probe, tables, witness):
    corpus = bt.load()
    pairs = bt.playercreateinfo_pairs()
    if pairs is None:
        pytest.skip("pairs missing")
    tbl = corpus.table(witness.fill_rule, pairs=pairs)
    res = df.compare_witness(CharacterResolver(tables, tbl), tbl, probe, witness)
    expected = ["mastery_value.charstats"] if witness.spec_id is not None else []
    if witness.name.endswith("-crossing"):
        expected = ["stat.Strength"] + expected             # binary32-vs-binary64, see the sweep
    assert res["mismatch_fields"] == expected
    for r in res["records"]:
        if "int32_agree" in r and not r.get("classification", "").startswith(("model-gap", "binary32")):
            assert r["int32_agree"], r["field"]


def test_level_90_witness_uses_filled_cell(probe, tables, table):
    w = next(x for x in df.default_witnesses() if x.name == "warrior-arms-human-90-fill")
    res = df.compare_witness(CharacterResolver(tables, table), table, probe, w)
    assert res["base_stats"]["evidence_class"] == "trinity-consumer(fill-rule)"
    assert res["base_stats"]["stats"] == {"Strength": 17647, "Agility": 12176, "Stamina": 86452, "Intellect": 12000, "Spirit": 0}
    fill = {r["field"]: r for r in res["records"] if r["field"].startswith("fill_rule")}
    assert fill["fill_rule.Strength"]["match"] and fill["fill_rule.tc_log_error_count"]["probe"] == 10
    assert res["python"]["hp_per_sta"] == 20.0 and res["python"]["max_health"] == (86452 + 15000) * 20


def test_generic_mastery_gap_is_exactly_eight_points(probe, tables, table):
    w = next(x for x in df.default_witnesses() if x.name == "priest-shadow-human-80")
    res = df.compare_witness(CharacterResolver(tables, table), table, probe, w)
    rec = {r["field"]: r for r in res["records"]}
    assert rec["mastery_value.charstats"]["match"] is False
    assert rec["mastery_value.with_114585"]["match"] is True
    assert rec["mastery_value.with_114585"]["python"] - rec["mastery_value.charstats"]["python"] == pytest.approx(8.0)


# -- fixture mode -------------------------------------------------------------------

def test_fixture_mode_fails_closed_on_unknown_shape(tmp_path):
    path = tmp_path / "x.compiled.json"
    path.write_text(json.dumps({"character": {"race": 1}}), encoding="utf-8")
    with pytest.raises(SourceError, match="unknown compiled fixture shape"):
        df.witness_from_compiled(path)
    path.write_text(json.dumps({"identity": {"race_id": 1, "class_id": 1, "level": 80},
                                "contributions": {"ratings": {"Bogus": 1}}}), encoding="utf-8")
    with pytest.raises(SourceError, match="unknown rating"):
        df.witness_from_compiled(path)


def test_fixture_mode_accepts_declared_shape(tmp_path):
    path = tmp_path / "w.compiled.json"
    path.write_text(json.dumps({"identity": {"race_id": 2, "class_id": 1, "level": 80, "spec_id": 71},
                                "contributions": {"stats": {"Strength": 100, "2": 50}, "ratings": {"Mastery": 10},
                                                  "armor": 7}}), encoding="utf-8")
    w = df.witness_from_compiled(path)
    assert (w.race_id, w.class_id, w.level, w.spec_id) == (2, 1, 80, 71)
    assert w.stats == {STAT_STRENGTH: 100, STAT_STAMINA: 50} and w.ratings == {"Mastery": 10} and w.armor == 7


# -- corpus ---------------------------------------------------------------------------

def test_corpus_summary(corpus):
    s = corpus["summary"]
    assert s["witnesses"] == len(df.default_witnesses()) + s["fixture_witnesses"]
    assert {f for f in s["mismatch_fields"] if not f.startswith("compiled.")} <= {"mastery_value.charstats",
                                                                                 "stat.Strength"}
    assert not [f for f in s["mismatch_fields"] if f.startswith("compiled_trinity.")]
    for w in corpus["witnesses"]:
        for r in w["records"]:
            if "match" in r and not r["match"]:
                assert r.get("classification", "").startswith(KNOWN_CLASSES), \
                    (w["witness"]["name"], r["field"])
    assert corpus["hp_per_sta_witness"]["level_80"] == 20.0 and corpus["hp_per_sta_witness"]["level_90"] == 20.0


def test_corpus_float_records_agree_after_int32(corpus):
    for w in corpus["witnesses"]:
        for r in w["records"]:
            if "int32_agree" in r and not r.get("classification", "").startswith(("model-gap", "binary32")):
                assert r["int32_agree"], (w["witness"]["name"], r["field"])
            if "rel_diff" in r and r["field"] not in ("mastery_value.charstats", "compiled.mastery_value"):
                assert r["rel_diff"] < 1e-6, (w["witness"]["name"], r["field"])


# -- binary32 / binary64 -----------------------------------------------------------

def test_armor_spec_multiplier_is_float_of_double():
    assert df.f32(1.05) == pytest.approx(1.0499999523162842, abs=0) and df.f32(1.05) != 1.05


def test_armor_spec_crossing_is_reachable(probe):
    # 17647 (Human Warrior L80 create Strength) + 9993 item Strength = 27640
    raw, whole = probe.ask(f"totalstat 17647 9993 100 1 0 {df.f32(1.05)!r}")
    assert int(whole) == 29021 and int(27640 * 1.05) == 29022
    assert float(raw) == pytest.approx(29021.998046875, abs=0)


def test_armor_spec_crossing_witness_classified(probe, tables, table):
    w = next(x for x in df.default_witnesses() if x.name.endswith("-crossing"))
    res = df.compare_witness(CharacterResolver(tables, table), table, probe, w)
    rec = {r["field"]: r for r in res["records"]}
    assert (rec["stat.Strength"]["python"], rec["stat.Strength"]["probe"]) == (29022, 29021)
    assert rec["stat.Strength"]["classification"].startswith("binary32-vs-binary64")
    assert rec["stat.Strength.raw"]["match"]                       # the raw values agree to 1e-6


def test_corpus_armor_spec_sweep(corpus):
    sweep = corpus["armor_spec_truncation_sweep"]
    assert sweep["divergence_count"] == 4045 and sweep["range"] == [1, 120000]
    assert sweep["divergences_by_total_mod_20"] == {"0": 4045}      # only multiples of 20
    assert sweep["python_minus_trinity_values"] == [1]              # Trinity always one lower


def test_corpus_rating_sweeps_never_cross_int32(corpus):
    sweeps = {(s["rating"], s["level"]): s for s in corpus["rating_int32_sweeps"]}
    assert set(sweeps) == {("Mastery", 80), ("CritMelee", 90), ("VersatilityDamageDone", 80)}
    for s in sweeps.values():
        assert s["int32_divergence_count"] == 0 and s["max_rel_diff"] < 1e-6


# -- generic mastery 114585 (DB2 half of the chain) ---------------------------------

def test_generic_mastery_db2_facts(tables):
    e = df.generic_mastery_evidence(tables)
    assert e["is_passive"] and e["spell_misc_attributes_0"] == 464
    assert [(x["effect"], x["effect_aura"], x["base_points"]) for x in e["effects"]] == [(6, 318, 8)]
    assert e["skill_line_ability"]["SkillLine"] == 183 and e["skill_line_ability"]["AcquireMethod"] == 2
    assert e["skill_line_ability"]["ClassMask"] == 0
    assert [(x["ClassMask"], x["Availability"], x["MinLevel"]) for x in e["skill_race_class_info"]] == [(16383, 1, 0)]
    assert e["stance_rows"] == e["aura_restriction_rows"] == e["equipped_item_rows"] == 0
    assert all(x["SpellLevel"] == 0 and x["BaseLevel"] == 0 for x in e["spell_levels"])


def test_corpus_records_generic_mastery(corpus):
    assert corpus["generic_mastery_114585"]["spell_id"] == df.GENERIC_MASTERY_SPELL == 114585


# -- agent E compiled shape -----------------------------------------------------------

def _e_compiled(level=80, stats_unresolved=False, ok=True, boundary=True, applied=None, value=None):
    handed = {"stats": {"Strength": 100}, "ratings": {"Mastery": 10}, "armor": 7, "attack_power": 0,
              "spell_power": 0, "health": 0, "source": "gearing.loadout.resolve_loadout aggregate"}
    prov = ([{"step": "boundary", "handed_over": handed}] if boundary else [])
    if stats_unresolved:
        prov.append({"step": "base-primary-stats", "result": "level-row-missing"})
    return {"provenance": {}, "fixture": {"identity": {"race_id": 2, "class_id": 1, "spec_id": 71, "level": level}},
            "validation": {"ok": ok, "errors": [] if ok else ["x"]},
            "derived": {"stats": {"value": None if stats_unresolved else value,
                                  "evidence_class": "unresolved" if stats_unresolved else "trinity-consumer",
                                  "provenance": prov},
                        "armor_specialization": {"value": {"applied_spell_id": applied}}}}


def test_e_shape_accepted(tmp_path):
    path = tmp_path / "arms.compiled.json"
    path.write_text(json.dumps(_e_compiled(applied=86526, value={"stats": {}})), encoding="utf-8")
    w = df.witness_from_compiled(path)
    assert (w.name, w.race_id, w.class_id, w.spec_id, w.level) == ("arms", 2, 1, 71, 80)
    assert w.stats == {STAT_STRENGTH: 100} and w.ratings == {"Mastery": 10} and w.armor == 7
    assert w.apply_armor_specialization and w.fill_rule == bt.FILL_RULE_NONE


def test_e_shape_level_gap_opts_into_fill_rule(tmp_path):
    path = tmp_path / "arms90.compiled.json"
    path.write_text(json.dumps(_e_compiled(level=90, stats_unresolved=True)), encoding="utf-8")
    w = df.witness_from_compiled(path)
    assert w.fill_rule == bt.FILL_RULE_TRINITY and "fill rule" in w.note and w.compiled_python is None


@pytest.mark.parametrize("kwargs,match", [
    ({"ok": False}, "failed validation"),
    ({"boundary": False}, "boundary step"),
])
def test_e_shape_fails_closed(tmp_path, kwargs, match):
    path = tmp_path / "bad.compiled.json"
    path.write_text(json.dumps(_e_compiled(**kwargs)), encoding="utf-8")
    with pytest.raises(SourceError, match=match):
        df.witness_from_compiled(path)


def test_e_shape_rejects_unknown_contribution_keys(tmp_path):
    raw = _e_compiled()
    raw["derived"]["stats"]["provenance"][0]["handed_over"]["weapon_dps"] = 1
    path = tmp_path / "bad.compiled.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(SourceError, match="unknown contributions keys"):
        df.witness_from_compiled(path)


def test_compiled_cross_check_detects_disagreement(probe, tables, table):
    w = next(x for x in df.default_witnesses() if x.name == "warrior-arms-human-80")
    resolver = CharacterResolver(tables, table)
    d = resolver.resolve(w.race_id, w.class_id, w.level, w.spec_id, w.contributions()).to_dict()
    fresh = df.compare_witness(resolver, table, probe, w)["python"]
    w.compiled_python = dict(fresh, bonus_armor=d["bonus_armor"], ranged_attack_power=d["ranged_attack_power"],
                             stats=dict(fresh["stats"]), stat_stages=d["stats"])
    res = df.compare_witness(resolver, table, probe, w)
    assert not [f for f in res["mismatch_fields"] if f.startswith("compiled")]
    assert any(r["field"] == "compiled_trinity.stat.Strength" and r["match"] for r in res["records"])
    w.compiled_python["stats"]["Strength"] += 1
    res = df.compare_witness(resolver, table, probe, w)
    assert "compiled.stat.Strength" in res["mismatch_fields"]


# -- agent E's real compiled fixtures (skip until character-prep-corpora/fixtures exists) ----

E_COMPILED = sorted((CORPORA / "fixtures").glob("*.compiled.json")) if (CORPORA / "fixtures").is_dir() else []
KNOWN_CLASSES = ("model-gap", "binary32-vs-binary64", "compiler-vs-charstats")


@pytest.mark.skipif(not E_COMPILED, reason="agent E fixtures not generated (python3 character_prep.py all)")
def test_e_fixtures_trinity_arithmetic_agrees(probe, tables, table):
    for path in E_COMPILED:
        try:
            w = df.witness_from_compiled(path)
        except SourceError as error:                        # fail closed: refusal must say why
            assert str(error)
            continue
        tbl = bt.load().table(w.fill_rule, pairs=bt.playercreateinfo_pairs())
        res = df.compare_witness(CharacterResolver(tables, tbl), tbl, probe, w)
        for r in res["records"]:
            if "match" in r and not r["match"]:
                assert r.get("classification", "").startswith(KNOWN_CLASSES), (path.name, r["field"])
