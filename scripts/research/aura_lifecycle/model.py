"""Lead synthesis: candidate semantic axes computed per provider spell, then falsified.

Each axis is a *policy value* computed by the owning track's pure classifier
(never re-implemented here), so the model can only be as right as the track
evidence it composes:

=================  =====================================================  =========
axis               value (Trinity default path, no mods, no scripts)       owner
=================  =====================================================  =========
AuraIdentity       refresh lookup scope + cross-caster outcome              A
ApplicationPolicy  passive/active x recipient pipeline set                  A, F, G
AmountPolicy       per-stack scaling / rolling / cast-time-points flags     C, D
DurationPolicy     authored duration family                                 B
StackPolicy        reapplication stack family                               C
ChargePolicy       which counter a proc/dispel decrements                   C
PeriodicPolicy     haste mode x extra-initial x authored tail              D
RefreshPolicy      reapplication branch x periodic timer x carry            B
RemovalPolicy      death class x interrupt-driven x dispellable x cancel    E
ExternalPolicy     externally touched lifecycle surfaces                    H
=================  =====================================================  =========

Two things are computed over the axes:

* ``population`` -- distinct axis tuples per population (the size of the
  model's state space on real data) and per-axis marginals;
* ``redundancy`` -- ordered axis pairs where one axis's value determines the
  other's on a population exactly or up to 1% of its spells (a candidate
  merge), with the violation count (R1: near-determination matters as much as
  exact determination).

The cross-track rule list (``LEAD_RULES``) is evaluated against the per-spell
axes: every rule reports its population, its counterexamples (spell ids) and a
status.  Rules that need a spell list are rejected by construction: every
predicate reads axis values only.

Membership in a tuple never implies executable support anywhere.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any, Callable

from . import FailClosed

AXES = ("AuraIdentity", "ApplicationPolicy", "AmountPolicy", "DurationPolicy", "StackPolicy", "ChargePolicy",
        "PeriodicPolicy", "RefreshPolicy", "RemovalPolicy", "ExternalPolicy")


class _Tools:
    """Track classifiers bound to one context (built once)."""

    def __init__(self, ctx) -> None:
        from . import charges, duration, identity, overlays, stacks
        self.ctx = ctx
        self.pb = identity.PropsBuilder(ctx)
        self.groups = identity.SpellGroups.from_overlay(ctx)
        self.empower = duration.empower_spells(ctx.bundle.source)
        self.surfaces = overlays.surface_flags(ctx)
        self.charges = charges
        self.stacks = stacks


@lru_cache(maxsize=1)
def _tools(ctx) -> _Tools:
    return _Tools(ctx)


def _identity(t: _Tools, spell: int) -> str:
    from . import identity
    sig = identity.signature(t.pb(spell), t.groups)
    parts = []
    if "lookup" in sig:
        parts.append(f"{sig['lookup']}/{sig['other_caster']}")
    if "dynobj" in sig:
        parts.append("dynobj-per-cast")
    return "+".join(parts) or "no-owned-aura"


def _application(t: _Tools, spell: int, passive: bool, channeled: bool) -> str:
    from .recipients import effect_pipeline
    pipes = sorted({p for e in t.ctx.data.effects(spell)
                    if (p := effect_pipeline(e["Effect"], e["EffectAura"])) is not None})
    return ("passive" if passive else "channel" if channeled else "active") + ":" + ("+".join(pipes) or "none")


#: AuraEffect::CalculateAmount aura-type cases (SpellAuraEffects.cpp:785-826).
CC_AURAS = frozenset({5, 7, 12, 26, 56, 455})   # CONFUSE FEAR STUN ROOT TRANSFORM ROOT_2
SCHOOL_ABSORB, MANA_SHIELD = 69, 97


def _amount(t: _Tools, spell: int, facts: dict[str, Any], rolling: bool) -> str:
    """Amount timing class of the spell's aura effects.

    Mirrors: SpellAuraEffects.cpp:785-826 -- crowd-control types set ``m_canBeRecalculated = false`` and,
    with ProcFlags, snapshot 10% of the holder's max health; SCHOOL_ABSORB snapshots caster done + holder
    taken bonuses at creation; MANA_SHIELD is not recalculated.  (AL-D-C-05: SetStackAmount recalculates
    anyway.)  Periodic families read done/taken bonuses at each tick (track D).
    """
    flags = []
    auras = {int(e["aura"]) for e in facts["effects"] if e["aura"]}
    if auras & CC_AURAS:
        info = t.ctx.catalog.get(spell)
        flags.append("cc-10pct-maxhp-snapshot" if info is not None and info.proc_flags else "cc-no-recalc")
    if SCHOOL_ABSORB in auras:
        flags.append("absorb-bonuses-snapshot")
    if MANA_SHIELD in auras:
        flags.append("mana-shield-no-recalc")
    effects = facts["effects"]
    aura_effects = [e for e in effects if e["aura"]]
    if any(e["suppress_points_stacking"] for e in aura_effects):
        flags.append("suppress-points-stacking" if all(e["suppress_points_stacking"] for e in aura_effects)
                     else "mixed-points-stacking")
    if any(e["aura_points_stack"] for e in aura_effects):
        flags.append("aura-points-stack")
    if rolling:
        flags.append("rolling-periodic")
    if any(int(e.get("EffectAttributes") or 0) & 0x8000 for e in t.ctx.data.effects(spell) if e["EffectAura"]):
        flags.append("compute-points-at-cast(ignored-by-trinity)")
    return "+".join(flags) or "base-captured/bonuses-live"


def _periodic(t: _Tools, spell: int) -> str:
    from .periodic import profile
    p = profile(t.ctx, spell)
    ticking = [e for e in p["effects"] if e.get("trinity_periodic")]
    if not ticking:
        return "none"
    modes = sorted({e["haste_mode"] for e in ticking})
    extra = any(e["extra_initial_period"] for e in ticking)
    tail = any(e.get("uncovered_tail_ms") for e in ticking)
    perm = all(e.get("permanent") for e in ticking)
    return ("permanent" if perm else "finite") + f":haste={'|'.join(modes)}" + (":tick-on-apply" if extra else "") \
        + (":uncovered-tail" if tail else "")


def _removal(t: _Tools, spell: int) -> str:
    from .removal import spell_removal_facts
    f = spell_removal_facts(t.ctx, spell)
    parts = [f["death_class"]]
    if f["aura_interrupt_flags"] or f["aura_interrupt_flags2"]:
        parts.append("interruptible")
    if f["dispel_type"]:
        parts.append("dispellable" if f["dispel_type"] != 11 else "dispel-type-11")
    if f["no_aura_cancel"]:
        parts.append("no-cancel")
    if f["shape_loss_candidate"]:
        parts.append("form-bound")
    if f["equipped_item_requirement"]:
        parts.append("item-requirement")
    return "+".join(parts)


def axes(ctx, spell: int) -> dict[str, str]:
    """Every axis value of one provider spell; raises :class:`FailClosed` if any owner does."""
    from .duration import facts, family
    from .refresh import classify
    t = _tools(ctx)
    df = facts(ctx.data, spell, t.empower)
    rc = classify(df)
    sf = t.stacks.stack_facts(ctx, spell)
    cf = t.charges.charge_facts(ctx, spell)
    ext = t.surfaces.get(spell, {})
    return {
        "AuraIdentity": _identity(t, spell),
        "ApplicationPolicy": _application(t, spell, sf["passive"], sf["channeled"]),
        "AmountPolicy": _amount(t, spell, sf, rc["rolling_periodic"]),
        "DurationPolicy": family(df),
        "StackPolicy": t.stacks.stack_family(sf),
        "ChargePolicy": t.charges.charge_family(cf),
        "PeriodicPolicy": _periodic(t, spell),
        "RefreshPolicy": f"{rc['branch']}|timer={rc['periodic_timer']}|carry={rc['carry']}",
        "RemovalPolicy": _removal(t, spell),
        "ExternalPolicy": "+".join(f"{s}:{p}" for s, p in sorted(ext.items())) or "none",
    }


# ---------------------------------------------------------------------------
# Cross-track candidate rules, stated over axis values only
# ---------------------------------------------------------------------------

Rule = tuple[str, str, Callable[[dict[str, str]], bool], Callable[[dict[str, str]], bool]]


def _has(axis: str, token: str) -> Callable[[dict[str, str]], bool]:
    return lambda a: token in a[axis]


#: (id, statement, applies-to predicate, claim predicate).  A counterexample is a
#: spell the rule applies to where the claim is false.
LEAD_RULES: tuple[Rule, ...] = (
    ("AL-R-X-01", "Reapplying the same SpellId by the same caster refreshes the existing aura",
     lambda a: a["AuraIdentity"] != "no-owned-aura" and "dynobj" not in a["AuraIdentity"],
     lambda a: a["RefreshPolicy"].startswith(("refresh", "stack-and-refresh"))),
    ("AL-R-X-02", "Passive auras have no refresh path (every application builds a new object)",
     lambda a: a["ApplicationPolicy"].startswith("passive"),
     lambda a: a["RefreshPolicy"].startswith("new-object")),
    ("AL-R-X-03", "Passive auras carry no mutable stack/charge/periodic state",
     lambda a: a["ApplicationPolicy"].startswith("passive"),
     lambda a: a["StackPolicy"] == "multislot-no-reapply-path" and a["ChargePolicy"] == "no-charges"
     and a["PeriodicPolicy"] == "none"),
    ("AL-R-X-04", "Passive auras are permanent",
     lambda a: a["ApplicationPolicy"].startswith("passive"),
     lambda a: a["DurationPolicy"] in ("no-duration-index-passive", "permanent-sentinel")),
    ("AL-R-X-05", "Death removes every non-passive aura of the holder",
     lambda a: not a["ApplicationPolicy"].startswith("passive"),
     lambda a: a["RemovalPolicy"].startswith("removed-death")),
    ("AL-R-X-06", "A refreshed periodic aura restarts its tick rhythm",
     lambda a: a["PeriodicPolicy"] != "none" and not a["RefreshPolicy"].startswith("new-object"),
     lambda a: "timer=reset" in a["RefreshPolicy"]),
    ("AL-R-X-07", "Stack capacity >= 2 means the aura stacks per caster",
     lambda a: a["StackPolicy"].startswith("stacking"),
     lambda a: a["StackPolicy"] == "stacking-per-caster"),
    ("AL-R-X-08", "Aura lifecycle is fully decided by DB2 (no external surface)",
     lambda a: True,
     lambda a: a["ExternalPolicy"] == "none"),
    ("AL-R-X-09", "Charges and stacks are the same counter",
     lambda a: a["ChargePolicy"] != "no-charges",
     lambda a: a["ChargePolicy"].startswith("stacks-as-charges")),
    ("AL-R-X-10", "Amount = base captured at application x stacks, bonuses read live",
     lambda a: True,
     lambda a: a["AmountPolicy"] == "base-captured/bonuses-live"),
    ("AL-R-X-11", "Pandemic carryover is computed from the remaining duration (finite auras only: "
     "Spell.cpp:3268 skips the carry for AuraDuration <= 0)",
     lambda a: "carry=pandemic" in a["RefreshPolicy"] and a["DurationPolicy"] not in (
         "permanent-sentinel", "no-duration-index-passive", "no-duration-index-active", "zero"),
     lambda a: "carry=pandemic-reads-live-remaining" in a["RefreshPolicy"]),
    ("AL-R-X-13", "Reapplying the same SpellId by the same caster refreshes the existing aura (non-passive only)",
     lambda a: not a["ApplicationPolicy"].startswith("passive") and a["AuraIdentity"] != "no-owned-aura"
     and "dynobj" not in a["AuraIdentity"],
     lambda a: a["RefreshPolicy"].startswith(("refresh", "stack-and-refresh"))),
    ("AL-R-X-12", "A periodic aura's authored duration is a whole number of periods",
     lambda a: a["PeriodicPolicy"].startswith("finite"),
     lambda a: "uncovered-tail" not in a["PeriodicPolicy"]),
)


#: Rules whose claim is fixed by the classifier that computes the axis (a code path, not
#: a data regularity).  The census cannot falsify them; they are labelled
#: ``source-backed`` and their evidence is the cited consumer code (R1-04, R1 "unfalsifiable").
SOURCE_BACKED = {
    "AL-R-X-02": "refresh.classify maps every multislot spell to new-object (Unit.cpp:3393, SpellInfo.cpp:1803)",
    "AL-R-X-11": "refresh.classify fixes carry=pandemic-reads-refreshed-duration for every timer-refreshing branch "
                 "(Spell.cpp:3284-3288 after SpellAuras.cpp:1119-1121); the probe (AL-D-D-02) is the evidence",
}


def evaluate(rows: dict[int, dict[str, str]], populations: dict[str, frozenset[int]],
             skewed: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    out = []
    for rid, statement, applies, claim in LEAD_RULES:
        per_pop = {}
        for pop, members in sorted(populations.items()):
            sub = [s for s in sorted(members) if s in rows and applies(rows[s])]
            bad = [s for s in sub if not claim(rows[s])]
            per_pop[pop] = {"applies": len(sub), "counterexamples": len(bad), "witnesses": bad[:8],
                            "build_skew_applies": sum(1 for x in sub if x in skewed),
                            "build_skew_counterexamples": sum(1 for x in bad if x in skewed)}
        falsified = any(v["counterexamples"] for v in per_pop.values())
        player_falsified = any(per_pop.get(p, {}).get("counterexamples", 0)
                               for p in ("player", "player+class-skills"))
        row = {"id": rid, "statement": statement, "per_population": per_pop,
               "status": "discarded" if player_falsified else "refined" if falsified else "holds-on-census"}
        if rid in SOURCE_BACKED:
            row["census_status"] = row["status"]
            row["status"] = "source-backed"
            row["note"] = SOURCE_BACKED[rid]
        if row["status"] == "refined":
            row["note"] = "holds on the player populations, falsified on all"
        out.append(row)
    return out


def redundancy(rows: dict[int, dict[str, str]], members: frozenset[int]) -> list[dict[str, Any]]:
    """For each ordered axis pair (X, Y): does X's value determine Y's on this population?"""
    sub = [rows[s] for s in sorted(members) if s in rows]
    out = []
    for x in AXES:
        for y in AXES:
            if x == y:
                continue
            seen: dict[str, Counter] = defaultdict(Counter)
            for r in sub:
                seen[r[x]][r[y]] += 1
            violations = sum(sum(c.values()) - max(c.values()) for c in seen.values())
            if violations <= max(1, len(sub) // 100):
                out.append({"determines": x, "determined": y, "values": len(seen), "violations": violations,
                            "exact": violations == 0})
    return out


def census(ctx) -> dict[str, Any]:
    from .providers import populations
    pops = populations(ctx, with_class_skills=True)
    rows: dict[int, dict[str, str]] = {}
    failed: dict[int, str] = {}
    for spell in sorted(pops["all"]):
        try:
            rows[spell] = axes(ctx, spell)
        except FailClosed as exc:
            failed[spell] = str(exc)
    per_pop: dict[str, Any] = {}
    for pop, members in sorted(pops.items()):
        sub = {s: rows[s] for s in sorted(members) if s in rows}
        tuples = Counter(tuple(r[a] for a in AXES) for r in sub.values())
        marg = {a: dict(sorted(Counter(r[a] for r in sub.values()).items())) for a in AXES}
        per_pop[pop] = {
            "spells": len(members), "evaluated": len(sub), "distinct_tuples": len(tuples),
            "singleton_tuples": sum(1 for c in tuples.values() if c == 1),
            "top_tuples": [{"count": c, **dict(zip(AXES, t))}
                           for t, c in sorted(tuples.items(), key=lambda kv: (-kv[1], kv[0]))[:15]],
            "marginals": marg,
            "distinct_per_axis": {a: len(marg[a]) for a in AXES},
        }
    return {
        "axes": list(AXES),
        "populations": per_pop,
        "rules": evaluate(rows, pops, frozenset(s for s in pops["all"] if ctx.is_skew(s))),
        "redundancy": {pop: redundancy(rows, pops[pop]) for pop in ("player", "player+class-skills", "controlled")},
        "failed": {str(k): v for k, v in sorted(failed.items())},
        "player_rows": {str(s): rows[s] for s in sorted(pops["player+class-skills"] | pops["controlled"]) if s in rows},
    }
