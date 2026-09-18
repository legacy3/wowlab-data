"""Track C commands: ``stacks`` and ``charges``.

``aura_lifecycle.py stacks <spell>``            stack lifecycle of one spell (facts, family, initial stacks,
                                                external mutators, a default reapplication timeline)
``aura_lifecycle.py stacks --census --out F``   the ``stacks.json`` corpus
``aura_lifecycle.py charges <spell>``           proc-charge lifecycle of one spell
``aura_lifecycle.py charges --census --out F``  the ``charges.json`` corpus
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from . import FailClosed, records
from . import charges as C
from . import stacks as S
from .cli import emit

POPS = ("all", "player", "controlled")
TC = "TrinityCore@7f3d43b"


def _add(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--census", action="store_true", help="write the corpus instead of explaining one spell")
    p.add_argument("--out")


# ---------------------------------------------------------------------------
# shared census pass (one context, cached on it)
# ---------------------------------------------------------------------------

def _census_facts(ctx) -> dict[str, Any]:
    cached = getattr(ctx, "_al_c_census", None)
    if cached is not None:
        return cached
    from .providers import populations
    pops = populations(ctx)
    facts: dict[int, dict[str, Any]] = {}
    missing: list[int] = []
    for s in sorted(pops["all"]):
        try:
            f = S.stack_facts(ctx, s)
        except FailClosed:
            missing.append(s)
            continue
        f["family"] = S.stack_family(f)
        cf = C.charge_facts(ctx, s)
        cf["family"] = C.charge_family(cf)
        facts[s] = {"stack": f, "charge": cf}
    mods = S.spellmod_rows(ctx)
    targets = S.spellmod_targets(ctx, mods, pops["all"])
    out = {"pops": pops, "facts": facts, "missing": missing, "mas": S.modify_aura_stacks_rows(ctx),
           "linked": S.linked_rows(ctx), "spellmods": mods, "spellmod_targets": targets,
           "sites": S.script_mutator_sites(ctx)}
    ctx._al_c_census = out
    return out


def _pop_counts(spells, pops) -> dict[str, int]:
    s = set(spells)
    return {p: len(s & pops[p]) for p in POPS}


def _witness(ctx, spells, pops, limit: int = 8) -> list[dict[str, Any]]:
    """Player spells first (flagging build skew), then the rest, ascending SpellID."""
    s = sorted(spells)
    player = [x for x in s if x in pops["player"]]
    rest = [x for x in s if x not in pops["player"]]
    return [{"spell": x, "name": ctx.name(x), "player": x in pops["player"], "build_skew": ctx.is_skew(x)}
            for x in (player + rest)[:limit]]


def _shape_for(ctx, spell: int, *, mods: dict[str, tuple[int, float]] | None = None) -> S.AuraShape:
    f = S.stack_facts(ctx, spell)
    cf = C.charge_facts(ctx, spell)
    info = ctx.catalog.get(spell)
    entry = cf["proc_entry"]
    mods = mods or {}
    return S.AuraShape(capacity=f["capacity"], max_stacks=S.calc_max_stack_amount(f["capacity"], mods.get("MaxAuraStacks")),
                       max_charges=C.calc_max_charges(cf["db2_proc_charges"], entry["charges"] if entry else None,
                                                      mods.get("ProcCharges")),
                       aura_unique=f["aura_unique"], aura_unique_per_caster=f["aura_unique_per_caster"],
                       pandemic=f["pandemic"], max_duration=info.duration_ms if info.duration_ms is not None else -1)


def _timeline(ctx, spell: int, steps: list[dict[str, Any]], rules_out: str, *, requested: int = 1,
              mods: dict[str, tuple[int, float]] | None = None, note: str = "") -> dict[str, Any]:
    shape = _shape_for(ctx, spell, mods=mods)
    f = S.stack_facts(ctx, spell)
    steps = [dict(s) for s in steps]
    for s in steps:
        if s["op"] == "reapply":
            s.setdefault("multislot", f["multislot"])
            s.setdefault("reset_periodic", S.reset_periodic_on_hit(f["capacity"]))
    if steps and steps[0]["op"] == "apply":
        steps[0].setdefault("stacks", requested)
    return {"spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell), "shape": shape.__dict__,
            "mods": {k: list(v) for k, v in (mods or {}).items()}, "note": note, "rules_out": rules_out,
            "steps": steps, "timeline": S.run_timeline(shape, steps),
            "evidence": ["trinity-probe", "differential"]}


# ---------------------------------------------------------------------------
# explain one spell
# ---------------------------------------------------------------------------

def explain_stacks(ctx, spell: int) -> dict[str, Any]:
    f = S.stack_facts(ctx, spell)
    fam = S.stack_family(f)
    cens = _census_facts(ctx)
    mods = [dict(m, targets_this=True) for m in cens["spellmods"]
            if spell in cens["spellmod_targets"].get((m["spell"], m["index"]), [])]
    doses = [m for m in mods if m["op"] == "Doses"]
    maxmods = [m for m in mods if m["op"] == "MaxAuraStacks"]
    mas = [r for r in cens["mas"] if r["target_spell"] == spell]
    linked_parent = [r for r in cens["linked"] if r["child"] == spell]
    linked_child = [r for r in cens["linked"] if r["spell"] == spell]
    sites = [s for s in cens["sites"] if spell in s["spells"]]
    hazards = []
    if f["capacity"] > 255:
        hazards.append("capacity > 255: ModStackAmount stores uint8 (wraps to 0 at 256) -- AL-D-C-01")
    for m in maxmods:
        if m["kind"] == "flat" and f["capacity"] + m["value"] <= 0:
            hazards.append(f"MaxAuraStacks {m['value']:+g} from {m['spell']} makes CalcMaxStackAmount <= 0: the next "
                           "increasing ModStackAmount stores uint8(max) -- AL-D-C-02")
    steps = [{"t": 0, "op": "apply"}, {"t": 1000, "op": "reapply"}, {"t": 2000, "op": "reapply"}]
    return {
        "spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell),
        "facts": f, "family": fam, "family_doc": S.FAMILY_DOC[fam],
        "initial_stacks": {
            "default": S.initial_stacks(),
            "capacity_is_not_initial": True,
            "doses_modifiers": doses,
            "script_sites": [s for s in sites if s["mutator"] == "SPELLVALUE_AURA_STACK"],
            "rule": "AL-R-C-01", "evidence": ["trinity-consumer", "trinity-probe"],
            "coords": ["Spell.cpp:450", "Spell.cpp:506", "Object.cpp:2239-2241", "Spell.cpp:3251", "SpellAuras.h:120",
                       "SpellAuras.cpp:481"],
        },
        "max_stacks": {"capacity": f["capacity"], "max_aura_stacks_modifiers": maxmods,
                       "coords": ["SpellAuras.cpp:1083-1091"]},
        "reapplication": {
            "multislot": f["multislot"], "one_slot_different_casters": f["one_slot_different_casters"],
            "lookup_key": "caster GUID ignored (shared slot)" if f["one_slot_different_casters"] else "same caster GUID",
            "reset_periodic_timer_on_hit": f["reset_periodic_on_hit"],
            "refresh_gate": "when the stack count does not drop (capacity != 0: AURA_UNIQUE ignored)" if f["capacity"] else (
                "never (AURA_UNIQUE / AURA_UNIQUE_PER_CASTER)" if (f["aura_unique"] or f["aura_unique_per_caster"])
                else "when the stack count does not drop"),
            "coords": ["Unit.cpp:3386-3446", "SpellAuras.cpp:1093-1127", "Spell.cpp:3240"],
        },
        "amount_scaling": [{"index": e["index"], "aura": e["aura"],
                            "scaled_by_stacks": not e["suppress_points_stacking"],
                            "base_accumulates_on_refresh": e["aura_points_stack"],
                            "rounded": e["aura"] in S.ROUNDED_AURA_TYPES}
                           for e in f["effects"] if e["aura"]],
        "external_mutators": {"modify_aura_stacks": mas, "linked_parents": linked_parent,
                              "linked_children": linked_child, "script_sites": sites},
        "hazards": hazards,
        "charges": C.charge_facts(ctx, spell),
        "default_timeline": _timeline(ctx, spell, steps, "none (illustration of the default reapplication path)"),
    }


def explain_charges(ctx, spell: int) -> dict[str, Any]:
    cf = C.charge_facts(ctx, spell)
    fam = C.charge_family(cf)
    cens = _census_facts(ctx)
    mods = [m for m in cens["spellmods"] if m["op"] == "ProcCharges"
            and spell in cens["spellmod_targets"].get((m["spell"], m["index"]), [])]
    return {"spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell), "facts": cf,
            "family": fam, "family_doc": C.FAMILY_DOC[fam], "proc_charges_modifiers": mods,
            "refresh_resets_charges": "yes, on every refreshing ModStackAmount (SpellAuras.cpp:1124)",
            "dispel": ("ModCharges(-n) (ATTR7_DISPEL_REMOVES_CHARGES)" if cf["dispel_removes_charges"]
                       else "ModStackAmount(-n)") + " -- Unit.cpp:4019-4022; dispel list counts charges/stacks "
                                                   "Unit.cpp:4765 (track E)",
            "distinct_from_spell_charges": cf["spell_charge_category"] != 0,
            "coords": ["SpellAuras.cpp:496-499", "SpellAuras.cpp:1004-1038", "SpellAuras.cpp:1810-1832",
                       "SpellAuras.cpp:1886"]}


# ---------------------------------------------------------------------------
# corpora
# ---------------------------------------------------------------------------

def _bucket(cap: int) -> str:
    if cap <= 1:
        return str(cap)
    if cap <= 9:
        return "2-9"
    if cap <= 99:
        return "10-99"
    if cap <= 255:
        return "100-255"
    return ">255"


def stacks_corpus(ctx, command: str) -> dict[str, Any]:
    cens = _census_facts(ctx)
    pops, facts = cens["pops"], cens["facts"]
    fams: dict[str, set[int]] = defaultdict(set)
    for s, f in facts.items():
        fams[f["stack"]["family"]].add(s)
    families = {k: {"doc": S.FAMILY_DOC[k], "count": _pop_counts(v, pops), "witnesses": _witness(ctx, v, pops)}
                for k, v in sorted(fams.items())}
    cap_dist = {p: dict(sorted(Counter(_bucket(facts[s]["stack"]["capacity"]) for s in facts if s in pops[p]).items()))
                for p in POPS}
    eff = {p: {"aura_effects": 0, "suppress_points_stacking": 0, "aura_points_stack": 0} for p in POPS}
    for s, f in facts.items():
        for e in f["stack"]["effects"]:
            if not e["aura"]:
                continue
            for p in POPS:
                if s in pops[p]:
                    eff[p]["aura_effects"] += 1
                    eff[p]["suppress_points_stacking"] += e["suppress_points_stacking"]
                    eff[p]["aura_points_stack"] += e["aura_points_stack"]
    over255 = sorted(s for s, f in facts.items() if f["stack"]["capacity"] > 255)
    unique_ignored = sorted(s for s, f in facts.items() if f["stack"]["capacity"] > 0
                            and (f["stack"]["aura_unique"] or f["stack"]["aura_unique_per_caster"]))
    mas = []
    for r in cens["mas"]:
        t = facts.get(r["target_spell"])
        mas.append(dict(r, target_family=t["stack"]["family"] if t else None,
                        target_capacity=t["stack"]["capacity"] if t else None,
                        player=r["spell"] in pops["player"], evidence="db2-fact",
                        consumer="SpellEffects.cpp:6126-6145"))
    linked_children = {r["child"] for r in cens["linked"]}
    mods = []
    for m in cens["spellmods"]:
        tg = cens["spellmod_targets"].get((m["spell"], m["index"]), [])
        mods.append(dict(m, class_mask=f"0x{m['class_mask']:032X}", player=m["spell"] in pops["player"],
                         provider_targets=len(tg), player_targets=[t for t in tg if t in pops["player"]],
                         evidence=["db2-fact", "structural-inference"],
                         consumer={"Doses": "Spell.cpp:506", "MaxAuraStacks": "SpellAuras.cpp:1088",
                                   "ProcCharges": "SpellAuras.cpp:1012"}[m["op"]]))
    doses_over_cap = []
    dose_targets: dict[int, list] = defaultdict(list)
    max_targets: set[int] = set()
    for m in mods:
        for t in cens["spellmod_targets"].get((m["spell"], m["index"]), []):
            if m["op"] == "Doses":
                dose_targets[t].append(m)
            elif m["op"] == "MaxAuraStacks":
                max_targets.add(t)
    for t, ms in sorted(dose_targets.items()):
        cap = facts[t]["stack"]["capacity"] if t in facts else None
        for m in ms:
            if cap is not None and m["kind"] == "flat" and t not in max_targets and 1 + m["value"] > max(cap, 1):
                doses_over_cap.append({"target": t, "name": ctx.name(t), "capacity": cap, "modifier": m["spell"],
                                       "initial_stacks": S.initial_stacks(doses_mod=int(1 + m["value"]))})
    sites = [dict(s, evidence="script-consumer") for s in cens["sites"]]
    site_counts = {"all": dict(sorted(Counter(s["mutator"] for s in sites).items())),
                   "bound_to_spells": sum(1 for s in sites if s["spells"]),
                   "bound_to_player_spells": sum(1 for s in sites if set(s["spells"]) & pops["player"])}
    payload: dict[str, Any] = {
        "provenance": records.provenance(command, population_sizes={p: len(pops[p]) for p in POPS},
                                         missing_spellinfo=cens["missing"]),
        "families": families,
        "capacity_distribution": cap_dist,
        "effect_attributes": eff,
        "uint8_capacity_hazard": {"count": _pop_counts(over255, pops), "spells": over255,
                                  "evidence": ["db2-fact", "trinity-probe"], "defect": "AL-D-C-01"},
        "aura_unique_ignored_when_capacity_nonzero": {"count": _pop_counts(unique_ignored, pops),
                                                      "witnesses": _witness(ctx, unique_ignored, pops),
                                                      "consumer": "SpellAuras.cpp:1114"},
        "modify_aura_stacks": {"count": len(mas), "by_mode": dict(Counter(r["mode"] for r in mas)),
                               "player_sources": sum(r["player"] for r in mas),
                               "target_families": dict(sorted(Counter(str(r["target_family"]) for r in mas).items())),
                               "set_to_zero": [r for r in mas if r["mode"] == "SetStackAmount" and r["base_points"] <= 0],
                               "rows": mas},
        "linked_stack_sync": {"count": len(cens["linked"]),
                              "player_parents": [dict(r, name=ctx.name(r["spell"])) for r in cens["linked"]
                                                 if r["spell"] in pops["player"]],
                              "children_that_are_multislot": sorted(t for t in linked_children
                                                                    if t in facts and facts[t]["stack"]["multislot"]),
                              "consumer": "SpellAuraEffects.cpp:5324-5359", "evidence": ["db2-fact", "trinity-consumer"]},
        "spell_modifiers": {"count": dict(Counter(m["op"] for m in mods)),
                            "player_count": dict(Counter(m["op"] for m in mods if m["player"])),
                            "doses_exceeding_capacity": doses_over_cap, "rows": mods},
        "script_sites": {"counts": site_counts, "rows": sites},
        "async_stacking_attribute": _async_section(ctx, facts, pops),
        "charges_with_capacity": _charges_capacity_section(ctx, facts, pops),
        "initial_stack_sources": _initial_sources(),
        "witnesses": _witnesses(ctx),
        "timelines": _timelines(ctx),
    }
    payload.update(_records(_counts(cens)))
    records.validate_corpus(payload)
    return payload


def _async_section(ctx, facts, pops) -> dict[str, Any]:
    spells = sorted(s for s, f in facts.items() if f["stack"]["async_stacking_attr"])
    return {"count": _pop_counts(spells, pops),
            "families": dict(sorted(Counter(facts[s]["stack"]["family"] for s in spells).items())),
            "player": [{"spell": s, "name": ctx.name(s), "capacity": facts[s]["stack"]["capacity"],
                        "family": facts[s]["stack"]["family"], "build_skew": ctx.is_skew(s)}
                       for s in spells if s in pops["player"]],
            "spells": spells,
            "trinity": "SPELL_ATTR15_UNK10 -- no consumer at the pinned revision; stacks share one duration",
            "simc": "SX_ASYNCHRONOUS_STACKING_AURA (engine/dbc/data_enums.hh:1910) -> buff_stack_behavior::ASYNCHRONOUS "
                    "(engine/buff/buff.cpp:777-779): per-stack expiry",
            "core": "SpellAttributeKind::AsynchronousStackingBuff (crates/dbc/src/spell_attribute.rs:181, :491) -- "
                    "navigation only",
            "evidence": ["db2-fact", "simc-consumer", "core-navigation", "retail-unknown"],
            "unknown": "AL-U-C-09", "rule": "AL-R-C-12"}


def _charges_capacity_section(ctx, facts, pops) -> dict[str, Any]:
    spells = sorted(s for s, f in facts.items() if f["stack"]["db2_proc_charges"] and f["stack"]["capacity"])
    return {"count": _pop_counts(spells, pops),
            "player": [{"spell": s, "name": ctx.name(s), "capacity": facts[s]["stack"]["capacity"],
                        "db2_proc_charges": facts[s]["stack"]["db2_proc_charges"],
                        "trinity_initial": {"stacks": 1, "charges": facts[s]["charge"]["initial_charges_no_mods"]},
                        "simc_initial_stacks": min(facts[s]["stack"]["db2_proc_charges"], facts[s]["stack"]["capacity"])}
                       for s in spells if s in pops["player"]],
            "trinity": "ProcCharges -> m_procCharges, independent of m_stackAmount (SpellAuras.cpp:498-499)",
            "simc": "spell_data_t::initial_stacks() returns _proc_charges (engine/dbc/spell_data.hpp:564-565); "
                    "buff_t::set_initial_stack uses it (engine/buff/buff.cpp:1048-1050); max_stacks falls back to "
                    "|initial_stacks| when CumulativeAura is 0 (buff.cpp:974-980); simc_initial_stacks here assumes the "
                    "buff clamps the initial stack to max_stack",
            "evidence": ["db2-fact", "trinity-consumer", "simc-consumer", "retail-unknown"],
            "unknown": "AL-U-C-10"}


def _initial_sources() -> list[dict[str, Any]]:
    return [
        {"source": "default", "value": "1", "coords": "Spell.cpp:450 / SpellAuras.h:134", "evidence": "trinity-consumer"},
        {"source": "SpellModOp::Doses", "value": "(1 + flat) * pct at Spell ctor (int32)", "coords": "Spell.cpp:506",
         "evidence": "trinity-consumer"},
        {"source": "SPELLVALUE_AURA_STACK override", "value": "uint8(value), replaces the Doses result",
         "coords": "Object.cpp:2239-2241, Spell.cpp:8741", "evidence": "trinity-consumer"},
        {"source": "AuraCreateInfo::SetStackAmount floor", "value": "<= 0 -> 1", "coords": "SpellAuras.h:120",
         "evidence": "trinity-probe"},
        {"source": "Aura::Aura narrowing", "value": "uint8(StackAmount)", "coords": "SpellAuras.cpp:481, SpellAuras.h:238",
         "evidence": "trinity-probe"},
        {"source": "spell steal", "value": "stolenCharges", "coords": "Unit.cpp:4078-4081", "evidence": "trinity-consumer"},
        {"source": "DB load", "value": "saved stackCount", "coords": "Player.cpp:19087, Pet.cpp:1273",
         "evidence": "trinity-consumer"},
        {"source": "post-construction scripts", "value": "SetStackAmount / SetAuraStack", "coords": "script_sites",
         "evidence": "script-consumer"},
        {"source": "SpellAuraOptions.CumulativeAura", "value": "NOT a source (capacity only)",
         "coords": "SpellInfo.cpp:1390 -> SpellAuras.cpp:1085", "evidence": "trinity-consumer"},
    ]


WITNESSES = (
    (1269312, "Phalanx: passive, CumulativeAura 2 -> initial 1; multi-slot, so reapplication never stacks"),
    (974, "Earth Shield: capacity 9, DOT_STACKING_RULE -> per caster; SuppressPointsStacking on every effect; "
          "Doses +3 (Earthen Communion) and MaxAuraStacks/Doses -99 (Therazane's Resilience)"),
    (192081, "Ironfur: capacity 1; Ursine Adept MaxAuraStacks +19 turns it into one shared-duration 20-stack aura"),
    (260708, "Sweeping Strikes: capacity 18; Improved Sweeping Strikes Doses +6 -> initial 7"),
    (44544, "Fingers of Frost: spell_proc USE_STACKS_FOR_CHARGES -> a proc removes one stack"),
    (36032, "Arcane Charge: shared across casters, capacity 4, proc charges 1 reset by each refresh"),
    (460553, "Doom: capacity 0 + AURA_UNIQUE -> reapplication neither refreshes nor resets charges"),
    (472433, "Evangelism: script sets initial stacks through SPELLVALUE_AURA_STACK"),
    (360827, "Blistering Scales: 15 proc charges; Regenerative Chitin ProcCharges -16 -> uint32(-1.0) (UB) -> 255"),
    (191634, "Stormkeeper: aura charges without a proc entry; consumed only by its script"),
)


def _witnesses(ctx) -> list[dict[str, Any]]:
    out = []
    for spell, why in WITNESSES:
        f = S.stack_facts(ctx, spell)
        cf = C.charge_facts(ctx, spell)
        out.append({"spell": spell, "name": ctx.name(spell), "build_skew": ctx.is_skew(spell), "why": why,
                    "stack_family": S.stack_family(f), "charge_family": C.charge_family(cf),
                    "capacity": f["capacity"], "initial_charges_no_mods": cf["initial_charges_no_mods"],
                    "evidence": ["db2-fact", "trinity-consumer"]})
    return out


def _timelines(ctx) -> list[dict[str, Any]]:
    re = {"op": "reapply"}
    return [
        _timeline(ctx, 1269312, [{"t": 0, "op": "apply"}, {"t": 1000, **re}],
                  "initial stacks = CumulativeAura (2); reapplying a passive adds a stack",
                  note="Phalanx: second application does not touch the first aura (multi-slot); A decides coexistence"),
        _timeline(ctx, 36032, [{"t": 0, "op": "apply"}, {"t": 1000, **re}, {"t": 2000, **re}, {"t": 3000, **re},
                               {"t": 4000, **re}, {"t": 4500, "op": "consume_proc"}],
                  "capped reapplication does not refresh; charges survive refresh; stacks and charges are one counter",
                  note="Arcane Charge: at capacity the reapply still refreshes and resets charges; a proc drops the charge "
                       "and removes the aura at 0 charges regardless of 4 stacks"),
        _timeline(ctx, 460553, [{"t": 0, "op": "apply"}, {"t": 5000, **re}],
                  "every same-caster reapplication refreshes the duration",
                  note="Doom: AURA_UNIQUE with capacity 0: no RefreshTimers (the Spell.cpp:3294 max-duration reset "
                       "only fires if the quoted duration changed -- track B)"),
        _timeline(ctx, 974, [{"t": 0, "op": "apply"}, {"t": 1000, **re}],
                  "a negative MaxAuraStacks mod makes the aura non-stacking",
                  requested=S.initial_stacks(doses_mod=S.apply_spell_mod(1, -99, 1.0, "int32")),
                  mods={"MaxAuraStacks": (-99, 1.0)},
                  note="Earth Shield under Therazane's Resilience (Trinity arithmetic; Retail unknown AL-U-C-01)"),
        _timeline(ctx, 44544, [{"t": 0, "op": "apply"}, {"t": 500, **re}, {"t": 1000, "op": "consume_proc",
                                                                           "use_stacks": True},
                               {"t": 1500, "op": "consume_proc", "use_stacks": True}],
                  "USE_STACKS_FOR_CHARGES procs refresh the aura; the last stack leaves a zero-stack aura",
                  note="Fingers of Frost: ModStackAmount(-1) per proc, removed with AURA_REMOVE_BY_DEFAULT at 0"),
        _timeline(ctx, 320137, [{"t": 0, "op": "apply"}, {"t": 1000, **re}],
                  "stack count is monotone under reapplication; stacks never exceed capacity",
                  requested=S.initial_stacks(doses_mod=S.apply_spell_mod(1, 1, 1.0, "int32")),
                  note="old Stormkeeper (capacity 0) with Doses +1 (1264863): created with 2 stacks, the next reapply "
                       "drops it to 1 and (1 < 2) does not refresh"),
        _timeline(ctx, 192081, [{"t": 0, "op": "apply"}, {"t": 1000, **re}, {"t": 2000, **re}],
                  "capacity-1 auras cannot hold more than one stack; each application keeps its own duration",
                  mods={"MaxAuraStacks": (19, 1.0)},
                  note="Ironfur + Ursine Adept: Trinity keeps one aura, stacks 1->2->3, one refreshed duration (Retail "
                       "Ironfur applications are believed independent -- AL-X-C-04)"),
    ]


def charges_corpus(ctx, command: str) -> dict[str, Any]:
    cens = _census_facts(ctx)
    pops, facts = cens["pops"], cens["facts"]
    fams: dict[str, set[int]] = defaultdict(set)
    for s, f in facts.items():
        fams[f["charge"]["family"]].add(s)
    families = {k: {"doc": C.FAMILY_DOC[k], "count": _pop_counts(v, pops), "witnesses": _witness(ctx, v, pops, 12)}
                for k, v in sorted(fams.items())}
    dist = {p: dict(sorted(Counter(facts[s]["charge"]["initial_charges_no_mods"] for s in facts
                                   if s in pops[p]).items())) for p in POPS}
    raw_over = [{"spell": s, "name": ctx.name(s), "db2_proc_charges": facts[s]["charge"]["db2_proc_charges"],
                 "uint8": facts[s]["charge"]["initial_charges_no_mods"]}
                for s in sorted(facts) if facts[s]["charge"]["db2_proc_charges"] > 255]
    origin = {p: dict(sorted(Counter((facts[s]["charge"]["proc_entry"] or {}).get("origin", "none")
                                     for s in facts if s in pops[p] and facts[s]["charge"]["initial_charges_no_mods"])
                             .items())) for p in POPS}
    both = sorted(s for s in facts if facts[s]["charge"]["initial_charges_no_mods"] and facts[s]["charge"]["spell_charge_category"])
    attr7_no_charges = sorted(s for s in facts if facts[s]["charge"]["dispel_removes_charges"]
                              and not facts[s]["charge"]["initial_charges_no_mods"])
    stacks_charges = sorted(s for s in facts if facts[s]["charge"]["use_stacks_for_charges"])
    mods = [m for m in cens["spellmods"] if m["op"] == "ProcCharges"]
    mod_rows = []
    for m in mods:
        tg = cens["spellmod_targets"].get((m["spell"], m["index"]), [])
        rows = []
        for t in tg:
            if t not in facts:
                continue
            base = facts[t]["charge"]["initial_charges_no_mods"]
            entry = facts[t]["charge"]["proc_entry"]
            raw = facts[t]["charge"]["db2_proc_charges"] if entry is None else entry["charges"]
            if m["kind"] in ("flat", "label_flat"):
                modded = C.calc_max_charges(facts[t]["charge"]["db2_proc_charges"],
                                            entry["charges"] if entry else None, (int(m["value"]), 1.0))
                rows.append({"target": t, "name": ctx.name(t), "base": base,
                             "after_mod": modded, "undefined_behaviour": raw + m["value"] < 0})
        mod_rows.append(dict(m, class_mask=f"0x{m['class_mask']:032X}", player=m["spell"] in pops["player"],
                             targets=rows, evidence=["db2-fact", "structural-inference"], consumer="SpellAuras.cpp:1012"))
    payload: dict[str, Any] = {
        "provenance": records.provenance(command, population_sizes={p: len(pops[p]) for p in POPS},
                                         missing_spellinfo=cens["missing"]),
        "families": families,
        "initial_charge_distribution": dist,
        "charge_entry_origin": origin,
        "raw_proc_charges_over_255": {"rows": raw_over, "evidence": ["db2-fact", "trinity-consumer"],
                                      "consumer": "SpellAuras.cpp:1014 (uint8)", "defect": "AL-D-C-03"},
        "stacks_as_charges": {"count": _pop_counts(stacks_charges, pops),
                              "rows": [{"spell": s, "name": ctx.name(s), "capacity": facts[s]["stack"]["capacity"],
                                        "dormant_charges": facts[s]["charge"]["initial_charges_no_mods"],
                                        "player": s in pops["player"]} for s in stacks_charges],
                              "consumer": "SpellAuras.cpp:1823-1826", "evidence": ["world-db-fact", "trinity-consumer"]},
        "aura_and_spell_charges": {"count": _pop_counts(both, pops), "witnesses": _witness(ctx, both, pops),
                                   "note": "aura proc charges and SpellCategories.ChargeCategory spell charges are "
                                           "unrelated state; no aura code reads ChargeCategory"},
        "dispel_removes_charges_without_charges": {
            "count": _pop_counts(attr7_no_charges, pops), "witnesses": _witness(ctx, attr7_no_charges, pops),
            "consumer": "Unit.cpp:4765-4767 (charges==0 -> not added to the dispel list)",
            "note": "ATTR7_DISPEL_REMOVES_CHARGES with no aura charges: never offered to a dispel (track E)"},
        "proc_charge_modifiers": mod_rows,
        "timelines": [
            _timeline(ctx, 360827, [{"t": 0, "op": "apply"}, {"t": 100, "op": "consume_proc"},
                                    {"t": 200, "op": "consume_proc"}],
                      "a negative ProcCharges modifier removes charges",
                      mods={"ProcCharges": (-16, 1.0)},
                      note="Blistering Scales + Regenerative Chitin: 255 charges (UB in Trinity; Retail unknown)"),
            _timeline(ctx, 135700, [{"t": 0, "op": "apply"}, {"t": 1000, "op": "consume_proc"}],
                      "charges survive to zero and the aura lingers",
                      note="Clearcasting 135700: 1 charge; PrepareProcChargeDrop 1->0 then ConsumeProcCharges removes"),
        ],
    }
    payload.update(_charge_records(_counts(cens)))
    records.validate_corpus(payload)
    return payload


def _counts(cens) -> dict[str, int]:
    pops, facts = cens["pops"], cens["facts"]
    player = pops["player"]
    sf = Counter(f["stack"]["family"] for f in facts.values())
    sfp = Counter(f["stack"]["family"] for s, f in facts.items() if s in player)
    cf = Counter(f["charge"]["family"] for f in facts.values())
    cfp = Counter(f["charge"]["family"] for s, f in facts.items() if s in player)
    multislot = sf["multislot-no-reapply-path"] + sf["multislot-dormant-capacity"]
    return {"with_info": len(facts), "non_multislot": len(facts) - multislot, "multislot": multislot,
            "multislot_player": sfp["multislot-no-reapply-path"] + sfp["multislot-dormant-capacity"],
            "unique": sf["single-no-refresh-unique"], "unique_player": sfp["single-no-refresh-unique"],
            "stacking": sf["stacking-shared-across-casters"] + sf["stacking-per-caster"], "cap1": sf["cap1-refresh"],
            "shared": sf["stacking-shared-across-casters"], "shared_player": sfp["stacking-shared-across-casters"],
            "per_caster": sf["stacking-per-caster"],
            "no_entry": cf["charges-without-proc-entry"], "no_entry_player": cfp["charges-without-proc-entry"],
            "use_stacks": cf["stacks-as-charges"] + cf["stacks-as-charges+dormant-charges"],
            "use_stacks_player": cfp["stacks-as-charges"] + cfp["stacks-as-charges+dormant-charges"],
            "dormant": cf["stacks-as-charges+dormant-charges"],
            "with_charges": sum(1 for f in facts.values() if f["charge"]["initial_charges_no_mods"]),
            "multislot_dormant_player": sfp["multislot-dormant-capacity"],
            "async": sum(1 for f in facts.values() if f["stack"]["async_stacking_attr"]),
            "async_player": sum(1 for s, f in facts.items() if s in player and f["stack"]["async_stacking_attr"]),
            "linked_multislot": len({r["child"] for r in cens["linked"]
                                     if r["child"] in facts and facts[r["child"]]["stack"]["multislot"]})}


# ---------------------------------------------------------------------------
# cross-cutting records
# ---------------------------------------------------------------------------

def _records(n: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    R = [
        ("initial-stacks-from-create-info",
         "A newly constructed aura holds uint8(max(1, AuraStackAmount)) stacks, where AuraStackAmount is 1, modified "
         "by the caster's Doses spell mods at Spell construction and replaced by a SPELLVALUE_AURA_STACK override; "
         "SpellAuraOptions.CumulativeAura is never read on this path",
         f"all providers with a SpellInfo ({n['with_info']:,})",
         ["Phalanx 1269312 (capacity 2, initial 1) kills 'initial = capacity'",
          "Sweeping Strikes 260708 + Improved Sweeping Strikes (Doses +6 -> 7) kills 'initial = 1'",
          "39 SPELLVALUE_AURA_STACK script sites (Evangelism 472433, Whirlwind 85739 via ApplyWhirlwindCleaveAura)"],
         "holds-on-census", ["trinity-consumer", "trinity-probe", "differential"]),
        ("capacity-caps-only-increases",
         "CalcMaxStackAmount (CumulativeAura, then MaxAuraStacks mods) caps ModStackAmount only when num > 0; a "
         "zero-capacity aura is pinned to 1; construction, SetStackAmount and decreases are uncapped",
         "all providers",
         ["old Stormkeeper 320137/350247/383009: capacity 0 + Doses +1 -> created with 2 stacks",
          "MODIFY_AURA_STACKS Set rows (21) bypass the cap", "capacity > 255 wraps (158 spells)",
          "Therazane's Resilience: max -90 -> 166 stacks"],
         "holds-on-census", ["trinity-consumer", "trinity-probe", "differential"]),
        ("reapply-adds-create-info-stack-amount",
         "A reapplication that reaches the refresh branch calls ModStackAmount(createInfo.StackAmount) -- the same "
         "Doses/override-modified amount as a fresh aura, not +1; multi-slot (every passive) and effect-mask "
         "mismatches never reach it",
         f"non-multislot providers ({n['non_multislot']:,} all)",
         [f"multislot families {n['multislot']:,} (player {n['multislot_player']:,}) never stack by reapplication",
          "Doses-modified casts add 1+Doses per reapply"],
         "holds-on-census", ["trinity-consumer", "trinity-probe"]),
        ("refresh-gate",
         "ModStackAmount refreshes (RefreshTimers + charges reset) iff the new count >= the old count and "
         "(capacity != 0 or neither ATTR1_AURA_UNIQUE nor ATTR5_AURA_UNIQUE_PER_CASTER); num == 0 refreshes",
         f"all providers; unique no-refresh family {n['unique']:,} (player {n['unique_player']:,}); unique ignored at capacity>0 (see corpus)",
         ["Doom 460553 (unique, capacity 0): reapply does not refresh",
          "Stormkeeper 320137 after Doses: reapply lowers 2 -> 1 and does not refresh",
          "HandleAuraLinked diff 0 -> ModStackAmount(0) refreshes the child"],
         "holds-on-census", ["trinity-consumer", "trinity-probe", "differential"]),
        ("periodic-reset-on-stack-hit",
         "A spell hit passes ResetPeriodicTimer = (capacity < 2) && !TRIGGERED_DONT_RESET_PERIODIC_TIMER; "
         "ATTR13 pandemic forces false inside RefreshTimers",
         f"capacity >= 2 non-multislot providers keep the periodic timer on reapply: {n['stacking']:,} all",
         [f"capacity 1 vs 2 differ: cap1-refresh {n['cap1']:,} reset, stacking families keep"],
         "holds-on-census", ["trinity-consumer", "trinity-probe"]),
        ("amount-scales-by-stacks",
         "AuraEffect::CalculateAmount multiplies every aura type's amount by the stack count unless the effect has "
         "SuppressPointsStacking (after the script CalcAmount hook, before rounding/clamp); periodic ticks also "
         "multiply the coefficient bonus by the live stack count",
         "provider aura effects (see effect_attributes)",
         ["SCHOOL_ABSORB / CC / MOD_* all scale -- no aura-type whitelist exists",
          "Earth Shield 974: every effect SuppressPointsStacking"],
         "holds-on-census", ["trinity-consumer"]),
        ("stack-change-recalculates",
         "Every SetStackAmount (hence every non-removing ModStackAmount) re-runs HandleAuraSpecificMods and "
         "ChangeAmount(CalculateAmount, mark=false, onStackOrReapply=true) on every effect -- a REAPPLY handler pass "
         "even when the amount is unchanged, ignoring m_canBeRecalculated",
         "all providers", ["AURA_UNIQUE no-refresh reapply still recalculates amounts",
                           "removal-side handler code runs on every SetStackAmount: track H AL-D-H-01"],
         "holds-on-census", ["trinity-consumer", "trinity-probe"]),
        ("aura-points-stack-accumulates-base",
         "On a refresh-branch hit, effects with AuraPointsStack add the new base points to the old m_baseAmount; "
         "others replace it; then the stack multiplication applies",
         "provider aura effects with AuraPointsStack (see effect_attributes)", [],
         "holds-on-census", ["trinity-consumer", "trinity-probe"]),
        ("shared-slot-across-casters",
         "Capacity >= 2, not channeled, no ATTR3_DOT_STACKING_RULE: the refresh branch looks the aura up with an "
         "empty caster GUID, so applications from any caster stack into the first caster's Aura",
         f"stacking-shared-across-casters {n['shared']:,} all / {n['shared_player']:,} player",
         [f"stacking-per-caster {n['per_caster']:,} (DOT_STACKING_RULE / channeled)"],
         "trinity-only", ["trinity-consumer", "trinity-probe"]),
        ("stacks-zero-removal",
         "ModStackAmount to <= 0 removes with the caller's remove mode; SetStackAmount(0) never removes (a live "
         "zero-stack aura with amount 0)",
         "all providers", ["2 MODIFY_AURA_STACKS Set-0 rows (451986 -> Stability 451179, 1255694 -> Scales 1253046)"],
         "trinity-only", ["trinity-consumer", "trinity-probe"]),
        ("client-applications-field",
         "The aura's single displayed count is stacks when IsUsingStacks (capacity > 0 or stacks > 1) else charges",
         "all providers", [f"CumulativeAura 0 vs 1 differ observably (cap1-refresh {n['cap1']:,})"],
         "holds-on-census", ["trinity-consumer"]),
        ("async-stacking-attribute-ignored",
         "SpellMisc Attributes_15 bit 0x400 (Trinity SPELL_ATTR15_UNK10; simc SX_ASYNCHRONOUS_STACKING_AURA) has no "
         "Trinity consumer: flagged auras keep one shared duration for all stacks, where simc expires stacks "
         "individually", f"{n['async']:,} providers / {n['async_player']:,} player",
         ["Unstable Affliction 1259790, Frenzy 335082, Between the Eyes 315341 (player)"],
         "trinity-only", ["db2-fact", "simc-consumer", "retail-unknown"]),
    ]
    rules = [{"id": f"AL-R-C-{i:02d}", "name": n, "definition": d, "population": p, "counterexamples": c,
              "status": s, "evidence": e} for i, (n, d, p, c, s, e) in enumerate(R, 1)]
    F = [
        ("AL-R-C-01", "initial stacks = CumulativeAura", "Phalanx 1269312: capacity 2, Aura::Aura reads createInfo.StackAmount (1)",
         "discarded"),
        ("AL-R-C-01", "initial stacks = 1", "Doses spell mods (21 rows; player targets Sweeping Strikes, Earth Shield, "
         "Dawnlight, Thunder Focus Tea, Stormkeeper, Unleash Life) and 39 SPELLVALUE_AURA_STACK script sites",
         "refined into AL-R-C-01"),
        ("AL-R-C-02", "stacks never exceed capacity", "cap-0 Stormkeeper + Doses (2 stacks); Set rows; uint8 wrap; "
         "negative max; probe confirms track A's AL-D-A-03 (create 7 on capacity 2 -> 7, next reapply clamps to 2 without "
         "refresh)", "discarded (Trinity); kept as AL-R-C-02 'caps only increases'"),
        ("AL-R-C-03", "reapplying adds exactly one stack", "adds createInfo.StackAmount; passives never stack", "refined"),
        ("AL-R-C-04", "every reapplication refreshes the duration", f"AURA_UNIQUE capacity 0 ({n['unique']:,}); lowered cap; "
         "decreasing reapply", "refined into the refresh gate"),
        ("AL-R-C-04", "a zero-change ModStackAmount is a no-op", "num == 0 satisfies new >= old: RefreshTimers + charge "
         "reset (linked-aura sync, HandleAuraLinked)", "discarded"),
        ("AL-R-C-06", "only periodic/stat aura types scale with stacks", "CalculateAmount multiplies after the "
         "type switch for every type unless SuppressPointsStacking", "discarded"),
        ("AL-R-C-11", "CumulativeAura 0 and 1 are equivalent", "capacity 1 ignores AURA_UNIQUE and makes IsUsingStacks "
         "true (displays stacks instead of charges)", "discarded"),
        ("AL-R-C-02", "a negative MaxAuraStacks modifier makes an aura non-stacking", "Therazane's Resilience on Earth "
         "Shield: probe stores uint8(-90) = 166", "discarded for Trinity; Retail unknown (AL-U-C-01)"),
        ("AL-R-C-01", "passive auras are immutable single-stack objects",
         f"{n['multislot_dormant_player']} player passives carry capacity > 0; 0 MODIFY_AURA_STACKS rows target a "
         f"multi-slot provider, but {n['linked_multislot']} SPELL_AURA_LINKED children are multi-slot and their stacks "
         "are synced by ModStackAmount on the parent's REAPPLY", "discarded: a passive aura's stacks can be mutated"),
        ("AL-R-C-09", "stacks are always per caster", "shared-slot family: key with empty caster GUID", "discarded"),
    ]
    fals = [{"id": f"AL-F-C-{i:02d}", "rule": r, "attempt": a, "result": res, "action": act}
            for i, (r, a, res, act) in enumerate(F, 1)]
    U = [
        ("negative MaxAuraStacks / Doses", "What does Retail do with Earth Shield / Water Shield under Therazane's "
         "Resilience (MaxAuraStacks -99, Doses -99)?", "Trinity: initial 1, reapply stores 166 stacks (probe)",
         "no Retail consumer; Trinity arithmetic is not evidence", "retail-unknown", "SpellAuras.cpp:1093-1106",
         "no client/server source", "AL-X-C-01 observed", False),
        ("negative ProcCharges modifier", "Does a ProcCharges result <= 0 mean 'no charges' in Retail (Regenerative "
         "Chitin on Blistering Scales)?", "Trinity: uint32(-1.0) is UB; gcc yields 255 charges",
         "UB + no Retail consumer", "retail-unknown", "SpellAuras.cpp:1004-1015; Player.cpp:22851",
         "no source", "AL-X-C-02 observed", False),
        ("stacks above 255", "Retail stack width for capacities 256..65000 (158 providers, none player)",
         "Trinity m_stackAmount is uint8", "no Retail consumer", "retail-unknown", "SpellAuras.h:238, SpellAuras.cpp:1056",
         "no source", "a Retail observation of any >255 stack aura", False),
        ("shared slot across casters", "Do capacity>=2 non-DOT_STACKING_RULE auras from two casters merge in Retail?",
         "Trinity merges into the first caster's aura; its own comment says 'TODO: Re-verify' (SpellInfo.cpp:1810)",
         "Trinity marks its own rule unverified", "retail-unknown", "SpellInfo.cpp:1808-1812, Unit.cpp:3401",
         "no source", "AL-X-C-03 observed", False),
        ("MaxAuraStacks-raised capacity-1 auras", "Does Ironfur with Ursine Adept keep independent per-application "
         "durations in Retail?", "Trinity: one aura, stacks share one refreshed duration",
         "no Retail consumer; Core has an independent-stack authority (navigation)", "retail-unknown",
         "SpellAuras.cpp:1093-1127", "no source", "AL-X-C-04 observed", False),
        ("SetStackAmount(0)", "Is a MODIFY_AURA_STACKS Set 0 a removal in Retail?", "Trinity keeps a zero-stack aura",
         "2 non-player rows only", "retail-unknown", "SpellEffects.cpp:6140-6142", "no source",
         "Retail observation of Stability 451179 after 451986", False),
        ("AURA_UNIQUE with capacity > 0", "Trinity ignores ATTR1_AURA_UNIQUE / ATTR5_AURA_UNIQUE_PER_CASTER for "
         "refresh when capacity != 0; is that Retail?", "SpellAuras.cpp:1114", "no Retail consumer", "retail-unknown",
         "SpellAuras.cpp:1114", "no source", "Retail recast of a unique stacking aura", False),
        ("Doses on reapplication", "Does Retail add 1+Doses stacks per reapplication (Trinity) or only on creation?",
         "Trinity: AuraStackAmount is modded per cast and passed to ModStackAmount", "no Retail consumer",
         "retail-unknown", "Spell.cpp:506, Unit.cpp:3441", "no source", "AL-X-C-05 observed", False),
        ("asynchronous stacking attribute", "Do auras with Attributes_15 0x400 expire stack by stack in Retail?",
         "Trinity ignores the bit (SPELL_ATTR15_UNK10); simc and Core treat it as per-stack deadlines",
         "the only Trinity-independent consumers are simc/Core (secondary)", ["retail-unknown", "simc-consumer"],
         "SharedDefines.h:1002; simc engine/buff/buff.cpp:777-779", "no Retail consumer", "AL-X-C-06 observed", False),
        ("proc charges vs stacks", "Are DB2 ProcCharges a separate counter (Trinity) or the initial stack count "
         "(simc) for auras that also have CumulativeAura?", "Trinity: separate m_procCharges; simc: initial_stacks() "
         "= ProcCharges", "two consumers disagree", ["retail-unknown", "simc-consumer"],
         "SpellAuras.cpp:498-499; simc engine/dbc/spell_data.hpp:564", "no Retail consumer", "AL-X-C-07 observed",
         False),
    ]
    unknowns = [{"id": f"AL-U-C-{i:02d}", "subject": s, "question": q, "known": k, "why_unresolved": w,
                 "evidence": e, "coords": c, "blocker": b, "reopen_condition": rc, "build_skew": bs}
                for i, (s, q, k, w, e, c, b, rc, bs) in enumerate(U, 1)]
    X = [
        ("Earth Shield stack count under Therazane's Resilience after two casts",
         [{"name": "trinity-wrap", "prediction": "166 applications after the second cast"},
          {"name": "no-stacks", "prediction": "0 or 1 applications, never increases"},
          {"name": "clamp-to-capacity", "prediction": "2 applications (capacity 9 ignoring the mod)"}],
         "Shaman with Earth Shield + Therazane's Resilience; cast Earth Shield on self twice",
         "UNIT_AURA applications of 974 / combat log", "AL-U-C-01 / AL-R-C-02", "exact", ["AL-U-C-01", "AL-D-C-02"]),
        ("Blistering Scales charges under Regenerative Chitin",
         [{"name": "trinity-ub-255", "prediction": "255 charges, removed after 255 hits"},
          {"name": "chargeless", "prediction": "no charge count; never consumed by hits"},
          {"name": "zero-clamp", "prediction": "aura removed at the first hit"}],
         "Evoker with Regenerative Chitin; apply Blistering Scales; take 16 melee hits", "aura applications / removal",
         "AL-U-C-02", "exact", ["AL-U-C-02", "AL-D-C-03"]),
        ("Two casters applying one capacity>=2 aura without DOT_STACKING_RULE",
         [{"name": "shared-slot", "prediction": "one aura, stacks sum, caster = first"},
          {"name": "per-caster", "prediction": "two auras, separate stacks and durations"}],
         "two players apply a shared-slot family aura (corpus witnesses) to one target",
         "aura list entries and applications", "AL-U-C-04", "approximate", ["AL-U-C-04", "AL-R-C-09"]),
        ("Ironfur with Ursine Adept: independent or shared duration",
         [{"name": "trinity-shared", "prediction": "one aura; each cast refreshes all stacks' duration"},
          {"name": "independent", "prediction": "each cast expires on its own timer"}],
         "Guardian druid with Ursine Adept; cast Ironfur at t=0 and t=3s; watch stack count over 10 s",
         "time-stamped applications count", "AL-U-C-05", "exact", ["AL-U-C-05"]),
        ("Doses added on reapplication",
         [{"name": "trinity-per-cast", "prediction": "Sweeping Strikes with Improved Sweeping Strikes gains 7 stacks per cast "
                                                     "(capped)"},
          {"name": "creation-only", "prediction": "7 on creation, +1 on refresh"}],
         "Warrior with Improved Sweeping Strikes; cast Sweeping Strikes, spend 2 stacks, recast",
         "applications before/after the recast", "AL-U-C-08", "approximate", ["AL-U-C-08"]),
        ("Per-stack expiry of an Attributes_15 0x400 aura",
         [{"name": "trinity-shared-duration", "prediction": "all stacks drop together when the last refresh expires"},
          {"name": "asynchronous", "prediction": "stacks drop one by one at their own application time + duration"}],
         "Warlock: apply Unstable Affliction stacks (1259790) at t=0, 2, 4 s without refreshing; watch applications",
         "time-stamped applications count", "AL-U-C-09", "exact", ["AL-U-C-09", "AL-R-C-12"]),
        ("Initial applications of a charges+capacity aura",
         [{"name": "trinity-separate", "prediction": "1 stack; tooltip/applications show stacks (IsUsingStacks)"},
          {"name": "simc-charges-are-stacks", "prediction": "applications start at min(ProcCharges, capacity)"}],
         "Brace For Impact 386029 (ProcCharges 5, capacity 3) -- or any charges_with_capacity witness", 
         "applications after the first application", "AL-U-C-10", "exact", ["AL-U-C-10"]),
    ]
    exps = [{"id": f"AL-X-C-{i:02d}", "question": q, "models": m, "setup": su, "observable": o,
             "discriminates": d, "fidelity": fi, "related": r} for i, (q, m, su, o, d, fi, r) in enumerate(X, 1)]
    D = [
        ("SpellAuras.cpp:1056 (uint8 SetStackAmount) via :1093-1127",
         "ModStackAmount caps against an int32 capacity up to 65000 but stores uint8: 255+1 -> 0 stacks",
         "a live aura with 0 stacks (amount 0), not removed; next increment restarts at 1",
         "reproduced (stacks.set_stack_amount u8; probe)"),
        ("SpellAuras.cpp:1099-1106", "negative CalcMaxStackAmount: an increasing ModStackAmount sets stackAmount = "
         "max (negative) without the <= 0 removal check (else-if), then stores uint8",
         "Earth Shield + Therazane's Resilience: 166 stacks, no refresh", "reproduced (probe-confirmed)"),
        ("Player.cpp:22851 / SpellAuras.cpp:1012-1014", "ApplySpellMod<uint32> converts a negative double to uint32 "
         "(undefined behaviour); CalcMaxCharges then truncates to uint8 (ProcCharges 999999 -> 63)",
         "Blistering Scales + Regenerative Chitin: 255 charges on gcc/x86-64", "reproduced as the gcc value; marked UB"),
        ("SpellEffects.cpp:6140-6142", "EffectModifyAuraStacks Set mode calls SetStackAmount without a <= 0 removal "
         "(Unit::SetAuraStack guards `if (stack)`, the effect does not)", "zero-stack live aura",
         "reproduced (set_stack_amount)"),
        ("SpellAuras.cpp:1068-1069", "SetStackAmount recalculates every effect with ChangeAmount(CalculateAmount) "
         "ignoring m_canBeRecalculated (absorb shields, fixed CC amounts reset on a stack/reapply)",
         "remaining absorb on refresh replaced by a fresh calculation", "reproduced (event recorded); likely-intended?"),
    ]
    defects = [{"id": f"AL-D-C-{i:02d}", "coords": c, "description": d, "lifecycle_effect": le,
                "oracle_behaviour": ob, "evidence": ["trinity-consumer", "trinity-probe"]}
               for i, (c, d, le, ob) in enumerate(D, 1)]
    K = [
        ("initial stack count", "crates/data/src/action/catalog/operation.rs:879-885",
         "AL-R-C-01", "Core aura mutations carry an explicit apply count validated count <= max_stacks; Trinity's "
         "initial count comes from Doses / SPELLVALUE_AURA_STACK and is not capped at construction",
         "a Core consumer of Doses or SPELLVALUE_AURA_STACK"),
        ("stack width", "crates/combat/src/aura_state.rs:155 (NonZeroU16 max_stacks)", "AL-D-C-01",
         "Core max stacks are u16 and non-zero; Trinity capacity 0 means 'non-stackable, pinned to 1' and storage is uint8",
         "Retail evidence on >255 stacks (AL-U-C-03)"),
        ("point stacking", "crates/combat/src/effect_point_stacking.rs:4-12", "AL-R-C-06",
         "Core EffectPointStacking::from_suppressed mirrors SuppressPointsStacking; Trinity applies it to every aura "
         "type plus the per-tick coefficient bonus", "a Core aura type that bypasses EffectPointStacking"),
        ("independent stacks", "crates/combat/src/independent_stack.rs:12-40", "AL-R-C-09 / AL-U-C-05",
         "Core has per-application stack cohorts with own deadlines; Trinity has none inside one Aura (only "
         "multi-slot passives or per-caster auras are independent)", "Retail result of AL-X-C-04"),
        ("asynchronous stacking", "crates/dbc/src/spell_attribute.rs:181, :491", "AL-R-C-12 / AL-U-C-09",
         "Core implements attribute 490 as independent per-application deadlines; Trinity has no consumer "
         "(SPELL_ATTR15_UNK10), so Trinity is not an oracle for these auras", "Retail result of AL-X-C-06"),
        ("aura stack mutation effect", "docs/research/core-semantic-boundary-audit.md CSA-K-01", "modify_aura_stacks",
         "raw 289 Set mode has no Core runtime op; Trinity Set never removes at 0", "a Core Set-stack op"),
    ]
    nav = [{"id": f"AL-K-C-{i:02d}", "topic": t, "core_coords": c, "research_ref": r, "observation": o,
            "reopen_condition": rc} for i, (t, c, r, o, rc) in enumerate(K, 1)]
    return {"rules": rules, "falsification": fals, "unknowns": unknowns, "retail_experiments": exps,
            "trinity_defects": defects, "core_navigation": nav}


def _charge_records(n: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    R = [
        ("initial-charges",
         "m_procCharges = uint8(CalcMaxCharges) at construction: spell_proc Charges (a 0 row falls back to DB2 "
         "ProcCharges at load) else DB2 ProcCharges, then ProcCharges spell mods; m_isUsingCharges = charges != 0",
         "all providers", ["spell_proc Charges 0 does not disable charges (SpellMgr.cpp:1576)"],
         "holds-on-census", ["trinity-consumer", "trinity-probe", "differential"]),
        ("proc-drop-needs-entry",
         "Only an aura with a SpellProcEntry can lose charges to procs (PrepareProcChargeDrop, then removal at 0 in "
         "ConsumeProcCharges); spell-mod charge auras are dropped through the same proc path",
         f"charges-without-proc-entry {n['no_entry']:,} all / {n['no_entry_player']:,} player never drop by the default proc path",
         ["Stormkeeper 191634: charges consumed by its script only"], "holds-on-census", ["trinity-consumer"]),
        ("use-stacks-for-charges",
         "PROC_ATTR_USE_STACKS_FOR_CHARGES (spell_proc) turns proc consumption into ModStackAmount(-1) and leaves "
         "m_procCharges untouched", f"{n['use_stacks']:,} all / {n['use_stacks_player']:,} player",
         [f"{n['dormant']:,} of them also have charges > 0: dormant charges still gate procs (IsUsingCharges && !GetCharges)"],
         "holds-on-census", ["world-db-fact", "trinity-consumer", "trinity-probe"]),
        ("charges-reset-on-refresh",
         "Every refreshing ModStackAmount resets charges to the live CalcMaxCharges; non-refreshing reapplications "
         "keep them", f"all providers with charges ({n['with_charges']:,} all)", ["AURA_UNIQUE capacity-0 auras keep spent charges"],
         "holds-on-census", ["trinity-consumer", "trinity-probe"]),
        ("mod-charges-inert-without-charges",
         "ModCharges is a no-op unless IsUsingCharges; it caps only increases and removes at <= 0",
         "all providers", ["ATTR7_DISPEL_REMOVES_CHARGES auras without charges are never offered to dispel"],
         "holds-on-census", ["trinity-consumer", "trinity-probe"]),
        ("charges-exist-by-live-mods",
         "Whether an aura uses charges is decided by the live CalcMaxCharges at construction (and each refresh), "
         "so ProcCharges spell mods can create charges on a DB2 charge-less aura or erase them",
         "11 ProcCharges modifier rows (see proc_charge_modifiers)",
         ["Wall of Steel 203261: Intervene 147833 0 -> 2 charges", "Arcane Dream 353360: Presence of Mind 205025 "
          "2 -> 0 (no charges) and 198154 0 -> uint32(-2) (UB) -> 254"],
         "holds-on-census", ["db2-fact", "trinity-consumer", "structural-inference"]),
    ]
    rules = [{"id": f"AL-R-C-{i:02d}", "name": n, "definition": d, "population": p, "counterexamples": c,
              "status": s, "evidence": e} for i, (n, d, p, c, s, e) in enumerate(R, 21)]
    F = [
        ("AL-R-C-21", "a spell_proc row with Charges 0 disables charges", "LoadSpellProcs fills 0 from DB2 ProcCharges",
         "discarded"),
        ("AL-R-C-22", "every aura with ProcCharges loses a charge per proc", f"{n['no_entry']:,} providers have no proc entry",
         "refined"),
        ("AL-R-C-23", "stacks and proc charges are one counter", "separate members; joined only by "
         "USE_STACKS_FOR_CHARGES and the refresh reset", "discarded"),
        ("AL-R-C-21", "aura charges and spell (category) charges interact", "no aura code reads ChargeCategory",
         "discarded"),
    ]
    fals = [{"id": f"AL-F-C-{i:02d}", "rule": r, "attempt": a, "result": res, "action": act}
            for i, (r, a, res, act) in enumerate(F, 21)]
    return {"rules": rules, "falsification": fals}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def _run(kind: str):
    def run(args) -> int:
        from . import context
        ctx = context.get()
        if args.census:
            cmd = f"aura_lifecycle.py {kind} --census --out docs/research/aura-lifecycle-corpora/{kind}.json"
            payload = stacks_corpus(ctx, cmd) if kind == "stacks" else charges_corpus(ctx, cmd)
        else:
            if args.spell is None:
                raise FailClosed(f"{kind}: a spell id or --census is required")
            payload = explain_stacks(ctx, args.spell) if kind == "stacks" else explain_charges(ctx, args.spell)
        emit(payload, args.out)
        return 0
    return run


COMMANDS = {
    "stacks": ("aura stack lifecycle of one spell, or --census", _add, _run("stacks")),
    "charges": ("aura proc-charge lifecycle of one spell, or --census", _add, _run("charges")),
}
