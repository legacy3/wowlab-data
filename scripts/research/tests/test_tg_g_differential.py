"""Track G: cross-track probe differential (targeting/differential.py).

Rules out 'the Python stages the pipeline calls agree with Trinity only on the hand-picked
fixtures': every probe is fed seeded generated cases and the pipeline's own stage inputs are
re-checked by the probes.  Skips when the sibling TrinityCore checkout or g++ is absent.
"""

from __future__ import annotations

import json

import pytest

from targeting import CORPORA
from targeting import differential as D


@pytest.fixture(scope="module", autouse=True)
def _need_probes():
    if not D.available():
        pytest.skip("sibling TrinityCore checkout or g++ absent")


def _failures(block: dict) -> dict:
    return {k: v["first_failures"] for k, v in block["checks"].items() if v["failed"]}


def test_geometry_probe_agrees() -> None:
    out = D.geom_probe(300)
    assert all(v["cases"] == 300 for v in out["checks"].values())
    assert not _failures(out)


def test_chain_probe_agrees() -> None:
    out = D.chain_probe(60)
    assert out["checks"]["search_chain_targets"]["cases"] >= 55
    assert not _failures(out)


def test_selector_probe_agrees_without_snapshot() -> None:
    out = D.selector_probe(None)
    assert out["checks"]["selector_axes"]["cases"] == 3 * 153
    assert out["checks"]["explicit_mask_generated"]["cases"] == 400
    assert not _failures(out)


def test_pipeline_stage_inputs_agree_with_probes() -> None:
    """The centre / radius / reason / lead effect the pipeline passes to the C and D stages give the
    same answers in Trinity's compiled predicates (area candidates, chain jumps)."""
    out = D.pipeline_integration()
    assert out["checks"]["pipeline_area_candidates"]["cases"] > 20
    assert out["checks"]["pipeline_chain_jumps"]["cases"] >= 1
    assert not _failures(out)


def test_generated_coordinates_are_finite() -> None:
    import math
    import random
    rng = random.Random(1)
    assert all(math.isfinite(D._coord(rng)) for _ in range(20000))


@pytest.mark.snapshot
def test_committed_corpus_regenerates(tg_ctx) -> None:
    path = CORPORA / "differential.json"
    if not path.exists():
        pytest.skip("differential corpus not generated")
    committed = json.loads(path.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(D.run(), sort_keys=True))
    assert fresh == committed, "regenerate: python3 targeting.py differential --out ../../docs/research/targeting-corpora/differential.json"
    assert committed["summary"]["failed"] == 0
