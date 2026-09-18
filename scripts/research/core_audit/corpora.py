"""Deterministic corpus generation for the Core semantic boundary audit."""

from __future__ import annotations

import json
import re
from collections import Counter

from . import CORPORA, FailClosed, extract, pins, registry
from .schema import KINDS

CORPUS_NAMES = {
    "authorities": "authority-inventory.json",
    "fact_flow": "fact-flow-graph.json",
    "provenance_loss": "provenance-loss.json",
    "cross_compiler": "cross-compiler-matrix.json",
    "mutations": "mutation-outcomes.json",
    "numeric": "numeric-boundaries.json",
    "rng": "rng-sites.json",
    "lifecycle": "mutable-lifecycle.json",
    "catalog": "catalog-consistency.json",
    "external_policy": "external-policy-boundary.json",
    "allocation": "allocation-contracts.json",
    "findings": "findings.json",
    "unknowns": "unknowns.json",
    "rejected": "rejected-suspicions.json",
}


def _dump(payload) -> str:
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _clean(record: dict) -> dict:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def _coord_file(coord: str) -> str:
    return coord.split(":", 1)[0]


def _coord_line(coord: str) -> tuple[str, int, int]:
    path, span = coord.split(":", 1)
    first, _, last = span.partition("-")
    return path, int(first), int(last or first)


def coverage(records: dict[str, list[dict]]) -> dict:
    """Which mechanical denominators have no hand-audited record."""

    def covered_by(kind: str) -> list[str]:
        return [c for r in records[kind] for c in registry.record_coords(r)]

    rng_coords = [_coord_line(c) for c in covered_by("rng")]
    rng_missing = []
    for site in extract.rng_call_sites():
        path, line, _ = _coord_line(site["coord"])
        if not any(p == path and a <= line <= b for p, a, b in rng_coords):
            rng_missing.append(site["coord"])

    authority_files = {_coord_file(c) for c in covered_by("authorities")}
    compiler_missing = []
    for module in extract.compiler_modules():
        prefix = module["file"][: -len(".rs")] + "/"
        if module["file"] not in authority_files and not any(
            f.startswith(prefix) for f in authority_files
        ):
            compiler_missing.append(module["file"])

    lifecycle_fields = {(r.get("owner"), r.get("field")) for r in records["lifecycle"]}
    field_missing = [
        f"{f['struct']}.{f['field']}"
        for f in extract.mutable_fields()
        if (f["struct"], f["field"]) not in lifecycle_fields
    ]

    catalog_seen = {(r["catalog"], r["raw"]) for r in records["catalog"]}
    catalog_missing = [
        f"{row['catalog']}:{row['raw']}"
        for row in extract.catalog_rows()
        if row["support"] == "implemented" and (row["catalog"], row["raw"]) not in catalog_seen
    ]

    return {
        "rng_sites_uncovered": sorted(rng_missing),
        "compiler_modules_uncovered": sorted(compiler_missing),
        "mutable_fields_uncovered": sorted(field_missing),
        "implemented_catalog_rows_uncovered": sorted(catalog_missing),
    }


def finding_cross_references(records: dict[str, list[dict]]) -> list[str]:
    ids = {r["id"] for r in records["findings"]}
    problems = []
    for kind, rows in records.items():
        for record in rows:
            for ref in record.get("finding_ids", []) or []:
                if ref not in ids:
                    problems.append(f"{kind}:{record['id']} references unknown finding {ref}")
    return problems


RECONCILIATION = registry.REGISTRY / "reconciliation.json"
_OVERRIDABLE = ("status", "severity", "reachability", "title", "correction_boundary")


def apply_reconciliation(records: dict[str, list[dict]]) -> list[str]:
    """Apply the lead's post-review verdicts; the track's original values are kept under ``original``."""
    if not RECONCILIATION.is_file():
        return []
    verdicts = json.loads(RECONCILIATION.read_text(encoding="utf-8"))
    by_id = {f["id"]: f for f in records["findings"]}
    unknowns = {u["id"]: u for u in records["unknowns"]}
    problems = []
    for fid, verdict in sorted(verdicts.items()):
        if fid.startswith("UNK-"):
            if fid not in unknowns:
                problems.append(f"reconciliation names unknown record {fid}")
            else:
                unknowns[fid]["closed_by"] = verdict["closed_by"]
            continue
        finding = by_id.get(fid)
        if finding is None:
            problems.append(f"reconciliation names unknown finding {fid}")
            continue
        original = {k: finding[k] for k in _OVERRIDABLE if k in verdict and k in finding}
        for key, value in verdict.items():
            if key in _OVERRIDABLE or key in ("merged_into", "review", "absorbs", "canonical_rank"):
                finding[key] = value
        if original:
            finding["original"] = original
        target = verdict.get("merged_into")
        if target and target not in by_id:
            problems.append(f"{fid} merged into unknown {target}")
        if (verdict.get("status") == "merged") != bool(target):
            problems.append(f"{fid}: status merged iff merged_into")
    return problems


def build() -> dict[str, str]:
    pins.verify_core()
    records = registry.load_all()
    recon_problems = apply_reconciliation(records)
    problems = registry.problems(records) + finding_cross_references(records) + recon_problems
    if problems:
        raise FailClosed("registry problems:\n  " + "\n  ".join(problems[:60]))

    header = {"pins": pins.PINS, "schema": 1}
    files: dict[str, str] = {}
    referenced: set[str] = set()

    for kind, name in CORPUS_NAMES.items():
        rows = sorted((_clean(r) for r in records[kind]), key=lambda r: r["id"])
        for record in rows:
            referenced.update(_coord_file(c) for c in registry.record_coords(record))
        files[name] = _dump({**header, "kind": kind, "count": len(rows), "records": rows})

    inventory = {
        "rng_call_sites": extract.rng_call_sites(),
        "compiler_modules": extract.compiler_modules(),
        "runtime_modules": extract.runtime_modules(),
        "mutable_fields": extract.mutable_fields(),
        "catalog_rows": extract.catalog_rows(),
    }
    for site in inventory["rng_call_sites"]:
        referenced.add(_coord_file(site["coord"]))
    for field in inventory["mutable_fields"]:
        referenced.add(_coord_file(field["coord"]))
    referenced.update(extract.CATALOGS[c][0] for c in extract.CATALOGS)
    referenced.add("crates/dbc/src/implicit_target.rs")
    files["core-extracted-inventory.json"] = _dump({**header, **inventory})

    cover = coverage(records)
    files["coverage.json"] = _dump({**header, **cover})

    findings = records["findings"]
    canonical = [f for f in findings if f["status"] not in ("merged", "rejected")]
    summary = {
        "canonical_findings": len(canonical),
        "canonical_by_reachability_severity": dict(
            sorted(Counter(f"{f['reachability']}/{f['severity']}" for f in canonical).items())
        ),
        **header,
        "records": {kind: len(records[kind]) for kind in KINDS},
        "findings_by_reachability": dict(sorted(Counter(f["reachability"] for f in findings).items())),
        "findings_by_severity": dict(sorted(Counter(f["severity"] for f in findings).items())),
        "findings_by_status": dict(sorted(Counter(f["status"] for f in findings).items())),
        "mutations_by_outcome": dict(sorted(Counter(m["outcome"] for m in records["mutations"]).items())),
        "cross_compiler_by_class": dict(sorted(Counter(c["class"] for c in records["cross_compiler"]).items())),
        "numeric_by_evidence": dict(sorted(Counter(n["evidence_class"] for n in records["numeric"]).items())),
        "provenance_by_pattern": dict(sorted(Counter(p["pattern"] for p in records["provenance_loss"]).items())),
        "denominators": {
            "rng_call_sites": len(inventory["rng_call_sites"]),
            "compiler_modules": len(inventory["compiler_modules"]),
            "runtime_modules": len(inventory["runtime_modules"]),
            "mutable_fields": len(inventory["mutable_fields"]),
            "catalog_rows": len(inventory["catalog_rows"]),
        },
        "uncovered": {key: len(value) for key, value in cover.items()},
    }
    files["summary.json"] = _dump(summary)
    files["core-source-hashes.json"] = _dump({**header, "files": extract.source_hashes(referenced)})
    return files


def write(files: dict[str, str]) -> None:
    CORPORA.mkdir(parents=True, exist_ok=True)
    stale = {p.name for p in CORPORA.glob("*.json")} - set(files)
    for name in stale:
        (CORPORA / name).unlink()
    for name, text in files.items():
        (CORPORA / name).write_text(text, encoding="utf-8")


_SAFE = re.compile(r"^[a-z0-9-]+\.json$")
assert all(_SAFE.match(n) for n in CORPUS_NAMES.values())
