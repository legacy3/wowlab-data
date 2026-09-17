"""Target caps: every max-target policy source and the cap stage (Track C).

Runtime order per selection category (all Spell.cpp, pinned):

=========  ==========================================================================  =========
category   order                                                                       cap rule
=========  ==========================================================================  =========
AREA       search -> [UNIT_AND_DEST ModDst] -> OnObjectAreaTargetSelect -> [FURTHEST     RandomResize; FURTHEST: truncate
           sort desc] -> cap -> AddUnitTarget(checkIfValid=false, losPosition=center)  (1439-1451)
CONE       search -> hook -> cap -> AddUnitTarget(checkIfValid=false)                  RandomResize (1310-1311)
LINE       search -> hook -> cap -> AddUnitTarget(checkIfValid=false)                  if max < size: sort asc by
                                                                                       caster dist, truncate (2003-2010)
NEARBY     single nearest (no cap)                                                     --
CHAIN      ChainTargets (Track D), not MaxAffectedTargets                              --
TRAJ       moves the destination only (no AddUnitTarget)                               --
=========  ==========================================================================  =========

Cap value: ``SpellValue::MaxAffectedTargets`` -- one value per *spell cast*
(Spell.cpp:448 ``= SpellInfo::MaxAffectedTargets`` = SpellTargetRestrictions.MaxTargets
(SpellInfo.cpp:1509); ``SpellModOp::MaxTargets`` applied once in the ``Spell`` ctor
(Spell.cpp:507, uint32 ``T((double(base)+flat)*pct)``, Player.cpp:22851);
``SPELLVALUE_MAX_TARGETS`` *replaces* it afterwards (Spell.cpp:8738)).  The same
value caps every selector call of every effect group independently: it is not
shared/decremented across TargetA/TargetB or across effect groups, and one
RandomResize draw set serves all effects of a group (the effect-mask grouping at
Spell.cpp:741-784 passes one mask to one selection).  0 = uncapped.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from . import TC_ROOT, FailClosed
from . import geometry as g
from .trace import Trace

SPELLMOD_MAX_TARGETS = 40   # SpellDefines.h:194 SpellModOp::MaxTargets
SPELLMOD_RADIUS = 6         # SpellDefines.h:158 SpellModOp::Radius
SPELLMOD_CHAIN_TARGETS = 17
ADD_MOD_AURAS = {107: "flat", 108: "pct", 219: "label_flat", 220: "label_pct"}  # SpellAuraDefines.h


# ---------------------------------------------------------------------------
# libstdc++ std::list::sort (merge sort) -- tie behaviour depends on it
# ---------------------------------------------------------------------------
def _merge(this: list, other: list, less) -> list:
    """libstdc++ ``list::merge(x, comp)``: take from ``x`` when ``comp(*x, *this)``."""
    out: list = []
    i = j = 0
    while i < len(this) and j < len(other):
        if less(other[j], this[i]):
            out.append(other[j])
            j += 1
        else:
            out.append(this[i])
            i += 1
    out.extend(this[i:])
    out.extend(other[j:])
    return out


def list_sort(items: list, less) -> list:
    """libstdc++ ``std::list<T>::sort(Comp)`` (bits/list.tcc bottom-up merge with 64 buckets).

    Reproduced exactly because Trinity passes a *non-strict* comparator
    (``ObjectDistanceOrderPred(ref, false)`` is ``!(d1 < d2)``) for which tie
    order is implementation-defined; verified against the compiled probe.
    """
    src = list(items)
    if len(src) <= 1:
        return src
    tmp: list[list] = [[] for _ in range(64)]
    fill = 0
    while src:
        carry = [src.pop(0)]
        counter = 0
        while counter != fill and tmp[counter]:
            tmp[counter] = _merge(tmp[counter], carry, less)
            carry, tmp[counter] = tmp[counter], []
            counter += 1
        carry, tmp[counter] = tmp[counter], carry
        if counter == fill:
            fill += 1
    for counter in range(1, fill):
        tmp[counter] = _merge(tmp[counter], tmp[counter - 1], less)
        tmp[counter - 1] = []
    return tmp[fill - 1]


def sort_by_distance(world, targets: list[str], ref_id: str, ascending: bool, trace: Trace | None,
                     mirrors: str) -> list[str]:
    """``targets.sort(Trinity::ObjectDistanceOrderPred(ref, ascending))``.

    Mirrors: Object.h:638 ``ObjectDistanceOrderPred`` (``GetDistanceOrder(l, r) == ascending``,
    3D squared float distances, Object.cpp:569) sorted by ``std::list::sort``.
    Descending uses ``!(d(l) < d(r))`` -- equal distances compare "less" both ways
    (not a strict weak ordering; reproduced, flagged TG-C-D03).
    """
    ref = g.vec(world.actor(ref_id).need("pos"))
    pos = {t: g.vec(world.actor(t).need("pos")) for t in targets}

    def less(a: str, b: str) -> bool:
        return g.distance_order(ref, pos[a], pos[b]) == ascending

    out = list_sort(targets, less)
    if trace is not None:
        # decisive only when the order differs from a strict (stable) sort of the same keys
        strict = list_sort(targets, (lambda a, b: g.distance_order(ref, pos[a], pos[b])) if ascending
                           else (lambda a, b: g.distance_order(ref, pos[b], pos[a])))
        trace.add("caps.sort", mirrors, output=out, inputs={"ref": ref_id, "ascending": ascending},
                  notes=[f"strict-comparator order would be {strict}"] if strict != out else [],
                  defect="TG-C-D03: non-strict comparator reorders equal distances" if strict != out else None)
    return out


# ---------------------------------------------------------------------------
# the cap value
# ---------------------------------------------------------------------------
def effective_max_targets(world, sv, trace: Trace | None = None) -> int:
    """``m_spellValue->MaxAffectedTargets`` at selection time.

    Mirrors: Spell.cpp:448 (base), Spell.cpp:507 (SpellModOp::MaxTargets through the
    ``caster->GetSpellModOwner()`` of the *constructor* caster), Spell.cpp:8738
    (``SPELLVALUE_MAX_TARGETS`` override).  The fixture may state the final value as
    ``spell_value.max_affected_targets`` (e.g. a script-cast override); otherwise
    base + ``modifiers.max_targets`` are combined.
    """
    if "max_affected_targets" in world.spell_value:
        value = int(world.spell_value["max_affected_targets"])
        src = "spell_value (stated)"
    else:
        from .oracle import apply_spell_mod
        base = int(sv.max_affected_targets)
        value = int(apply_spell_mod(world, world.caster, "max_targets", base, is_float=False))
        src = "SpellInfo::MaxAffectedTargets + SpellModOp::MaxTargets"
    if value < 0 or value > 0xFFFFFFFF:
        raise FailClosed(f"caps: MaxAffectedTargets {value} outside uint32 (double->uint32 conversion is UB)")
    if trace is not None:
        trace.add("caps.value", "Spell.cpp:448,507,8738", output=value, inputs={"source": src})
    return value


# ---------------------------------------------------------------------------
# RandomResize
# ---------------------------------------------------------------------------
def random_resize(world, items: list, n: int, trace: Trace | None) -> list:
    """Mirrors: Containers.h:67 ``RandomResize`` (implementation: ``targeting.rng.random_resize``)."""
    from .rng import random_resize as d_random_resize
    return d_random_resize(world, items, n, trace)


# ---------------------------------------------------------------------------
# the cap stage
# ---------------------------------------------------------------------------
def apply(world, spell_view, eff, sel, targets: list[str], trace: Trace | None) -> list[str]:
    """Apply ``MaxAffectedTargets`` for one selector call.

    Mirrors: Spell.cpp:1308-1311 (cone), 1443-1451 (area), 2001-2010 (line).
    ``targets`` must already be in post-hook (and, for FURTHEST, post-sort) order.
    Empty lists skip the stage entirely (``if (!targets.empty())``).
    """
    from . import selectors
    if isinstance(sel, int):
        sel = selectors.info(sel)
    if not targets:
        return []
    cat = sel.category
    if cat not in ("AREA", "CONE", "LINE"):
        raise FailClosed(f"caps: selection category {cat} has no MaxAffectedTargets stage")
    n = effective_max_targets(world, spell_view, trace)
    if not n:
        return list(targets)
    if cat == "AREA" and sel.id == 115:  # TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY
        out = list(targets[:n])
        rule = "truncate (already sorted far->near)"
        mirrors = "Spell.cpp:1449-1450"
    elif cat == "LINE":
        if n < len(targets):
            out = sort_by_distance(world, targets, world.caster, True, trace, "Spell.cpp:2007")[:n]
            rule = "sort near->far from caster, truncate"
        else:
            out = list(targets)
            rule = "max >= size: unchanged (no sort)"
        mirrors = "Spell.cpp:2003-2010"
    else:
        out = random_resize(world, targets, n, trace)
        rule = "RandomResize"
        mirrors = "Spell.cpp:1311" if cat == "CONE" else "Spell.cpp:1448"
    if trace is not None:
        trace.add("caps.apply", mirrors, output=out, inputs={"max": n, "size": len(targets), "rule": rule})
    return out


# ---------------------------------------------------------------------------
# census
# ---------------------------------------------------------------------------
def _script_cap_sites() -> list[dict[str, Any]]:
    """Structural scan of Trinity spell/pet scripts for cap writers/readers (script-consumer, lines only)."""
    out = []
    root = TC_ROOT / "src/server/scripts"
    pat = re.compile(r"SPELLVALUE_MAX_TARGETS|MaxAffectedTargets|RandomResize|SelectRandomInjuredTargets|"
                     r"resize\(|SelectRandomContainerElement")
    for sub in ("Spells", "Pet"):
        for path in sorted((root / sub).glob("*.cpp")):
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                m = pat.search(line)
                if m:
                    out.append({"file_line": f"{path.relative_to(TC_ROOT)}:{n}", "token": m.group(0),
                                "text": line.strip()[:160]})
    return out


def sqrt_target_limits() -> list[dict[str, Any]]:
    """``SpellMgr::LoadSpellInfoTargetCaps`` blocks (SpellMgr.cpp:5407): damage/heal sqrt diminishing inputs.

    Not a recipient cap (payload dependency edge); listed so the census can show
    where it disagrees with the selection cap.
    """
    text = (TC_ROOT / "src/server/game/Spells/SpellMgr.cpp").read_text(encoding="utf-8")
    start = text.index("void SpellMgr::LoadSpellInfoTargetCaps()")
    end = text.index("\n}\n", start)
    out = []
    for m in re.finditer(r"ApplySpellFix\(\{([^}]*)\},[^;]*?_LoadSqrtTargetLimit\(([^;]*)\);", text[start:end], re.S):
        line = text.count("\n", 0, start + m.start()) + 1
        ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
        args = [a.strip() for a in m.group(2).split(",")]
        out.append({"line": line, "spells": ids, "max_targets": int(args[0]),
                    "num_non_diminished": int(args[1]), "args": args})
    return out


def spellmod_sources(ctx, ops: tuple[int, ...] = (SPELLMOD_MAX_TARGETS,)) -> list[dict[str, Any]]:
    """Aura effects in the current-player reach that add a spell modifier of ``ops``.

    Mirrors (loading): SpellAuraEffects.cpp:1030-1060 ``HandleAddModifier``
    (misc0 = SpellModOp, misc1 = label for *_BY_SPELL_LABEL).
    """
    reach = ctx.scope.reach
    out = []
    for sid in sorted(reach):
        info = ctx.catalog.get(sid)
        if info is None:
            continue
        for e in info.effects:
            if e.is_aura and e.aura in ADD_MOD_AURAS and e.misc0 in ops:
                out.append({"spell": sid, "effect": e.index, "aura": ADD_MOD_AURAS[e.aura], "op": e.misc0,
                            "value": e.base_points, "family": info.family, "class_mask": e.class_mask,
                            "label": e.misc1 if e.aura in (219, 220) else None})
    return out


def spellmod_affects(ctx, src: dict[str, Any], target_spell: int) -> int:
    """Mirrors: SpellInfo.cpp:1989 ``IsAffectedBySpellMod`` (count of matched family-flag bits / label hit)
    and SpellInfo.cpp:1984 (SPELL_ATTR3_IGNORE_CASTER_MODIFIERS)."""
    from procs.enums import attr
    info = ctx.catalog.get(target_spell)
    if info is None or info.has_attr(attr("SPELL_ATTR3_IGNORE_CASTER_MODIFIERS")):
        return 0
    if src["label"] is not None:
        return 1 if src["label"] in info.labels else 0
    if info.family != src["family"]:
        return 0
    return bin(src["class_mask"] & info.family_flags).count("1")


def _corrected_cap(corr, spell: int, db2: int) -> int | str:
    for fx in corr.by_spell.get(spell, []):
        for w in fx["writes"]:
            if w["member"] == "MaxAffectedTargets":
                return w["value"]
    return db2


def census(ctx, effect_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cap-source census over current-player effects (rows from ``cmd_c.scope_effects``)."""
    from dummy_semantics.corrections import Corrections
    d = ctx.data
    reach = ctx.scope.reach
    capped_spells = {}
    for sid in sorted(reach):
        r = d.restrictions(sid)
        if r and int(r["MaxTargets"]):
            capped_spells[sid] = int(r["MaxTargets"])
    corr = Corrections(ctx.bundle)
    corr_rows = []
    for fx in corr.fixes:
        for w in fx["writes"]:
            if w["member"] in ("MaxAffectedTargets", "ConeAngle", "Width", "RadiusEntry", "TargetARadiusEntry",
                               "TargetBRadiusEntry", "ChainTargets", "TargetA", "TargetB"):
                ids = [i for i in fx["ids"] if i in reach]
                if ids:
                    corr_rows.append({"line": fx["line"], "member": w["member"], "value": w["value"],
                                      "spells": ids, "build_skew": [i for i in ids if ctx.is_skew(i)]})
    mods = spellmod_sources(ctx, (SPELLMOD_MAX_TARGETS, SPELLMOD_RADIUS))
    selector_spells = sorted({r["spell"] for r in effect_rows if r["category"] in ("AREA", "CONE", "LINE")})
    mod_edges = []
    for m in mods:
        hits = [s for s in selector_spells if spellmod_affects(ctx, m, s)]
        if m["op"] == SPELLMOD_MAX_TARGETS:
            for s in hits:
                mod_edges.append({"mod_spell": m["spell"], "mod_effect": m["effect"], "target_spell": s,
                                  "value": m["value"], "kind": m["aura"],
                                  "creates_cap": capped_spells.get(s, 0) == 0,
                                  "multiplicity": spellmod_affects(ctx, m, s)})
        m["affects_selector_spells"] = len(hits)
    by_cat = Counter()
    by_cat_capped = Counter()
    grouped_shared = 0
    for r in effect_rows:
        by_cat[r["category"]] += 1
        if r["spell"] in capped_spells:
            by_cat_capped[r["category"]] += 1
        if r.get("group_size", 1) > 1 and r["spell"] in capped_spells and r.get("group_leader"):
            grouped_shared += 1
    per_spec: dict[int, int] = defaultdict(int)
    for spec, spells in ctx.scope.specs_reach.items():
        per_spec[spec] = sum(1 for s in spells if s in capped_spells and s in set(selector_spells))
    return {
        "db2_max_targets": {"spells": len(capped_spells),
                            "on_selector_spells": sum(1 for s in selector_spells if s in capped_spells),
                            "values": dict(sorted(Counter(capped_spells.values()).items())),
                            "capped_without_capable_selector": sum(1 for s in capped_spells
                                                                   if s not in set(selector_spells))},
        "effects_by_category": dict(sorted(by_cat.items())),
        "capped_effects_by_category": dict(sorted(by_cat_capped.items())),
        "grouped_effect_sets_sharing_one_cap_draw": grouped_shared,
        "corrections": sorted(corr_rows, key=lambda r: (r["line"], r["member"])),
        "spellmod_sources": sorted(mods, key=lambda m: (m["op"], m["spell"], m["effect"])),
        "spellmod_max_target_edges": sorted(mod_edges, key=lambda e: (e["target_spell"], e["mod_spell"])),
        "script_sites": _script_cap_sites(),
        "sqrt_target_limits_in_scope": [
            {**b, "spells": [x for x in b["spells"] if x in reach],
             "selection_cap": {str(x): capped_spells.get(x, 0) for x in b["spells"] if x in reach},
             "selection_cap_after_corrections": {str(x): _corrected_cap(corr, x, capped_spells.get(x, 0))
                                                 for x in b["spells"] if x in reach}}
            for b in sqrt_target_limits() if any(x in reach for x in b["spells"])],
        "per_spec_capped_selector_spells": dict(sorted(per_spec.items())),
        "capped_spells": {str(k): v for k, v in sorted(capped_spells.items())},
    }
