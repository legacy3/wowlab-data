"""TargetA / TargetB composition (Track A).

How the two implicit selectors of one effect interact in the pinned consumer:

* ``Spell::SelectSpellTargets`` (Spell.cpp:720) calls ``SelectEffectImplicitTargets``
  for TargetA and then TargetB **with the same SpellEffectInfo (the first effect of
  the group) and the same grouped effect mask** (Spell.cpp:787-788).  There is no
  intersection step: every selector that finds recipients calls ``AddUnitTarget``
  / ``AddGOTarget`` / ``AddCorpseTarget``, which *union* into the spell-wide unique
  target lists (Spell.cpp:2466-2471 ORs effect bits into an existing entry).  B
  therefore never refines (filters) A's recipients.
* The only coupling is the spell-wide ``m_targets`` (SpellCastTargets) state:
  DEFAULT-category DEST/SRC selectors ``SetDst``/``SetSrc`` it; DEST-referenced
  area/line selectors and ``SelectImplicitDestDestTargets`` read it; TRAJ and
  DEST_DEST call ``CheckDst()`` (caster fallback, Spell.cpp:7268) and ``ModDst``;
  ``UNIT_AND_DEST`` area selectors ``ModDst`` to the referer (Spell.cpp:1434,
  ``ModDst`` ASSERTs ``HasDst`` at Spell.cpp:373/379).  ``TARGET_REFERENCE_TYPE_LAST``
  reads the last unique target carrying this effect's bit -- i.e. A's (or an
  earlier group's) last recipient.
* ``m_targets`` is not reset between effects, so B of effect *k* can read a
  destination written by A/B of effect *j < k*; ``AddDestTarget`` (Spell.cpp:800)
  snapshots the current dst for every effect after its selectors ran.
* ``SpellInfo::_InitializeExplicitTargetMask`` (SpellInfo.cpp:4572) folds A then B
  of every effect in order with *shared* ``srcSet``/``dstSet`` flags, so an earlier
  DEST/SRC-producing selector suppresses the explicit DEST/SRC request of a later
  DEST/SRC-referenced one.  ``InitExplicitTargets`` (Spell.cpp:621) then keeps /
  synthesises / removes the client dst and src according to that mask.

This module gives (1) a mirror of the explicit-mask construction (differentially
tested against the probe), (2) a *symbolic* model of the ``m_targets``
read/write sequence used by the discriminating composition tests, and (3) the
pair classification written to ``composition.json``.  It is not the recipient
oracle (Track G); geometry, checks and caps are other tracks' stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import FailClosed
from .selectors import (TARGET_FLAG_CORPSE_MASK, TARGET_FLAG_GAMEOBJECT_MASK, TARGET_FLAG_UNIT_MASK, TF,
                        effect_implicit_target_type, effect_used_object, handler, info, target_flag_mask)

SPELL_ATTR13_DO_NOT_FAIL_IF_NO_TARGET = 0x00008000       # SharedDefines.h:933
DONT_FAIL_SPELL_ON_TARGETING_FAILURE = 0x00100000         # DBCEnums.h:2423
PLAYERS_ONLY = 0x00004000                                 # DBCEnums.h:2417


@dataclass(frozen=True)
class EffectSlots:
    """The effect members the composition stages read."""
    index: int
    effect: int
    target_a: int
    target_b: int
    attributes: int = 0

    @property
    def is_effect(self) -> bool:
        """Mirrors: SpellInfo.cpp:455 ``IsEffect``."""
        return self.effect != 0


def provided_target_mask(e: EffectSlots) -> int:
    """Mirrors: SpellInfo.cpp:830 ``GetProvidedTargetMask``."""
    return target_flag_mask(info(e.target_a).object) | target_flag_mask(info(e.target_b).object)


def missing_target_mask(e: EffectSlots, src_set: bool = False, dst_set: bool = False, mask: int = 0) -> int:
    """Mirrors: SpellInfo.cpp:835 ``GetMissingTargetMask``."""
    eff = target_flag_mask(effect_used_object(e.effect))
    provided = provided_target_mask(e) | mask
    if provided & TARGET_FLAG_UNIT_MASK:
        eff &= ~TARGET_FLAG_UNIT_MASK
    if provided & TARGET_FLAG_CORPSE_MASK:
        eff &= ~(TARGET_FLAG_UNIT_MASK | TARGET_FLAG_CORPSE_MASK)
    if provided & TF["GAMEOBJECT_ITEM"]:
        eff &= ~(TF["GAMEOBJECT_ITEM"] | TF["GAMEOBJECT"] | TF["ITEM"])
    if provided & TF["GAMEOBJECT"]:
        eff &= ~(TF["GAMEOBJECT"] | TF["GAMEOBJECT_ITEM"])
    if provided & TF["ITEM"]:
        eff &= ~(TF["ITEM"] | TF["GAMEOBJECT_ITEM"])
    if dst_set or provided & TF["DEST_LOCATION"]:
        eff &= ~TF["DEST_LOCATION"]
    if src_set or provided & TF["SOURCE_LOCATION"]:
        eff &= ~TF["SOURCE_LOCATION"]
    return eff


def explicit_target_mask(effects: list[EffectSlots], *, max_range_negative: float, max_range_positive: float,
                         targets: int = 0, attributes13: int = 0) -> tuple[int, int]:
    """(ExplicitTargetMask, RequiredExplicitTargetMask).

    Mirrors: SpellInfo.cpp:4572 ``SpellInfo::_InitializeExplicitTargetMask``.
    ``max_range_*`` are ``GetMaxRange(false/true)`` without a caster
    (``RangeEntry->RangeMax[0/1]``, 0 when the spell has no range row; SpellInfo.cpp:3949).
    ``targets`` is ``SpellTargetRestrictions.Targets``.  Effects must be in effect-index order.
    """
    src_set = dst_set = False
    explicit = required = 0
    for e in effects:
        if not e.is_effect:
            continue
        m, src_set, dst_set = info(e.target_a).explicit_target_mask(src_set, dst_set)
        mb, src_set, dst_set = info(e.target_b).explicit_target_mask(src_set, dst_set)
        m |= mb
        if effect_implicit_target_type(e.effect) == 1:  # EFFECT_IMPLICIT_TARGET_EXPLICIT
            eff_mask = missing_target_mask(e, src_set, dst_set, m)
            if max_range_positive == 0.0 and max_range_negative == 0.0:
                eff_mask &= ~(TARGET_FLAG_UNIT_MASK | TF["GAMEOBJECT"] | TARGET_FLAG_CORPSE_MASK | TF["DEST_LOCATION"])
            m |= eff_mask
        explicit |= m
        if not e.attributes & DONT_FAIL_SPELL_ON_TARGETING_FAILURE:
            required |= m
    explicit |= targets
    if not attributes13 & SPELL_ATTR13_DO_NOT_FAIL_IF_NO_TARGET:
        required |= targets
    return explicit & 0xFFFFFFFF, required & 0xFFFFFFFF


# -- symbolic m_targets model ---------------------------------------------------------

def reads_writes(target_id: int) -> dict[str, Any]:
    """What one selector reads from / writes to ``m_targets`` and whether it adds recipients.

    Derived from :func:`targeting.selectors.handler` (Spell.cpp:945-2023).
    """
    s = info(target_id)
    h = handler(target_id)
    reads: list[str] = []
    if target_id == 0 or s.category == "NYI":
        return {"reads": [], "writes": [], "adds": False}
    if s.category in ("AREA", "LINE"):
        reads.append({"SRC": "src", "DEST": "dst", "TARGET": "explicit-unit", "LAST": "last-recipient",
                      "CASTER": "caster"}[s.reference])
    elif s.category == "TRAJ":
        reads += ["src", "dst"]
    elif s.category == "DEFAULT":
        if s.reference == "DEST":
            reads.append("dst")
        elif s.reference == "TARGET":
            reads.append("explicit-object")
        else:
            reads.append("caster")
    elif s.category == "CHANNEL":
        reads.append("channel")
    else:
        reads.append("caster")
    writes = [w.split(" ")[0] for w in h["writes"]]
    return {"reads": reads, "writes": writes, "adds": bool(h["adds"])}


@dataclass
class SymbolicTargets:
    """``SpellCastTargets`` reduced to labels (Spell.cpp:178-400)."""
    unit: str | None = None
    dst: str | None = None
    src: str | None = None
    stale_dst: str | None = None  # RemoveDst only clears the flag; m_dst keeps its position (Spell.cpp:383)
    stale_src: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


def init_explicit_targets(explicit_mask: int, caster: str, client: dict[str, Any]) -> SymbolicTargets:
    """Mirrors: Spell.cpp:621 ``Spell::InitExplicitTargets`` (symbolic).

    ``client`` = {"unit": label|None, "unit_kind": "unit"|"gameobject"|"corpse", "dst": label|None,
    "src": label|None, "selection": label|None (player selection, assumed CheckExplicitTarget-valid)}.
    """
    t = SymbolicTargets(unit=client.get("unit"), dst=client.get("dst"), src=client.get("src"))
    kind = client.get("unit_kind", "unit")
    if t.unit is not None:
        if (kind == "unit" and not explicit_mask & (TARGET_FLAG_UNIT_MASK | TARGET_FLAG_CORPSE_MASK)) \
                or (kind == "gameobject" and not explicit_mask & TARGET_FLAG_GAMEOBJECT_MASK) \
                or (kind == "corpse" and not explicit_mask & TARGET_FLAG_CORPSE_MASK):
            t.trace.append({"stage": "InitExplicitTargets", "mirrors": "Spell.cpp:634-637", "removed_object": t.unit})
            t.unit = None
    elif explicit_mask & TARGET_FLAG_UNIT_MASK:
        if "selection" not in client:
            raise FailClosed("InitExplicitTargets: no object target and the mask needs a unit; state the player selection")
        unit = client["selection"]
        if unit is None and not explicit_mask & (TF["UNIT_ENEMY"] | TF["UNIT_DEAD"] | TF["UNIT_MINIPET"] | TF["UNIT_PASSENGER"]):
            unit = caster
        t.unit = unit
        t.trace.append({"stage": "InitExplicitTargets", "mirrors": "Spell.cpp:642-661", "unit": unit})
    if explicit_mask & TF["DEST_LOCATION"]:
        if t.dst is None:
            t.dst = f"pos({client['unit']})" if client.get("unit") is not None else f"pos({caster})"
            t.trace.append({"stage": "InitExplicitTargets", "mirrors": "Spell.cpp:666-677", "dst": t.dst})
    elif t.dst is not None:
        t.trace.append({"stage": "InitExplicitTargets", "mirrors": "Spell.cpp:680", "removed_dst": t.dst})
        t.stale_dst, t.dst = t.dst, None
    if explicit_mask & TF["SOURCE_LOCATION"]:
        if client.get("src") is None:
            t.src = f"pos({caster})"
    elif t.src is not None:
        t.stale_src, t.src = t.src, None
    return t


def _dest_label(target_id: int, t: SymbolicTargets, caster: str) -> str:
    s = info(target_id)
    if s.reference == "CASTER":
        base = f"pos({caster})"
        if target_id in (18, 62, 125):
            return base if target_id == 18 else f"ground({base})"
        return f"{handler(target_id)['branch'].split(':')[0]}@{base}/{s.direction}"
    if s.reference == "TARGET":
        if t.unit is None:
            raise FailClosed(f"selector {target_id}: SelectImplicitTargetDestTargets without an object target "
                             "(ASSERT unless DontFailSpellOnTargetingFailure; Spell.cpp:1655)")
        base = f"pos({t.unit})"
        return base if target_id in (53, 63, 132) else f"offset({base},{s.direction})"
    raise FailClosed(f"selector {target_id}: no symbolic dest rule")


def select_symbolic(target_id: int, t: SymbolicTargets, caster: str, effect_mask: int,
                    recipients: list[dict[str, Any]]) -> None:
    """One ``SelectEffectImplicitTargets`` call, symbolically.  Mirrors: Spell.cpp:945."""
    if target_id == 0:
        return
    s = info(target_id)
    h = handler(target_id)
    if h["abort"]:
        raise FailClosed(f"selector {target_id}: ABORT path {h['abort']}")
    step = {"selector": target_id, "function": h["function"], "mask": effect_mask}
    if s.category == "NYI":
        step["result"] = "nyi-no-op"
    elif s.category == "DEFAULT" and s.object == "SRC":
        t.src = f"pos({caster})"
        step["src"] = t.src
    elif s.category == "DEFAULT" and s.object == "DEST":
        if s.reference == "DEST":
            if t.dst is None:  # CheckDst, Spell.cpp:1696
                t.dst = f"pos({caster})"
                step["check_dst"] = t.dst
            if h["branch"].startswith("no-op"):
                step["dst"] = t.dst
            else:
                t.dst = f"moved({t.dst},{target_id})"
                step["dst"] = t.dst
        else:
            t.dst = _dest_label(target_id, t, caster)
            step["dst"] = t.dst
    elif s.category == "TRAJ":
        if t.dst is None:
            t.dst = f"pos({caster})"
        step["dst"] = f"clip({t.dst}) if HasTraj"
    elif s.category == "DEFAULT":  # object selectors
        if s.reference == "CASTER":
            who = caster if target_id == 1 else f"{h['branch']}"
        else:
            if t.unit is None:
                raise FailClosed(f"selector {target_id}: no explicit object target (Spell.cpp:1809)")
            who = t.unit
        recipients.append({"selector": target_id, "set": who, "mask": effect_mask,
                           "chain_from": who if h["chain"] else None})
        step["adds"] = who
    elif s.category in ("AREA", "LINE", "CONE", "NEARBY"):
        if s.category == "CONE" or s.category == "NEARBY":
            centre = f"pos({caster})"
        elif s.reference == "DEST":
            centre = t.dst if t.dst is not None else f"stale-dst({t.stale_dst or 'default SpellDestination'})"
        elif s.reference == "SRC":
            centre = t.src if t.src is not None else f"stale-src({t.stale_src or 'default SpellDestination'})"
        elif s.reference == "TARGET":
            if t.unit is None:
                step["result"] = "no explicit unit -> no targets (Spell.cpp:1359)"
                t.trace.append(step)
                return
            centre = f"pos({t.unit})"
        elif s.reference == "LAST":
            # m_UniqueTargetInfo is insertion ordered; the bit tested is the group's first effect index
            first_bit = effect_mask & -effect_mask
            last = next((r["set"] for r in reversed(recipients) if r["mask"] & first_bit), None)
            centre = f"pos({last})" if last else f"pos({caster})"
        else:
            centre = f"pos({caster})"
        if s.category == "NEARBY" and s.object == "DEST":
            t.dst = f"nearest[{s.check}]"
            step["dst"] = t.dst
        else:
            label = f"{s.category.lower()}[{s.check}]@{centre}"
            recipients.append({"selector": target_id, "set": label, "mask": effect_mask, "centre": centre})
            step["adds"] = label
        if s.object == "UNIT_AND_DEST":
            if t.dst is None:
                raise FailClosed(f"selector {target_id}: ModDst without HasDst -> ASSERT (Spell.cpp:1434, Spell.cpp:379)")
            t.dst = centre
            step["dst"] = t.dst
    elif s.category == "CHANNEL":
        if target_id == 76:
            t.dst = "channel-dst"
            step["dst"] = t.dst
        else:
            recipients.append({"selector": target_id, "set": "channel-objects", "mask": effect_mask})
    t.trace.append(step)


def simulate(effects: list[EffectSlots], caster: str, client: dict[str, Any], explicit_mask: int,
             group: bool = True) -> dict[str, Any]:
    """Symbolic ``SelectSpellTargets`` over A/B of every effect.

    Grouping (Spell.cpp:741-790) is reduced to "same (A, B) pair"; callers asserting
    conditions / PlayersOnly / radius / script differences must pass ``group=False``
    (Track F owns the full grouping).  Mirrors: Spell.cpp:720-800.
    """
    t = init_explicit_targets(explicit_mask, caster, client)
    recipients: list[dict[str, Any]] = []
    dests: dict[int, str | None] = {}
    processed = 0
    for e in effects:
        if not e.is_effect:
            continue
        mask = 1 << e.index
        if group:
            for j in effects:
                if j.index > e.index and j.is_effect and (j.target_a, j.target_b) == (e.target_a, e.target_b):
                    mask |= 1 << j.index
        mask &= ~processed
        if mask:
            select_symbolic(e.target_a, t, caster, mask, recipients)
            select_symbolic(e.target_b, t, caster, mask, recipients)
            processed |= mask
        dests[e.index] = t.dst  # AddDestTarget, Spell.cpp:799-800
    return {"recipient_sets": recipients, "dests": dests, "final_dst": t.dst, "final_src": t.src, "trace": t.trace}


# -- pair classification ----------------------------------------------------------------

RELATIONS = {
    "empty": "no selector; SelectEffectTypeImplicitTargets alone decides (EffectImplicitTargetTypes)",
    "a-only": "B unused (0); A alone",
    "b-only": "A slot empty (0); B alone",
    "b-uses-a-dest": "A writes m_targets dst; B selects recipients around/along that dst",
    "b-uses-a-src": "A writes m_targets src; B selects recipients around that src",
    "b-moves-a-dest": "A writes dst; B (DEST_DEST) CheckDst + ModDst relative to it",
    "b-clips-a-dest": "A writes dst; B (TRAJ) clips it along the trajectory",
    "b-overwrites-a-dest": "both write dst; B's SetDst wins for AddDestTarget and later effects",
    "b-uses-a-last-recipient": "B (LAST reference) centres on the last unique target carrying this effect bit (A's last add)",
    "a-dest-b-independent": "A writes dst (only AddDestTarget / later effects read it); B adds recipients from a caster/target reference",
    "a-src-b-independent": "A writes src (unused by B); B adds recipients from a caster/target reference",
    "a-src-b-reads-spell-dest": "A writes src; B reads the dst that exists before A (explicit/InitExplicitTargets or an earlier effect)",
    "a-recipients-b-dest": "A adds recipients; B writes or moves dst (effect destination / later effects)",
    "a-recipients-b-src": "A adds recipients; B writes src (read only by later effects)",
    "a-recipients-b-reads-spell-dest": "A adds recipients; B reads the dst that exists before A (explicit/InitExplicitTargets or an earlier effect)",
    "a-recipients-b-reads-spell-src": "A adds recipients; B reads the src that exists before A",
    "independent-union": "both add recipients from independent references; AddUnitTarget unions them (no intersection)",
    "a-reads-dest-b-writes": "A reads the pre-existing dst; B then writes dst",
    "dest-only": "neither adds recipients; only m_targets dst/src changes",
}


def classify(a: int, b: int) -> dict[str, Any]:
    """Composition semantics of one (TargetA, TargetB) pair.  Mirrors: Spell.cpp:787-788 + handlers."""
    nyi = [t for t in (a, b) if t and info(t).category == "NYI"]
    if nyi:  # SelectEffectImplicitTargets logs and does nothing (Spell.cpp:1020-1022): same as an empty slot
        out = classify(0 if a in nyi else a, 0 if b in nyi else b)
        out["nyi_slots"] = nyi
        out["a"] = {"id": a, "name": info(a).name, **reads_writes(a)}
        out["b"] = {"id": b, "name": info(b).name, **reads_writes(b)}
        return out
    if a == 0 and b == 0:
        rel = "empty"
    elif b == 0:
        rel = "a-only"
    elif a == 0:
        rel = "b-only"
    else:
        ra, rb = reads_writes(a), reads_writes(b)
        a_dst = any(w.startswith("dst") for w in ra["writes"])
        a_src = any(w.startswith("src") for w in ra["writes"])
        b_set_dst = any(w.startswith("dst:set") for w in rb["writes"])
        b_any_dst = any(w.startswith("dst") for w in rb["writes"])
        b_src = any(w.startswith("src") for w in rb["writes"])
        sb = info(b)
        if "last-recipient" in rb["reads"] and ra["adds"]:
            rel = "b-uses-a-last-recipient"
        elif a_dst and sb.category == "TRAJ":
            rel = "b-clips-a-dest"
        elif a_dst and sb.category == "DEFAULT" and sb.reference == "DEST":
            rel = "b-moves-a-dest"
        elif a_dst and "dst" in rb["reads"] and rb["adds"]:
            rel = "b-uses-a-dest"
        elif a_src and "src" in rb["reads"] and rb["adds"]:
            rel = "b-uses-a-src"
        elif a_dst and b_set_dst:
            rel = "b-overwrites-a-dest"
        elif a_dst and rb["adds"]:
            rel = "a-dest-b-independent"
        elif a_src and "dst" in rb["reads"] and rb["adds"]:
            rel = "a-src-b-reads-spell-dest"
        elif a_src and rb["adds"]:
            rel = "a-src-b-independent"
        elif ra["adds"] and b_any_dst:
            rel = "a-recipients-b-dest"
        elif ra["adds"] and b_src:
            rel = "a-recipients-b-src"
        elif a_src and (b_any_dst or b_src):
            rel = "dest-only"
        elif ra["adds"] and "dst" in rb["reads"] and rb["adds"]:
            rel = "a-recipients-b-reads-spell-dest"
        elif ra["adds"] and "src" in rb["reads"] and rb["adds"]:
            rel = "a-recipients-b-reads-spell-src"
        elif ra["adds"] and rb["adds"]:
            rel = "independent-union"
        elif "dst" in ra["reads"] and b_set_dst:
            rel = "a-reads-dest-b-writes"
        elif not ra["adds"] and not rb["adds"]:
            rel = "dest-only"
        else:
            raise FailClosed(f"composition ({a},{b}) has no classification rule")
    return {"relation": rel, "description": RELATIONS[rel],
            "a": {"id": a, "name": info(a).name, **reads_writes(a)},
            "b": {"id": b, "name": info(b).name, **reads_writes(b)}}


def pair_hazards(a: int, b: int, effects_before_write_dst: bool = False) -> list[str]:
    """Pair-local hazards visible without a fixture."""
    out = []
    for slot, t in (("A", a), ("B", b)):
        if t == 116:
            out.append(f"{slot}=116 ModDst ASSERT unless m_targets already HasDst (Spell.cpp:1434)")
        if t == 54:
            out.append(f"{slot}=54 ConeAngle 0 -> 90 at load (SpellMgr.cpp:5282), Spell.cpp:1285 180 default unreachable")
    if a and b and info(b).reference == "DEST" and info(b).category in ("AREA", "LINE") \
            and not any(w.startswith("dst") for w in reads_writes(a)["writes"]):
        out.append("B reads m_targets dst not written by A: explicit dst (client / InitExplicitTargets fallback) or an earlier effect")
    return out


# -- real-data inputs and corpus ------------------------------------------------------------

def spell_explicit_inputs(ctx, spell: int) -> dict[str, Any]:
    """``_InitializeExplicitTargetMask`` inputs of one DIFFICULTY_NONE spell after load corrections.

    Mirrors (loading): SpellInfo.cpp:1419-1510 (RangeEntry, Targets, AttributesEx13),
    SpellMgr.cpp corrections (targets / effect type only; RangeEntry corrections are not applied here
    and are listed by :func:`targeting.attributes.corrections`).
    """
    from .attributes import corrected_effects
    effs, _ = corrected_effects(spell, ctx.data.effects(spell))
    misc = ctx.data.misc(spell)
    rng = ctx.data.ranges.get(misc["RangeIndex"]) if misc else None
    restr = ctx.data.restrictions(spell)
    return {
        "effects": [EffectSlots(e["index"], e["effect"], e["a"], e["b"], e["attributes"]) for e in effs],
        "has_range": rng is not None,
        "max_range_negative": float(rng["RangeMax_0"]) if rng else 0.0,
        "max_range_positive": float(rng["RangeMax_1"]) if rng else 0.0,
        "targets": int(restr["Targets"]) if restr else 0,
        "attributes13": int(misc["Attributes_13"]) & 0xFFFFFFFF if misc else 0,
    }


def explicit_mask_for(ctx, spell: int) -> tuple[int, int]:
    x = spell_explicit_inputs(ctx, spell)
    return explicit_target_mask(x["effects"], max_range_negative=x["max_range_negative"],
                                max_range_positive=x["max_range_positive"], targets=x["targets"],
                                attributes13=x["attributes13"])


def probe_input_line(ctx, spell: int) -> str:
    x = spell_explicit_inputs(ctx, spell)
    effs = [e for e in x["effects"]]
    parts = [str(spell), str(int(x["has_range"])), repr(x["max_range_negative"]), repr(x["max_range_positive"]),
             str(x["targets"] & 0xFFFFFFFF), str(x["attributes13"]), str(len(effs))]
    for e in effs:
        parts += [str(e.effect), str(e.target_a), str(e.target_b), str(e.attributes & 0xFFFFFFFF)]
    return " ".join(parts)


def cross_effect_dependencies(ctx, spell: int) -> list[dict[str, Any]]:
    """Selection turns whose selectors read a dst/src written by an *earlier* turn (Spell.cpp:787-800).

    Grouping is Track F's exact static plan (``targeting.cmd_f.static_plan`` over
    ``targeting.recipients.selection_plan``, Spell.cpp:741-785, incl. radius / conditions /
    PlayersOnly / script-hook splits); selector ids are taken after load corrections.
    """
    from .cmd_f import static_plan, view
    x = spell_explicit_inputs(ctx, spell)
    slots = {e.index: e for e in x["effects"]}
    plan, _, _ = static_plan(view(spell))
    out = []
    writer: dict[str, int | None] = {"dst": None, "src": None}
    earlier: dict[str, int | None] = {"dst": None, "src": None}  # last writer before this turn
    for step in plan:
        if not step.mask:
            continue
        e = slots[step.effect]
        members = [k for k in range(32) if step.mask >> k & 1]
        earlier = dict(writer)
        for slot, t in (("A", e.target_a), ("B", e.target_b)):
            if t >= 153:
                continue
            rw = reads_writes(t)
            for kind in ("dst", "src"):
                if kind in rw["reads"] and earlier[kind] is not None:
                    value_from_earlier = writer[kind] != e.index
                    out.append({"effect": e.index, "group_effects": members, "slot": slot, "selector": t,
                                "reads": kind, "earlier_writer": earlier[kind],
                                "value_from_earlier_turn": value_from_earlier})
            for w in rw["writes"]:
                writer[w.split(":")[0]] = e.index
    return out


def build_corpus(ctx, census: dict[str, Any]) -> dict[str, Any]:
    from collections import Counter, defaultdict
    rows = census["_rows"]
    pair_rows: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["corrected"]:
            pair_rows[tuple(r["corrected"])].append(r)
    cu_pairs = Counter((r["a"], r["b"]) for r in census["_cu_rows"])
    pairs = []
    for (a, b), rs in sorted(pair_rows.items()):
        c = classify(a, b)
        witnesses = sorted({(r["spell"], r["effect"]) for r in rs if not r["build_skew"]})[:5]
        pairs.append({"a": a, "b": b, "a_name": info(a).name, "b_name": info(b).name, "relation": c["relation"],
                      "description": c["description"], "a_rw": c["a"], "b_rw": c["b"],
                      "hazards": pair_hazards(a, b), "player_effects": len(rs),
                      "player_spells": len({r["spell"] for r in rs}),
                      "build_skew_effects": sum(r["build_skew"] for r in rs),
                      "controlled_unit_effects": cu_pairs.get((a, b), 0),
                      "witnesses": [f"{s}:{e}" for s, e in witnesses],
                      "evidence": "trinity-consumer", "consumer": "Spell.cpp:787-788"})
    cu_only = []
    for (a, b), n in sorted(cu_pairs.items()):
        if (a, b) in pair_rows:
            continue
        cu_only.append({"a": a, "b": b, "relation": classify(a, b)["relation"], "controlled_unit_effects": n,
                        "hazards": pair_hazards(a, b)})
    # explicit masks + cross-effect coupling over current-player spells
    masks = Counter()
    cross = []
    for spell in sorted({r["spell"] for r in rows}):
        exp, req = explicit_mask_for(ctx, spell)
        masks[exp] += 1
        for d in cross_effect_dependencies(ctx, spell):
            cross.append({"spell": spell, **d, "build_skew": ctx.is_skew(spell)})
    from .selectors import flag_names
    return {
        "relations": RELATIONS,
        "rules": [
            {"rule": "A then B, same SpellEffectInfo (first of group) and same grouped effect mask", "consumer": "Spell.cpp:787-788",
             "evidence": "trinity-consumer"},
            {"rule": "recipients union into m_UniqueTargetInfo; existing entry ORs effect bits; B never filters A",
             "consumer": "Spell.cpp:2466-2471", "evidence": "trinity-consumer"},
            {"rule": "DEFAULT DEST/SRC selectors SetDst/SetSrc spell-wide m_targets (no effect mask)",
             "consumer": "Spell.cpp:980, 1650, 1687", "evidence": "trinity-consumer"},
            {"rule": "DEST/SRC-referenced area/line read m_targets dst/src position (GetDstPos never null, even if !HasDst)",
             "consumer": "Spell.cpp:1365-1370, 1972-1977, SpellCastTargets::GetDstPos Spell.cpp:336", "evidence": "trinity-consumer"},
            {"rule": "DEST_DEST and TRAJ: CheckDst() (dst := caster if none) then ModDst",
             "consumer": "Spell.cpp:966, 1696, 1739, 1960, 7268", "evidence": "trinity-consumer"},
            {"rule": "UNIT_AND_DEST area: ModDst(referer) after the search; ModDst ASSERTs HasDst",
             "consumer": "Spell.cpp:1426-1435, 377-381", "evidence": "trinity-consumer"},
            {"rule": "LAST reference: last m_UniqueTargetInfo entry with bit (1 << first effect of group), else caster",
             "consumer": "Spell.cpp:1339-1352", "evidence": "trinity-consumer"},
            {"rule": "AddDestTarget per effect snapshots m_targets dst after that effect's selectors and EffectType targets",
             "consumer": "Spell.cpp:799-800", "evidence": "trinity-consumer"},
            {"rule": "m_targets is not reset between effects: later effects read earlier effects' dst/src",
             "consumer": "Spell.cpp:727-844", "evidence": "trinity-consumer"},
            {"rule": "explicit mask folds A then B of each effect with shared srcSet/dstSet; UNIT_AND_DEST sets dstSet without "
                     "requesting DEST_LOCATION", "consumer": "SpellInfo.cpp:140-244, 4572-4606", "evidence": "differential"},
            {"rule": "InitExplicitTargets: needed DEST without client dst -> explicit object position else caster; "
                     "not needed -> RemoveDst (flag only); SRC likewise with caster",
             "consumer": "Spell.cpp:665-688", "evidence": "trinity-consumer"},
            {"rule": "channel: after all selectors, m_channelTargetEffectMask gets the effect bit if any unique target carries it",
             "consumer": "Spell.cpp:824-843", "evidence": "trinity-consumer"},
            {"rule": "REQUIRE_ALL_TARGETS checks only unit-object selector groups after both selectors",
             "consumer": "Spell.cpp:802-821", "evidence": "trinity-consumer"},
        ],
        "pairs": pairs,
        "controlled_unit_only_pairs": cu_only,
        "relation_counts": dict(sorted(Counter(p["relation"] for p in pairs for _ in range(p["player_effects"])).items())),
        "explicit_target_masks": {"spells": sum(masks.values()),
                                  "by_mask": {f"0x{m:08X}": {"spells": n, "flags": flag_names(m)}
                                              for m, n in sorted(masks.items())},
                                  "evidence": "differential (tests/test_tg_a_probe.py::test_explicit_masks_all_player_spells)"},
        "cross_effect_dependencies": {"count": len(cross), "rows": cross,
                                      "turn_reads_after_earlier_write": {
                                          "effects": len({(r["spell"], k) for r in cross for k in r["group_effects"]}),
                                          "spells": len({r["spell"] for r in cross}),
                                          "definition": "a selection turn reads m_targets dst/src after an earlier turn "
                                                        "wrote it (Track F tag dest-read-after-earlier-write)"},
                                      "dst_reads_by_turn_lead": {
                                          "effects": len({(r["spell"], r["effect"]) for r in cross if r["reads"] == "dst"}),
                                          "spells": len({r["spell"] for r in cross if r["reads"] == "dst"}),
                                          "definition": "dst only, counted at the turn's lead effect (same definition as "
                                                        "Track F effect-recipients.json)"},
                                      "value_from_earlier_turn": {
                                          "effects": len({(r["spell"], k) for r in cross if r["value_from_earlier_turn"]
                                                          for k in r["group_effects"]}),
                                          "spells": len({r["spell"] for r in cross if r["value_from_earlier_turn"]}),
                                          "definition": "the read value was not overwritten by this turn's own TargetA "
                                                        "before TargetB read it"},
                                      "grouping": "Track F exact static plan (targeting.cmd_f.static_plan); "
                                                  "effects = every member of a reading selection turn"},
    }
