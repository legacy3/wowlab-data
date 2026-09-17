"""Track C: cap policy helpers and the real-data cap/area/geometry census."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from targeting import CORPORA, caps
from targeting.fixture import World

VERDICTS = {"understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved", "n/a"}


@settings(max_examples=300, deadline=None)
@given(st.lists(st.integers(0, 5), max_size=20))
def test_list_sort_strict_comparator_is_stable(xs):
    """With a strict comparator libstdc++ list::sort is a stable sort (the ascending LINE cap relies on it)."""
    idx = list(range(len(xs)))
    assert caps.list_sort(idx, lambda a, b: xs[a] < xs[b]) == sorted(idx, key=lambda i: xs[i])


def _w(draws):
    return World.from_dict({"schema": "targeting-fixture/1", "caster": "c",
                            "actors": [{"id": "c", "kind": "player"}], "rng": {"draws": draws}})


@settings(max_examples=200, deadline=None)
@given(st.integers(1, 8), st.integers(0, 8), st.data())
def test_caps_random_resize_is_track_d(size, n, data):
    """caps.random_resize delegates to rng.random_resize (single implementation)."""
    from targeting.rng import random_resize
    draws = [data.draw(st.integers(1, size - i)) for i in range(size)]
    items = [f"u{i}" for i in range(size)]
    w1, w2 = _w(draws), _w(draws)
    assert caps.random_resize(w1, items, n, None) == random_resize(w2, items, n, None)
    assert w1.draws_consumed == (0 if size <= n else size)


def test_random_resize_rejects_out_of_range_draw():
    from targeting import FailClosed
    with pytest.raises(FailClosed):
        caps.random_resize(_w([5, 1, 1]), ["a", "b", "c"], 1, None)


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------
def test_divine_storm_legacy_cap(tg_ctx):
    """TG-C-D09: current-player Divine Storm (53385) gets MaxAffectedTargets=4 from a WotLK correction."""
    from dummy_semantics.corrections import Corrections
    assert 53385 in tg_ctx.scope.reach
    assert tg_ctx.data.restrictions(53385) is None  # no DB2 cap
    corr = Corrections(tg_ctx.bundle)
    writes = [w for f in corr.by_spell[53385] for w in f["writes"] if w["member"] == "MaxAffectedTargets"]
    assert [w["value"] for w in writes] == ["4"]
    sqrt = [b for b in caps.sqrt_target_limits() if 53385 in b["spells"]]
    assert sqrt and sqrt[0]["max_targets"] == 5
    effs = tg_ctx.data.effects(53385)
    assert (effs[0].target_a, effs[0].target_b) == (effs[1].target_a, effs[1].target_b) == (18, 16)


def test_spellmod_max_targets_edge(tg_ctx):
    """SpellModOp::MaxTargets sources in scope are found through family flags / labels."""
    mods = caps.spellmod_sources(tg_ctx)
    src = [m for m in mods if m["spell"] == 1270255 and m["op"] == caps.SPELLMOD_MAX_TARGETS]
    assert src
    assert caps.spellmod_affects(tg_ctx, src[0], 5484) >= 1


def test_sqrt_limits_are_not_selection_caps(tg_ctx):
    """LoadSpellInfoTargetCaps blocks exist for spells with no selection cap at all."""
    rows = caps.sqrt_target_limits()
    assert rows and all(r["line"] > 5400 for r in rows)
    uncapped = [s for r in rows for s in r["spells"] if s in tg_ctx.scope.reach
                and not (tg_ctx.data.restrictions(s) or {}).get("MaxTargets")]
    assert 2120 in uncapped  # Flamestrike: sqrt limit 8, no MaxTargets


def test_effect_classes_shape(tg_ctx):
    from targeting import cmd_c
    classes = cmd_c.effect_classes()
    assert classes
    keys = list(classes)
    assert keys == sorted(keys, key=lambda k: tuple(map(int, k.split(":"))))
    for k, v in classes.items():
        assert v["class"] in VERDICTS
        assert v["tags"] == sorted(v["tags"])
        assert isinstance(v["build_skew"], bool)
        assert int(k.split(":")[0]) in tg_ctx.scope.reach
    ds = classes["53385:0"]
    assert "random-cap" in ds["tags"] or "correction" in ds["tags"]


def test_census_counts_match_corpora(tg_ctx):
    """The committed corpora are regenerable (counts and effect_classes)."""
    from targeting import cmd_c
    rows = cmd_c.scope_effects()
    area_n = sum(1 for r in rows if "AREA" in cmd_c._cats(r))
    cone_n = sum(1 for r in rows if "CONE" in cmd_c._cats(r))
    for name, key, want in (("area.json", "effects_with_area", area_n), ("geometry.json", "effects_with_cone", cone_n)):
        path = CORPORA / name
        if not path.exists():
            pytest.skip(f"{name} not generated yet")
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["census"][key] == want
        classes = cmd_c.effect_classes()
        assert all(classes[k] == v for k, v in doc["effect_classes"].items())
