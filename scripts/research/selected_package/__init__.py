"""Selected-passive package generalization research.

Question this package answers:

    What is the smallest *source-backed* rule that admits exactly the selected
    passive trait packages Core admits today, expressed only through reusable
    semantic dimensions -- never through talent, entry, definition or spell
    identity?

Nothing here is production code and nothing here may be imported by Core.  The
modules mirror one Core stage each so a finding can be quoted as a diff against
a named Core symbol:

``source``      the trait tables, projected (mirrors ``wowlab_data::TraitSourceCatalog``)
``provenance``  entry -> definition -> provider -> ranked amounts
                (mirrors ``TraitSourceCatalog::try_selected_spell_effect_amounts``)
``effects``     exact-effect semantic facts and their classification into roles
                (mirrors ``SelectedTraitEffectRole`` / ``SelectedSpellModifierValue``)
``owner``       provider sidecar census and owner-policy vocabulary
                (mirrors ``SelectedTraitOwnerContract`` + ``exact_source.rs`` shells)
``procpolicy``  AuraOptions -> effective proc generation (reuses the proc oracle)
``coreref``     a transcription of Core's *current* admission branches, used only
                as the baseline the candidate rules are scored against
``rule``        candidate generic rules + the falsification ledger

Evidence discipline: every classification carries an ``evidence`` string from
:data:`EVIDENCE_CLASSES`.  A fact that cannot be proved from the pinned source is
``unknown`` with a ``reopen`` condition; it never silently becomes a default.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TABLES = ROOT / "data" / "tables"
CORPORA = ROOT / "docs" / "research" / "selected-package-corpora"
CORE = Path("/home/dev/pallet/core")

#: Strength of the evidence behind one recorded fact.
EVIDENCE_CLASSES = ("source", "core", "trinity", "probe", "inferred", "unknown")


class FailClosed(Exception):
    """Raised when evidence is missing; the oracle never guesses."""


def evidence(value: str) -> str:
    if value not in EVIDENCE_CLASSES:
        raise FailClosed(f"unknown evidence class {value!r}")
    return value
