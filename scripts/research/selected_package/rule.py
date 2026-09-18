"""Candidate generic admission rules, and the falsification ledger.

A rule is a *pipeline of named dimensions*.  Each dimension is a predicate over
source facts only -- never over an entry, definition, provider or label id -- and
each one can be switched on or off independently.  That is what makes the ledger
meaningful: an iteration adds exactly one dimension, and the corpus records what
that dimension did to the false-positive and false-negative counts.

The hypothesis under test (from the mission):

    selected source provenance
      x complete exact-effect topology
      x per-effect semantic classification
      x rank-shaping provenance
      x owner acquisition/lifetime policy
      x effective proc policy
      x package atomicity
    = immutable selected-passive admission

Scoring is always against :class:`selected_package.corebaseline.CoreBaseline`
``admit_final`` -- the cold contract AND final owner revalidation -- because the
cold contract alone overstates what Core actually compiles.

Vocabulary:
    false positive  the rule admits an entry x rank Core does not
    false negative  Core admits an entry x rank the rule does not

A false positive is only a defect once it is shown to need semantics Core has not
proved; a false positive that is a *legitimate generalization* is the point of the
exercise.  Every one is classified individually, never in bulk.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from .corebaseline import CoreBaseline
from .effects import Classification, classify_package
from .owner import Owners
from .provenance import Provenance, Resolved, TraitSpellError

#: Verdicts a false positive can receive.  Mission-mandated taxonomy.
FP_CLASSES = (
    "likely-legitimate-generalization",
    "blocked-by-unsupported-effect-semantic",
    "conditional-or-active",
    "proc-driven",
    "stacking-or-lifetime-dependent",
    "malformed-or-build-skewed",
    "acquisition-unknown",
    "genuine-counterexample",
)


@dataclass
class Verdict:
    """The rule's answer for one entry x rank, and why."""

    entry: int
    rank: int
    provider: int | None
    admitted: bool
    roles: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    #: dimension name -> passed?
    dimensions: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"entry": self.entry, "rank": self.rank, "provider": self.provider,
                "admitted": self.admitted, "roles": list(self.roles),
                "blockers": sorted(self.blockers), "dimensions": dict(self.dimensions)}


@dataclass(frozen=True)
class Dimension:
    """One named, independently switchable admission dimension."""

    name: str
    description: str
    #: (ctx, resolved, classifications) -> list of blockers (empty == passes)
    check: Callable[..., list[str]]


class Context:
    """Loaded evidence shared by every rule evaluation."""

    def __init__(self, provenance: Provenance | None = None) -> None:
        self.provenance = provenance or Provenance()
        self.owners = Owners(self.provenance.source)
        self.baseline = CoreBaseline(self.provenance, self.owners)
        self._proc = None

    @property
    def proc(self):
        """The track-D proc policy, if its module is present; else None (dimension skipped)."""
        if self._proc is None:
            try:
                from .procpolicy import ProcPolicy
                self._proc = ProcPolicy(self.provenance.source)
            except Exception:                       # noqa: BLE001 - optional dimension
                self._proc = False
        return self._proc or None

    def variants(self):
        """Every (entry, rank) Core's source resolver can resolve, in stable order."""
        for entry_id in sorted(self.provenance.traits.entries):
            entry = self.provenance.traits.entries[entry_id]
            if entry.max_ranks not in (1, 2):
                continue
            for rank in range(1, entry.max_ranks + 1):
                yield entry_id, rank


# ---------------------------------------------------------------------------
# dimensions
# ---------------------------------------------------------------------------

def _d_effects_classified(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D2+D3: every exact effect of the COMPLETE package enters an ordinary role."""
    out = []
    for classification in cls:
        if not classification.ok:
            out.extend(f"{classification.effect.ref}: {blocker}"
                       for blocker in classification.blockers)
    return out


def _d_owner_policy(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D5: the owner permits immutable passive preparation."""
    return list(ctx.owners.policy(resolved.spell).blockers)


def _d_rank_amounts(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D4: every effect resolves to a finite amount at every rank in the domain.

    **VACUOUS — retained only as a proved redundancy.**  It never fires over the whole
    population, because ``SelectedTraitPointOperation::apply`` (selected_spell.rs:82-91,
    ported in ``provenance._apply``) already refuses a non-finite result at resolution
    time, and an unshaped effect keeps its authored binary32 base points, which are finite
    by construction.  It is kept in the ladder so the ledger records the redundancy as a
    measured fact rather than an assumption; ``test_sp_a_oracle.py`` asserts it stays
    vacuous, so if provenance ever stops guaranteeing this the test fails loudly.
    """
    out = []
    for effect in resolved.effects:
        for rank in range(1, resolved.max_ranks + 1):
            amount = resolved.amount_at_rank(effect.index, rank)
            if amount is None or amount != amount or abs(amount) == float("inf"):
                out.append(f"{effect.ref}: rank {rank} amount is not finite")
    return out


def _d_shaping_uniformity(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D4-rejected: all siblings share one shaping operation.

    **This dimension is FALSIFIED and is retained only so the ledger can show why.**
    Improved Vivify (entry 101510, provider 231602) is a package Core admits whose
    effect 1 is `Set`-shaped 20 -> 40 while effect 2 keeps its authored 40 unshaped, so
    the two siblings carry *different* values at rank 1.  Requiring uniform shaping
    therefore produces a false negative against Core's own admitted population, which is
    evidence that mixed shaping is legitimate source authoring rather than a defect.
    """
    out = []
    operations = {resolved.point_operation(effect.index) for effect in resolved.effects}
    if resolved.max_ranks == 2 and None in operations and len(operations) > 1:
        out.append("mixed shaped/unshaped siblings at two ranks")
    if len({op for op in operations if op}) > 1:
        out.append("mixed Set and Multiply shaping in one package")
    return out


def _d_percent_floor(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D4b: percentage roles need points >= -100 at every rank (Core's own floor)."""
    out = []
    for classification in cls:
        role = classification.role
        if role is None or role.value not in ("Percent", "PercentFirstEffect"):
            continue
        for rank in range(1, resolved.max_ranks + 1):
            amount = resolved.amount_at_rank(classification.effect.index, rank)
            if amount is not None and amount < -100.0:
                out.append(f"{classification.effect.ref}: rank {rank} percentage {amount} < -100")
    return out


def _d_integral_flats(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D4c: flat roles need exactly representable i32 amounts; cooldown flats must be negative."""
    out = []
    for classification in cls:
        role = classification.role
        if role is None or role.value not in ("FlatFirstEffect", "FlatCooldown"):
            continue
        for rank in range(1, resolved.max_ranks + 1):
            amount = resolved.amount_at_rank(classification.effect.index, rank)
            if amount is None or amount != amount or float(int(amount)) != amount \
                    or not -(2**31) <= int(amount) < 2**31:
                out.append(f"{classification.effect.ref}: rank {rank} flat is not an exact i32")
            elif role.value == "FlatCooldown" and int(amount) >= 0:
                out.append(f"{classification.effect.ref}: rank {rank} cooldown flat is not negative")
    return out


def _d_proc_policy(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D6: any AuraOptions row must be provably inert (track D's oracle)."""
    proc = ctx.proc
    if proc is None:
        return []
    try:
        return list(proc.classify(resolved.spell).blockers)
    except Exception as exc:                        # noqa: BLE001 - fail closed
        return [f"proc policy unavailable: {exc}"]


def _d_effect_ceiling(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D7a: Core stores at most four effects per package (MAX_PACKAGE_EFFECTS)."""
    return ([f"{len(resolved.effects)} effects exceed the four-effect package ceiling"]
            if len(resolved.effects) > 4 else [])


def _d_role_uniqueness(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D7b: no two effects of one package may claim the same non-modifier authority.

    Two raw-344 effects on one provider would both drive auto-attack damage for one
    actor with no source-backed composition order.
    """
    counts = Counter(c.role.name for c in cls if c.role and c.role.name != "SpellModifier")
    return [f"{name} appears {count} times in one package"
            for name, count in sorted(counts.items()) if count > 1]


def _d_identity(ctx, resolved: Resolved, cls: list[Classification]) -> list[str]:
    """D7c: the provider must not be reachable from a second resolvable entry.

    Core's state side keys package atomicity on the provider SpellId alone
    (construction.rs:299-360 `mixed_passive_effects(program, owner)`), so a provider
    reachable from two entries -- possibly at different rank domains -- has no unique
    package identity.  This dimension exists to MEASURE that exposure, not to endorse it.
    """
    entries = ctx.resolvable_entries_for_provider(resolved.spell)
    return ([f"provider {resolved.spell} is reachable from {len(entries)} resolvable entries "
             f"({sorted(entries)[:4]}...)"] if len(entries) > 1 else [])


DIMENSIONS: dict[str, Dimension] = {
    "effects": Dimension("effects", "every exact effect of the complete package has an ordinary role",
                         _d_effects_classified),
    "owner": Dimension("owner", "owner acquisition/lifetime permits immutable passive preparation",
                       _d_owner_policy),
    "rank_amounts": Dimension("rank_amounts",
                              "VACUOUS: every rank amount is finite (already guaranteed "
                              "by source resolution; never fires)",
                              _d_rank_amounts),
    "shaping_uniformity": Dimension(
        "shaping_uniformity",
        "FALSIFIED: all siblings share one shaping operation (Improved Vivify refutes it)",
        _d_shaping_uniformity),
    "percent_floor": Dimension("percent_floor", "percentage points are at least -100 at every rank",
                               _d_percent_floor),
    "integral_flats": Dimension("integral_flats", "flat amounts are exact i32; cooldown flats negative",
                                _d_integral_flats),
    "proc_policy": Dimension("proc_policy", "any AuraOptions row is provably inert",
                             _d_proc_policy),
    "effect_ceiling": Dimension("effect_ceiling", "at most four effects per package",
                                _d_effect_ceiling),
    "role_uniqueness": Dimension("role_uniqueness", "no repeated non-modifier authority",
                                 _d_role_uniqueness),
    "identity": Dimension("identity", "the provider identifies exactly one resolvable package",
                          _d_identity),
}


@dataclass(frozen=True)
class Rule:
    """One numbered candidate rule: a set of dimensions, in a fixed order."""

    version: str
    summary: str
    dimensions: tuple[str, ...]

    def evaluate(self, ctx: Context, entry: int, rank: int) -> Verdict:
        resolved = ctx.provenance.resolve(entry, rank)
        if isinstance(resolved, TraitSpellError):
            return Verdict(entry, rank, None, False,
                           blockers=[f"source:{resolved.value}"],
                           dimensions={"provenance": False})
        cls = classify_package(resolved)
        verdict = Verdict(entry, rank, resolved.spell, True,
                          roles=[c.role.key() if c.role else "?" for c in cls],
                          dimensions={"provenance": True})
        for name in self.dimensions:
            blockers = DIMENSIONS[name].check(ctx, resolved, cls)
            verdict.dimensions[name] = not blockers
            if blockers:
                verdict.admitted = False
                verdict.blockers.extend(f"[{name}] {b}" for b in blockers)
        return verdict


def _resolvable_entries_for_provider(self: Context):
    cache: dict[int, set[int]] = getattr(self, "_provider_entries", None)
    if cache is None:
        cache = defaultdict(set)
        for entry_id, rank in self.variants():
            if rank != 1:
                continue
            resolved = self.provenance.resolve(entry_id, rank)
            if not isinstance(resolved, TraitSpellError):
                cache[resolved.spell].add(entry_id)
        self._provider_entries = cache
    return cache


Context.resolvable_entries_for_provider = lambda self, spell: _resolvable_entries_for_provider(self).get(spell, set())


# ---------------------------------------------------------------------------
# the numbered candidate rules
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule("v1", "per-effect semantic classification of the complete package, plus owner policy",
         ("effects", "owner")),
    Rule("v2", "v1 + rank-amount provenance -- measured VACUOUS, retained as a proved redundancy",
         ("effects", "owner", "rank_amounts")),
    Rule("v2x", "FALSIFIED probe: v2 + uniform sibling shaping",
         ("effects", "owner", "rank_amounts", "shaping_uniformity")),
    Rule("v3", "v2 + Core's own amount floors (percentage >= -100, exact negative i32 cooldowns)",
         ("effects", "owner", "rank_amounts", "percent_floor", "integral_flats")),
    Rule("v4", "v3 + effective proc policy",
         ("effects", "owner", "rank_amounts", "percent_floor", "integral_flats", "proc_policy")),
    Rule("v5", "v4 + package atomicity (four-effect ceiling, unique non-modifier authority)",
         ("effects", "owner", "rank_amounts", "percent_floor", "integral_flats", "proc_policy",
          "effect_ceiling", "role_uniqueness")),
    # v5 is the final candidate rule.  v5x is a MEASUREMENT, not a candidate: it adds the
    # provider-uniqueness dimension to quantify how much of Core's own admitted population
    # would be lost if package identity were keyed on the provider spell alone -- which is
    # exactly what Core's state side does today.  Its false negatives are the exposure, not
    # a defect in v5, so they are deliberately left unadjudicated.
    Rule("v5x", "MEASUREMENT: v5 + unique provider identity, to size the state-side exposure",
         ("effects", "owner", "rank_amounts", "percent_floor", "integral_flats", "proc_policy",
          "effect_ceiling", "role_uniqueness", "identity")),
)


def score(ctx: Context, rule: Rule) -> dict[str, Any]:
    """Run one rule over the whole population and diff it against Core."""
    admitted, core = set(), set()
    verdicts: dict[tuple[int, int], Verdict] = {}
    core_branch: dict[tuple[int, int], str] = {}

    for entry, rank in ctx.variants():
        key = (entry, rank)
        verdict = rule.evaluate(ctx, entry, rank)
        verdicts[key] = verdict
        if verdict.admitted:
            admitted.add(key)
        decision = ctx.baseline.admit_final(entry, rank)
        if not isinstance(decision, str):
            core.add(key)
            core_branch[key] = decision.branch

    false_positives = sorted(admitted - core)
    false_negatives = sorted(core - admitted)
    return {
        "rule": {"version": rule.version, "summary": rule.summary,
                 "dimensions": list(rule.dimensions)},
        "population": len(verdicts),
        "core_admitted": len(core),
        "rule_admitted": len(admitted),
        "false_positives": len(false_positives),
        "false_negatives": len(false_negatives),
        "agreement": len(admitted & core),
        "_fp_keys": false_positives,
        "_fn_keys": false_negatives,
        "_verdicts": verdicts,
        "_core_branch": core_branch,
    }


# ---------------------------------------------------------------------------
# false-positive classification
# ---------------------------------------------------------------------------
#
# Every false positive is classified individually.  A false positive is NOT
# automatically a defect: the whole point of the pass is to find packages whose
# semantics Core has already proved but whose *identity* it has not been told about.
# The taxonomy is the mission's; `differs_in` names the dimension on which the
# package departs from the nearest Core branch that admits the same role multiset.

#: Role multisets Core admits today, branch -> (multiset, rank domain, shaping)
def core_admitted_shapes(ctx: Context) -> dict[str, dict[str, Any]]:
    shapes: dict[str, dict[str, Any]] = {}
    for entry, rank in ctx.variants():
        decision = ctx.baseline.admit_final(entry, rank)
        if isinstance(decision, str):
            continue
        resolved = ctx.provenance.resolve(entry, rank)
        multiset = tuple(sorted(c.role.key() for c in classify_package(resolved) if c.role))
        record = shapes.setdefault(decision.branch, {
            "multisets": {}, "count": 0})
        # One branch can admit several role multisets (generic_single_effect covers six),
        # so rank domain and shaping are tracked PER MULTISET, not per branch.
        per_shape = record["multisets"].setdefault(
            multiset, {"rank_domains": set(), "shapings": set(), "count": 0})
        per_shape["rank_domains"].add(resolved.max_ranks)
        per_shape["shapings"].add(tuple(resolved.point_operation(e.index)
                                        for e in resolved.effects))
        per_shape["count"] += 1
        record["count"] += 1
    return shapes


def classify_false_positive(ctx: Context, entry: int, rank: int,
                            shapes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    resolved = ctx.provenance.resolve(entry, rank)
    cls = classify_package(resolved)
    multiset = tuple(sorted(c.role.key() for c in cls if c.role))
    shaping = tuple(resolved.point_operation(e.index) for e in resolved.effects)
    record: dict[str, Any] = {
        "entry": entry, "rank": rank, "provider": resolved.spell,
        "definition": resolved.definition_id,
        "effects": len(resolved.effects),
        "roles": [c.role.key() for c in cls if c.role],
        "max_ranks": resolved.max_ranks,
        "shaping": [op or "none" for op in shaping],
        "amounts": [resolved.selected_amount(e.index) for e in resolved.effects],
    }

    if len(resolved.effects) > 4:
        record["class"] = "blocked-by-unsupported-effect-semantic"
        record["differs_in"] = ["package-effect-ceiling"]
        record["reason"] = (f"{len(resolved.effects)} effects exceed MAX_PACKAGE_EFFECTS = 4 "
                            "(selected_trait_package.rs:26); a storage bound, not a proved "
                            "semantic boundary")
        return record

    match = next(((branch, shape["multisets"][multiset])
                  for branch, shape in sorted(shapes.items())
                  if multiset in shape["multisets"]), None)
    if match is None:
        record["nearest_branch"] = None
        record["differs_in"] = ["role-multiset-has-no-Core-composition"]
        if len(resolved.effects) == 1:
            record["class"] = "likely-legitimate-generalization"
            role = cls[0].role if cls and cls[0].role else None
            if role is not None and role.name != "SpellModifier":
                record["reason"] = ("sole-effect package whose role has its own ordinary "
                                    "authority compiler; no package composition is needed")
            elif role is not None and role.generic_in_core:
                record["reason"] = ("sole-effect spell modifier in a cell "
                                    "`generic_spell_modifier` already reaches; refused for a "
                                    "reason outside the role itself")
            else:
                # Do NOT claim an authority compiler here: these are the cross-product cells
                # Core compiles only inside a named branch (raw-108 op 15, raw-218 ops 0 and 3,
                # and the cells hostile review 2 restored).  Admitting them is a widening of
                # Core's classifier, not a use of an existing generic path.
                record["reason"] = ("sole-effect spell modifier in a cross-product cell Core "
                                    "compiles only inside a named branch; admitting it widens "
                                    "`generic_spell_modifier` rather than reusing it")
        else:
            record["class"] = "unreviewed-composition"
            record["reason"] = ("multi-effect role multiset Core has never composed; admitting it "
                                "needs a composition rule, which is exactly what this pass is "
                                "trying to derive -- not a defect, an open item")
        return record

    branch, shape = match
    differs = []
    # Core's refusal can come from the cold contract OR from final owner revalidation;
    # consult the real one rather than inferring it from the shape alone.
    decision = ctx.baseline.admit_final(entry, rank)
    if isinstance(decision, str) and decision.startswith("owner:"):
        differs.append("owner-policy (" + decision[len("owner:"):] + ")")
    # A sole-effect package can also be refused by its ordinary authority compiler, whose
    # refusal never carries the "owner:" prefix.  Ask that path directly, or an
    # amount/identity refusal is mislabelled as "identity only".
    if len(resolved.effects) == 1:
        try:
            from .soleauthority import admit_sole_effect
            role, sole_blockers = admit_sole_effect(resolved)
        except ImportError:
            role, sole_blockers = None, []
        if role is None and sole_blockers:
            identity_only = all(b.startswith("identity:") for b in sole_blockers)
            differs.append(("sole-authority identity pin (" if identity_only
                            else "sole-authority refusal (")
                           + "; ".join(sole_blockers) + ")")
    if resolved.max_ranks not in shape["rank_domains"]:
        differs.append(f"rank-domain (Core admits {sorted(shape['rank_domains'])}, "
                       f"this is {resolved.max_ranks})")
    if shaping not in shape["shapings"]:
        differs.append("rank-shaping (Core admits "
                       f"{sorted(tuple(o or 'none' for o in sh) for sh in shape['shapings'])}, "
                       f"this is {[op or 'none' for op in shaping]})")
    if not differs:
        differs.append("identity only (Core pins an entry/definition/spell literal)")
    record["core_refusal"] = decision if isinstance(decision, str) else None

    record["class"] = "likely-legitimate-generalization"
    record["nearest_branch"] = branch
    record["differs_in"] = differs
    record["reason"] = (f"same role multiset as Core's `{branch}` branch; refused only on "
                        + "; ".join(differs))
    return record
