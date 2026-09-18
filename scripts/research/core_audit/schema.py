"""Record kinds of the audit registry.

Every record is one JSON object with a stable ``id``. ``coords`` entries are
``"<path relative to Core root>:<line>"`` or ``"<path>:<first>-<last>"`` and are
checked against the pinned tree. Unknown keys are allowed; missing required keys
and out-of-vocabulary enum values fail closed.
"""

from __future__ import annotations

TRACKS = {
    "A": "selected traits / package provenance / atomicity",
    "B": "SpellMods / cooldowns / labels / masks",
    "C": "critical chance / bonus / block",
    "D": "direct damage / healing / mitigation / absorbs",
    "E": "periodic semantics / schedules",
    "F": "white attacks / weapon / swing scheduling",
    "G": "casts / interruption / cooldown lifecycle",
    "H": "state construction / actors / reset",
    "I": "RNG / quote / commit",
    "J": "numeric precision / conversions",
    "K": "catalogs / source / external policy",
    "L": "allocation / pay-for-play",
    "X": "cross-authority comparison",
    "R1": "hostile review: false positives",
    "R2": "hostile review: false negatives",
    "R3": "hostile review: lifecycle / numeric / RNG",
    "LEAD": "lead reconciliation",
}

STAGES = (
    "source",
    "source_resolution",
    "candidate",
    "resolved_catalog",
    "combat_program",
    "state_construction",
    "quote",
    "rng_outcome",
    "commit",
    "reset",
    "iteration_reset",
)

REACHABILITY = ("LIVE", "LATENT", "INCONSISTENT", "UNKNOWN", "UNREACHABLE")
SEVERITY = ("critical", "high", "medium", "low", "info")
FINDING_STATUS = ("confirmed", "downgraded", "rejected", "open", "merged")

PROVENANCE_PATTERNS = (
    "dropped_then_reconstructed",
    "selected_amount_to_authored",
    "exact_effect_to_provider",
    "actor_to_global",
    "package_to_authority_family",
    "premature_f64_promotion",
    "retained_never_validated",
    "retained_never_consumed",
    "lossless",
)

CROSS_CLASSES = ("A", "B", "C", "D", "E", "F")

MUTATION_OUTCOMES = (
    "rejected_intended",
    "rejected_accidental",
    "accepted_inert",
    "accepted_suspicious",
    "unreachable",
    "inconclusive",
)

MUTATION_METHODS = ("probe", "static", "core_test", "mutant_build")

NUMERIC_EVIDENCE = (
    "direct_consumer_reproduced",
    "source_backed",
    "core_choice",
    "assumed",
    "known_divergence",
    "unknown",
)

KINDS: dict[str, dict] = {
    "authorities": {
        "required": ("id", "track", "name", "layer", "coords", "semantic_facts"),
        "enums": {
            "layer": (
                "source",
                "source_resolution",
                "candidate",
                "resolved_catalog",
                "compiler",
                "state_construction",
                "runtime",
                "rng",
                "reset",
            )
        },
    },
    "fact_flow": {
        "required": ("id", "track", "authority", "fact", "stages"),
        "enums": {},
    },
    "provenance_loss": {
        "required": ("id", "track", "authority", "fact", "pattern", "coords", "verdict"),
        "enums": {
            "pattern": PROVENANCE_PATTERNS,
            "verdict": ("finding", "clean", "inert", "unknown"),
        },
    },
    "cross_compiler": {
        "required": ("id", "track", "concept", "compilers", "class", "evidence"),
        "enums": {"class": CROSS_CLASSES},
    },
    "mutations": {
        "required": (
            "id",
            "track",
            "contract",
            "dimension",
            "mutation",
            "outcome",
            "method",
            "evidence",
        ),
        "enums": {"outcome": MUTATION_OUTCOMES, "method": MUTATION_METHODS},
    },
    "numeric": {
        "required": ("id", "track", "coords", "conversion", "evidence_class", "observable"),
        "enums": {"evidence_class": NUMERIC_EVIDENCE},
    },
    "rng": {
        "required": (
            "id",
            "track",
            "coords",
            "site",
            "draws",
            "order",
            "quote",
            "failure",
            "reset",
        ),
        "enums": {},
    },
    "lifecycle": {
        "required": (
            "id",
            "track",
            "owner",
            "field",
            "coords",
            "init",
            "mutation_sites",
            "quote_visible",
            "failure",
            "reset",
            "iteration_reset",
        ),
        "enums": {},
    },
    "catalog": {
        "required": ("id", "track", "catalog", "raw", "support", "consumers", "verdict"),
        "enums": {
            "catalog": (
                "SPELL_EFFECT_REQUIREMENTS",
                "SPELL_ATTRIBUTE_REQUIREMENTS",
                "AURA_SUBTYPE_REQUIREMENTS",
                "EFFECT_ATTRIBUTE_REQUIREMENTS",
                "IMPLICIT_TARGET_SEMANTICS",
            ),
            "verdict": ("consistent", "finding", "unknown"),
        },
    },
    "external_policy": {
        "required": ("id", "track", "boundary", "coords", "claim", "verdict"),
        "enums": {"verdict": ("holds", "finding", "unknown")},
    },
    "allocation": {
        "required": ("id", "track", "contract", "coords", "evidence", "verdict"),
        "enums": {"verdict": ("holds", "finding", "unknown")},
    },
    "findings": {
        "required": (
            "id",
            "track",
            "title",
            "severity",
            "reachability",
            "status",
            "coords",
            "evidence",
            "reproducer",
            "why_tests_missed",
            "correction_boundary",
            "must_not_widen",
        ),
        "enums": {
            "severity": SEVERITY,
            "reachability": REACHABILITY,
            "status": FINDING_STATUS,
        },
    },
    "unknowns": {
        "required": ("id", "track", "question", "why_unknown", "reopen"),
        "enums": {},
    },
    "rejected": {
        "required": ("id", "track", "suspicion", "why_rejected", "coords"),
        "enums": {},
    },
}


def validate(kind: str, record: dict) -> list[str]:
    spec = KINDS[kind]
    problems = [f"missing {key}" for key in spec["required"] if key not in record]
    for key, allowed in spec["enums"].items():
        if key in record and record[key] not in allowed:
            problems.append(f"{key}={record[key]!r} not in {allowed}")
    track = record.get("track")
    if track is not None and track not in TRACKS:
        problems.append(f"track={track!r} unknown")
    if kind == "fact_flow":
        for stage in record.get("stages", []):
            if stage.get("stage") not in STAGES:
                problems.append(f"stage {stage.get('stage')!r} not in STAGES")
    return problems
