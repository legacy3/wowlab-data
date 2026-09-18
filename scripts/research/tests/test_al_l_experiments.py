"""Track L: Retail experiment catalogue, observation surface and fidelity grading."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from aura_lifecycle import CORPORA, TC_ROOT, FailClosed, ROOT
from aura_lifecycle import experiments as X
from aura_lifecycle.records import validate, validate_corpus

DOC_COPY = ROOT.parent.parent / "bag" / "aura-lifecycle-pass" / "scratch" / "L" / "src" / "all"
CORE_TOPICS = {"identity", "coexistence", "pandemic", "next-tick", "snapshot", "death", "dispel", "stack-around-tick"}


# --- arithmetic mirrors ---------------------------------------------------------

def test_calculate_pct_is_binary32():
    """Rules out a double-precision CalculatePct: 2^24+1 is not representable in binary32."""
    assert X.calculate_pct_i32(15000, 130) == 19500
    assert X.calculate_pct_i32(16777217, 130) == 21810380
    assert int(16777217 * 1.3) == 21810382


def test_pandemic_models_discriminate_only_below_the_cap():
    """Trinity-as-written carries the full 30% whatever remains; remaining-capped models do not.
    At r >= 30% of D every model agrees (the control), so only r < 30% discriminates."""
    d = 12000
    small, large = 1800, 6000
    assert X.pandemic_trinity_as_written(d, d) == 15600
    assert X.pandemic_remaining_read(d, small) == 13800
    assert X.pandemic_simc(d, small) == 13800
    assert X.pandemic_core(d, small) == 13800
    for fn in (X.pandemic_remaining_read, X.pandemic_simc, X.pandemic_core):
        assert fn(d, large) == 15600


@given(st.integers(1, 600000), st.integers(0, 600000))
def test_pandemic_as_written_ignores_remaining(d, r):
    """The as-written branch reads the already-refreshed duration: no dependence on r."""
    assert X.pandemic_trinity_as_written(d, d) == min(2 * d, X.calculate_pct_i32(d, 130))


@given(st.integers(1, 600000), st.integers(0, 600000))
def test_simc_and_core_agree_on_multiples_of_ten(d, r):
    d -= d % 10
    if d == 0:
        return
    assert X.pandemic_simc(d, r) == X.pandemic_core(d, r)
    assert X.pandemic_simc(d, r) >= r


# --- periodic timelines -----------------------------------------------------------

def test_trinity_tick_count_with_extra_initial_period():
    """GetTotalTicks = max/period + 1 with EXTRA_INITIAL_PERIOD: tick at first update and at expiry."""
    tl = X.trinity_periodic_timeline(period=3000, max_ms=12000, extra_initial=True)
    assert tl == {"ticks": [1, 3000, 6000, 9000, 12000], "removed_at": 12000}
    tl = X.trinity_periodic_timeline(period=3000, max_ms=12000, extra_initial=False)
    assert tl["ticks"] == [3000, 6000, 9000, 12000]


def test_refresh_branches_differ_in_next_tick():
    """Rules out 'refresh always restarts the tick clock' for the pandemic branch and
    'reset delays the next tick a full period' for EXTRA_INITIAL_PERIOD spells."""
    kept = X.trinity_periodic_timeline(period=3000, max_ms=12000, extra_initial=True, refresh_at=7000,
                                       refresh_max_ms=15600)
    reset = X.trinity_periodic_timeline(period=3000, max_ms=12000, extra_initial=True, refresh_at=7000,
                                        refresh_max_ms=15600, reset_on_refresh=True)
    assert next(t for t in kept["ticks"] if t > 7000) == 9000
    assert next(t for t in reset["ticks"] if t > 7000) == 7001
    assert kept["removed_at"] == reset["removed_at"] == 22600
    # whole ticks only, capped by max/period+1 after ResetTicks
    assert len([t for t in kept["ticks"] if t > 7000]) <= 15600 // 3000 + 1


def test_simc_partial_last_tick_vs_trinity_whole_ticks():
    simc = X.simc_periodic_timeline(period=3000, duration_ms=12000, tick_zero=True, refresh_at=7000,
                                    refresh_duration_ms=13800)
    assert simc["ticks"][-1] == [20800, str(Fraction(2800, 3000))]
    trin = X.trinity_periodic_timeline(period=3000, max_ms=12000, extra_initial=True, refresh_at=7000,
                                       refresh_max_ms=13800)
    assert all(t % 3000 in (0, 1) for t in trin["ticks"])


def test_timeline_fails_closed():
    with pytest.raises(FailClosed):
        X.trinity_periodic_timeline(period=0, max_ms=1000, extra_initial=False)
    with pytest.raises(FailClosed):
        X.trinity_periodic_timeline(period=1000, max_ms=1000, extra_initial=False, refresh_at=5)


# --- surface and fidelity ---------------------------------------------------------

def test_aura_channels_are_secret_in_combat_unless_never_secret():
    """Rules out 'addon aura APIs work while DoTing a dummy' (SecretWhenUnitAuraRestricted)."""
    assert X.channel_available("OBS-ADDON-AURA-QUERY", "solo-no-combat")
    assert not X.channel_available("OBS-ADDON-AURA-QUERY", "open-world-combat")
    assert X.channel_available("OBS-ADDON-AURA-QUERY", "open-world-combat", secrecy="NeverSecret")
    assert X.channel_available("OBS-COMBATLOG-FILE", "instance-encounter")
    assert not any(X.channel_available("OBS-ADDON-CLEU", c) for c in X.CONTEXTS)


def test_identity_is_unobservable_in_combat():
    """The combat log carries no instance id: identity in combat is insufficient without NeverSecret."""
    obs = [{"quantity": "identity"}]
    assert X.classify(obs, "open-world-combat")["fidelity"] == "insufficient"
    assert X.classify(obs, "open-world-combat", secrecy="NeverSecret")["fidelity"] == "exact"
    assert X.classify(obs, "solo-no-combat")["fidelity"] == "exact"


def test_same_time_order_never_observable():
    """Receipt order of equal-timestamp lines is not scheduler order (brief §3)."""
    for ctx in X.CONTEXTS:
        assert X.classify([{"quantity": "same-time-order"}], ctx)["fidelity"] == "insufficient"


def test_timing_margin_threshold():
    bound = 2 * (X.TIMING_JITTER_MS + 1)
    assert X.classify([{"quantity": "event-time", "margin_ms": bound}], "solo-no-combat")["fidelity"] == "insufficient"
    assert X.classify([{"quantity": "event-time", "margin_ms": bound + 1}],
                      "solo-no-combat")["fidelity"] == "approximate"
    with pytest.raises(FailClosed):
        X.classify([{"quantity": "event-time"}], "solo-no-combat")
    with pytest.raises(FailClosed):
        X.classify([{"quantity": "vibes"}], "solo-no-combat")
    with pytest.raises(FailClosed):
        X.classify([{"quantity": "stacks"}], "moon")


def test_surface_references_resolve():
    for name, ch in X.CHANNELS.items():
        assert ch["sources"], name
        assert set(ch["sources"]) <= set(X.SOURCES), name
        assert set(ch["provides"]) <= set(X.QUANTITIES), name
    for sid, src in X.SOURCES.items():
        assert src["kind"] in X.SOURCE_KINDS and src["status"] in X.SOURCE_STATUS, sid


@pytest.mark.skipif(not DOC_COPY.is_dir(), reason="no local copy of Blizzard_APIDocumentationGenerated")
def test_doc_quotes_verbatim():
    """Every generated-doc claim quotes the cited line (or the next few lines) verbatim."""
    for sid, src in X.SOURCES.items():
        if src["kind"] != "blizzard-generated-doc":
            continue
        path, line = src["coords"].rsplit(":", 1)
        lines = (DOC_COPY / Path(path).name).read_text(encoding="utf-8").splitlines()
        window = "\n".join(lines[int(line) - 1:int(line) + 6])
        assert src["quote"] in window, sid


@pytest.mark.skipif(not (TC_ROOT / "src").is_dir(), reason="TrinityCore absent")
def test_trinity_coordinates():
    game = TC_ROOT / "src" / "server" / "game"
    checks = {
        ("Spells/Spell.cpp", 3284): "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION",
        ("Spells/Spell.cpp", 3240): "resetPeriodicTimer = (m_spellInfo->StackAmount < 2)",
        ("Spells/Auras/SpellAuras.cpp", 261): "auraData.Applications = aura->IsUsingStacks()",
        ("Spells/Auras/SpellAuras.cpp", 976): "void Aura::RefreshTimers(bool resetPeriodicTimer)",
        ("Spells/Auras/SpellAuras.h", 394): "ObjectGuid const m_castId;",
        ("Spells/Auras/SpellAuraDefines.h", 22): "#define MAX_AURAS 300",
        ("Spells/Auras/SpellAuraEffects.cpp", 949): "void AuraEffect::ResetPeriodic",
        ("Spells/Auras/SpellAuraEffects.cpp", 1250): "void AuraEffect::Update(uint32 diff, Unit* caster)",
        ("Spells/Auras/SpellAuraEffects.cpp", 5916): "SpellHealingBonusDone",
        ("Entities/Unit/Unit.cpp", 4472): "void Unit::RemoveAllAurasOnDeath()",
    }
    for (path, line), text in checks.items():
        assert text in (game / path).read_text(encoding="utf-8", errors="replace").splitlines()[line - 1], (path, line)
    assert "return T(base * static_cast<float>(pct) / 100.0f);" in \
        (TC_ROOT / "src/common/Utilities/Util.h").read_text().splitlines()[73]


# --- catalogue shape (no snapshot) ------------------------------------------------

def test_core_specs_cover_assignment_and_have_competing_models():
    specs = X._core_specs()
    assert CORE_TOPICS <= {s["topic"] for s in specs}
    for s in specs:
        assert s["context"] in X.CONTEXTS
        assert set(s["api"]) <= set(X.CHANNELS)
        if s["topic"] not in ("pandemic", "next-tick"):  # filled from witness data
            assert len(s["models"]) >= 2, s["id"]
            assert all(m["prediction"] for m in s["models"]), s["id"]


def _cand(i, question, **extra):
    return {"id": i, "question": question, "models": [{"name": "a"}, {"name": "b"}], "setup": {}, "observable": "x",
            "discriminates": "y", "fidelity": "exact", "related": [], **extra}


def test_merge_candidates_dedupes_and_regrades():
    base = [{"id": "AL-X-L-01", "question": "Does a refresh keep the instance?", "related": [], "origin": "L",
             "fidelity": "exact"}]
    dup = _cand("AL-X-B-01", "Does a  refresh keep the INSTANCE?")
    new = _cand("AL-X-E-02", "Is a dead caster's DoT removed?", context="open-world-combat",
                observables=[{"quantity": "identity"}])
    out = X.merge_candidates(base, [dup, new])
    assert [e["id"] for e in out] == ["AL-X-E-02", "AL-X-L-01"]
    assert out[1]["merged_from"] == ["AL-X-B-01"]
    assert out[0]["origin"] == "E" and out[0]["fidelity"] == "insufficient" and out[0]["fidelity_track"] == "exact"
    with pytest.raises(ValueError):
        X.merge_candidates(base, [{"id": "AL-X-E-03"}])


def test_cross_cutting_records_validate():
    validate("unknowns", X.unknowns())
    validate("rules", X.rules())
    validate("falsification", X.falsification())


# --- real data ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def al_corpus(al_ctx):
    return X.corpus(al_ctx)


def test_catalogue_on_snapshot(al_corpus):
    validate_corpus(al_corpus)
    entries = al_corpus["retail_experiments"]
    assert CORE_TOPICS <= {e["topic"] for e in entries}
    for e in entries:
        assert e["fidelity"] == X.classify(e["observables"], e["context"])["fidelity"], e["id"]
        assert len(e["models"]) >= 2, e["id"]
        w = e["witness"]
        if w["spell"] is not None:
            assert w["status"] == "ok", (e["id"], w)
            assert w["player"], (e["id"], "witness must be in the current-player population")
        assert e["applicability"]["player"] >= 1, e["id"]


def test_pandemic_predictions_follow_the_witness(al_corpus):
    e = next(e for e in al_corpus["retail_experiments"] if e["topic"] == "pandemic")
    d = e["setup"]["inputs"]["D_ms"]
    assert d == e["witness"]["facts"]["duration_ms"]
    preds = {m["name"]: m["prediction"] for m in e["models"]}
    assert preds["Trinity as written"]["r_small"] > preds["simc DOT_REFRESH_PANDEMIC"]["r_small"]
    assert len({p["r_large"] for p in preds.values()}) == 1
    assert e["observables"][0]["margin_ms"] > 2 * X.TIMING_JITTER_MS


def test_corpus_on_disk_is_current(al_corpus):
    path = CORPORA / "retail-experiments.json"
    if not path.exists():
        pytest.skip("corpus not written yet")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    if on_disk["counts"]["by_origin"] != {"L": on_disk["counts"]["experiments"]}:
        pytest.skip("corpus includes merged candidates; regenerate with --scan to compare")
    assert on_disk == json.loads(json.dumps(al_corpus, sort_keys=True))
