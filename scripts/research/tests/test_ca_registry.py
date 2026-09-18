"""Core semantic boundary audit: registry integrity, coverage and corpus drift."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RESEARCH = Path(__file__).resolve().parents[1]
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from core_audit import CORPORA, PACKAGE, extract, pins, registry  # noqa: E402
from core_audit.corpora import apply_reconciliation, build, coverage, finding_cross_references  # noqa: E402

PROBE_TESTS = PACKAGE / "probe" / "tests"


@pytest.fixture(scope="module")
def records():
    loaded = registry.load_all()
    assert apply_reconciliation(loaded) == []
    return loaded


def test_core_is_at_the_audited_pin():
    pins.verify_core()


def test_registry_has_no_schema_coord_or_reference_problems(records):
    assert registry.problems(records) + finding_cross_references(records) == []


def test_mechanical_denominators_are_pinned():
    assert len(extract.rng_call_sites()) == 9
    assert len(extract.compiler_modules()) == 112
    assert len(extract.mutable_fields()) == 27
    rows = extract.catalog_rows()
    assert len(rows) == 555
    assert sum(r["support"] == "implemented" for r in rows) == 154


def test_rng_sites_all_live_in_combat():
    assert all(s["coord"].startswith("crates/combat/src/") for s in extract.rng_call_sites())


def test_every_denominator_is_covered_by_an_audited_record(records):
    assert coverage(records) == {
        "rng_sites_uncovered": [],
        "compiler_modules_uncovered": [],
        "mutable_fields_uncovered": [],
        "implemented_catalog_rows_uncovered": [],
    }


def _probe_functions() -> set[str]:
    found = set()
    for path in PROBE_TESTS.glob("*.rs"):
        for name in re.findall(r"\bfn\s+([a-z_][a-z0-9_]*)\s*\(", path.read_text(encoding="utf-8")):
            found.add(f"{path.name}::{name}")
    return found


def _probe_refs(record: dict) -> list[str]:
    refs = record.get("probe_test") or []
    if isinstance(refs, str):
        refs = [refs]
    return list(refs) + list(record.get("probe_tests") or [])


def test_every_cited_probe_exists(records):
    functions = _probe_functions()
    missing = [
        f"{kind}:{r['id']} -> {ref}"
        for kind, rows in records.items()
        for r in rows
        for ref in _probe_refs(r)
        if ref not in functions
    ]
    assert missing == []


def test_probe_mutations_name_their_probe(records):
    assert [m["id"] for m in records["mutations"] if m["method"] == "probe" and not _probe_refs(m)] == []


def test_live_and_latent_findings_carry_reproducers(records):
    weak = [
        f["id"]
        for f in records["findings"]
        if f["status"] != "rejected"
        and f["reachability"] in ("LIVE", "LATENT")
        and not (f["coords"] and f["reproducer"])
    ]
    assert weak == []


def test_live_findings_are_probe_backed(records):
    unbacked = [
        f["id"]
        for f in records["findings"]
        if f["status"] == "confirmed" and f["reachability"] == "LIVE" and not _probe_refs(f)
    ]
    assert unbacked == []


def test_provenance_findings_cite_a_finding(records):
    assert [
        p["id"] for p in records["provenance_loss"] if p["verdict"] == "finding" and not p.get("finding_ids")
    ] == []


def test_corpora_on_disk_match_the_registry():
    files = build()
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in CORPORA.glob("*.json")}
    assert sorted(on_disk) == sorted(files)
    assert [name for name in files if on_disk.get(name) != files[name]] == []


def test_merged_findings_point_at_a_surviving_finding(records):
    by_id = {f["id"]: f for f in records["findings"]}
    bad = [
        f["id"]
        for f in records["findings"]
        if f["status"] == "merged" and by_id[f["merged_into"]]["status"] in ("merged", "rejected")
    ]
    assert bad == []
