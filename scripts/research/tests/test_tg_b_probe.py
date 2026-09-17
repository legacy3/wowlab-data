"""Track B: differential of targeting.relations against Trinity's verbatim relation predicates
(tools/tc_target_relation_probe) and real-data census invariants."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from targeting import TC_ROOT
from targeting.cmd_b import PROBE_DIR, differential


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Entities/Object/Object.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None:
        pytest.skip("no g++")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE_DIR / "probe"


@pytest.mark.parametrize("seed", [11, 12])
def test_relations_match_verbatim_trinity(probe, tg_ctx, seed):
    """Rules out any divergence between the Python mirror and the pinned Object.cpp text over random
    worlds (ownership chains, charmers, traps, forced reactions, reputation, duels, PvP/FFA/sanctuary,
    groups, visibility, spell attribute bypasses)."""
    res = differential(tg_ctx.bundle.source, 600, seed)
    st = res["stats"]
    assert st["fail_closed"] == 0
    assert st["mismatches"] == 0, res["mismatches"][:2]
    assert st["fields_compared"] > 10000
    # the random worlds must actually reach the rare branches
    for needle in ("2094:duel", "2105:ffa", "2166:CvP-reputation",
                   "2423:trap-vs-creature", "2453:rep-not-at-war", "2469:sanctuary", "2596:PvC"):
        assert any(needle in k for k in st["paths"]), needle


def test_explicit_census_invariants(tg_ctx):
    from targeting import explicit
    c = explicit.census(tg_ctx)
    rows = {r["id"]: r for r in c["matrix"]}
    assert c["scope"]["reach_spells"] == len(tg_ctx.scope.reach)
    n = c["scope"]["explicit_unit_spells"]
    assert n == rows["M01"]["witness_count"] > 0
    # M09 (UNIT_ENEMY required) + no-enemy spells partition the explicit-unit set only when counted with the optional mask
    assert rows["M09"]["witness_count"] <= c["explicit_mask_flag_counts"]["UNIT_ENEMY"]
    # dead predicate: allowing + not allowing == all
    assert rows["M05"]["witness_count"] + rows["M07a"]["witness_count"] == n
    assert set(c["effect_classes"]) and all(":" in k for k in c["effect_classes"])
    assert {v["class"] for v in c["effect_classes"].values()} <= {
        "understood", "understood-with-defect", "fixture-dependent", "blocked", "unresolved", "n/a"}
    # no current-player explicit-unit spell reads a split hostile/friendly SpellRange row (TG-B-R1 scope note)
    assert rows["M17"]["witness_count"] == 0


def test_explicit_mask_known_spells(tg_ctx):
    """Fireball (133) is an enemy-only explicit spell; Flash Heal (2061) is ally-explicit with self fallback."""
    from targeting import explicit as X
    f = X._spell_facts(tg_ctx, 133)
    assert f["required"] & X.TARGET_FLAG["UNIT_ENEMY"] and not f["explicit"] & X.TARGET_FLAG["UNIT_ALLY"]
    g = X._spell_facts(tg_ctx, 2061)
    assert g is not None and g["explicit"] & X.TARGET_FLAG["UNIT_ALLY"]
