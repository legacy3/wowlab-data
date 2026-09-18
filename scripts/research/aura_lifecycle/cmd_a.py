"""Track A commands: ``identity`` and ``application``.

    aura_lifecycle.py identity <spell> [--vs <spell>]         identity signature (+ pairwise CanStackWith)
    aura_lifecycle.py identity --corpus --out <identity.json>  full census corpus
    aura_lifecycle.py application <spell>                      construction path of one spell
    aura_lifecycle.py application --corpus --out <application.json>
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import FailClosed
from .cli import emit

IDENTITY_CMD = "aura_lifecycle.py identity --corpus --out docs/research/aura-lifecycle-corpora/identity.json"
APPLICATION_CMD = "aura_lifecycle.py application --corpus --out docs/research/aura-lifecycle-corpora/application.json"

# Witnesses (current-player spells unless flagged); every claim is re-derived and asserted by the tests.
W_DOT_STACKING = (980, 774)                 # Agony, Rejuvenation
W_BUFF_TWO_CASTERS = (1126, 10060, 1160)    # Mark of the Wild, Power Infusion, Demoralizing Shout
W_GROUP_PAIRS = ((2823, 381664), (386164, 386208))   # lethal poisons (group 1501), stances (group 1502)
W_SHARED = (64844,)                         # Divine Hymn (stack>1 buff on others)
W_SPECIFIC_DEFECTS = ((48265, 48263), (111400, 108370))   # Death's Advance/VotTW, Burning Rush/Soul Leech
W_HAND = (1022, 1044)                       # Blessing of Protection / Freedom (same caster)
W_SHIELDS = (974, 52127)                    # Earth / Water Shield
W_DYNOBJ = (5740,)                          # Rain of Fire
W_AREA = (465, 1270083)                     # Devotion Aura (area + APPLY_AURA), Beacon of the Savior (area-only passive)
W_APPLICATION = (48181, 14914, 8936, 465, 980, 205022, 5740)


def _ctx():
    from . import context
    return context.get()


def _tools(ctx):
    from .identity import PropsBuilder, SpellGroups
    return PropsBuilder(ctx), SpellGroups.from_overlay(ctx)


def pair(pb, groups, new: int, existing: int, same_caster: bool) -> dict[str, Any]:
    from .identity import AuraObj, can_stack_with
    a = AuraObj(pb(new), "A", "T")
    b = AuraObj(pb(existing), "A" if same_caster else "B", "T")
    why: list[str] = []
    stack = can_stack_with(a, b, groups, trace=why)
    return {"new": new, "existing": existing, "same_caster": same_caster,
            "outcome": "coexist" if stack else "existing-removed", "why": why[-1],
            "specific": [pb(new).spell_specific, pb(existing).spell_specific]}


def props_summary(ctx, p) -> dict[str, Any]:
    return {"spell": p.spell, "name": p.name, "build_skew": ctx.is_skew(p.spell), "first_rank": p.first_rank,
            "family": p.family, "family_flags": f"0x{p.family_flags:032X}", "stack_amount": p.stack_amount,
            "passive": p.passive, "channeled": p.channeled, "dot_stacking_rule": p.dot_stacking_rule,
            "multislot": p.multislot, "one_slot_any_caster": p.one_slot_any_caster, "single_target": p.single_target,
            "max_targets": p.max_targets, "enchant_proc": p.enchant_proc, "spell_specific": p.spell_specific,
            "corrections": list(p.corrections),
            "effects": [{"index": e.index, "label": e.label(), "unit_owned": e.is_unit_owned_aura,
                         "trigger": e.trigger, "targets": [e.target_a, e.target_b]} for e in p.effects]}


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def _identity_args(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--vs", type=int, help="pairwise CanStackWith against another spell")
    p.add_argument("--corpus", action="store_true", help="write the full identity corpus")
    p.add_argument("--out")


def _identity(args) -> int:
    ctx = _ctx()
    pb, groups = _tools(ctx)
    if args.corpus:
        emit(identity_corpus(ctx, pb, groups), args.out)
        return 0
    if args.spell is None:
        raise FailClosed("identity: give a spell id or --corpus")
    from .identity import lookup_key, signature
    p = pb(args.spell)
    out = {"props": props_summary(ctx, p), "signature": signature(p, groups),
           "lookup_key": lookup_key(p, "<caster>", "<cast item>"),
           "groups": sorted(groups.spell_groups.get(p.first_rank, ()))}
    if args.vs is not None:
        out["vs"] = {"props": props_summary(ctx, pb(args.vs)),
                     "same_caster": [pair(pb, groups, args.spell, args.vs, True), pair(pb, groups, args.vs, args.spell, True)],
                     "other_caster": [pair(pb, groups, args.spell, args.vs, False), pair(pb, groups, args.vs, args.spell, False)],
                     "group_rule": groups.rule(p.first_rank, pb(args.vs).first_rank)}
    emit(out, args.out)
    return 0


def identity_corpus(ctx, pb, groups) -> dict[str, Any]:
    from . import records
    from .identity import TAXONOMY, census
    c = census(ctx, pb, groups)
    rows = c["rows"]
    from .providers import populations
    pops = populations(ctx)
    player = sorted(pops["player"])
    controlled = sorted(pops["controlled"])

    def spells_with(pred, pop) -> list[int]:
        return [s for s in pop if s in rows and pred(rows[s])]

    specific_all = Counter(pb(s).spell_specific for s in rows if pb(s).spell_specific != "NORMAL")
    player_specific = {str(s): pb(s).spell_specific for s in spells_with(lambda r: any(f.startswith("specific:") for f in r["flags"]), player)}
    group_members = []
    for s in sorted(rows):
        gs = sorted(groups.spell_groups.get(pb(s).first_rank, ()))
        if gs:
            group_members.append({"spell": s, "name": pb(s).name, "groups": gs, "player": s in pops["player"]})
    witnesses = identity_witnesses(ctx, pb, groups, rows)
    player_rows = {str(s): rows[s] for s in player}
    controlled_rows = {str(s): rows[s] for s in controlled}
    all_lists = {
        "enchant_proc": sorted(s for s in rows if "enchant-proc" in rows[s]["flags"]),
        "enchant_proc_derivation": {str(k): v for k, v in sorted(pb.enchant_proc_spells.items())},
        "ranked_providers": sorted(s for s in rows if "ranked" in rows[s]["flags"]),
        "corrected_providers": sorted(s for s in rows if "corrected" in rows[s]["flags"]),
        "zero_aura_apply_effects": sorted(s for s in rows if rows[s].get("zero_aura_effects")),
        "spell_specific_counts": dict(sorted(specific_all.items())),
    }
    payload = {
        "provenance": records.provenance(IDENTITY_CMD, population_note=(
            "all = providers.provider_spells (DIFFICULTY_NONE aura-producing spells); player = Scope.reach; "
            "controlled = controlled-unit-corpora/spells.json")),
        "taxonomy": TAXONOMY,
        "counts": c["counts"],
        "failed": {str(k): v for k, v in sorted(c["failed"].items())},
        "spell_groups": {"rules": c["group_rules"], "dropped": c["groups_dropped"], "members": group_members},
        "all_population_lists": all_lists,
        "player_spell_specific": player_specific,
        "player_rows": player_rows,
        "controlled_rows": controlled_rows,
        "witnesses": witnesses,
        "rules": IDENTITY_RULES(c["counts"]),
        "falsification": _fmt(IDENTITY_FALSIFICATION, multislot=c["counts"]["player"]["lookup"].get("multislot", 0),
                              replace=c["counts"]["player"]["other_caster"].get("replace", 0),
                              chain=c["counts"]["player"]["other_caster_why"].get("SpellAuras.cpp:1766 same rank chain", 0),
                              specific=len(player_specific), area_replace_before=16),
        "unknowns": IDENTITY_UNKNOWNS,
        "retail_experiments": IDENTITY_EXPERIMENTS,
        "trinity_defects": IDENTITY_DEFECTS,
        "core_navigation": CORE_NAVIGATION,
    }
    records.validate_corpus(payload)
    return payload


def _fmt(entries: list[dict[str, Any]], **values: Any) -> list[dict[str, Any]]:
    return [{k: (v.format(**values) if isinstance(v, str) else v) for k, v in e.items()} for e in entries]


def identity_witnesses(ctx, pb, groups, rows) -> list[dict[str, Any]]:
    from .identity import AuraObj, CreateInfo, try_refresh_stack_or_create
    out: list[dict[str, Any]] = []

    def w(kind: str, spells, detail: dict[str, Any], evidence) -> None:
        out.append({"kind": kind, "spells": list(spells), "names": [pb(s).name for s in spells],
                    "player": [s in ctx.scope.reach for s in spells], "build_skew": [ctx.is_skew(s) for s in spells],
                    "evidence": evidence, **detail})

    for s in W_DOT_STACKING:
        p = pb(s)
        w("dot-stacking-rule", [s], {"signature": rows[s], "stack_amount": p.stack_amount,
          "counterfactual_without_attr3": "any-caster shared object" if p.stack_amount > 1 else "coexist only if both periodic non-area",
          "coords": "SpellInfo.cpp:1808-1812; SpellAuras.cpp:1698-1699"}, ["db2-fact", "trinity-consumer"])
    for s in W_BUFF_TWO_CASTERS:
        owned: list = []
        first = try_refresh_stack_or_create(owned, CreateInfo(pb(s), "A", "T"), groups)
        second = try_refresh_stack_or_create(owned, CreateInfo(pb(s), "B", "T"), groups)
        w("non-stacking-buff-two-casters", [s], {"timeline": [first, second], "coords": "SpellAuras.cpp:1757-1766; Unit.cpp:3728-3729"},
          ["db2-fact", "trinity-consumer"])
    for a, b in W_GROUP_PAIRS:
        w("exclusive-spell-group", [a, b], {"group_rule": groups.rule(pb(a).first_rank, pb(b).first_rank),
          "groups": sorted(groups.spell_groups.get(pb(a).first_rank, set()) & groups.spell_groups.get(pb(b).first_rank, set())),
          "same_caster": pair(pb, groups, a, b, True), "other_caster": pair(pb, groups, a, b, False),
          "coords": "SpellMgr.cpp:447-485; SpellAuras.cpp:1673-1678"}, ["world-db-fact", "trinity-consumer"])
    for s in W_SHARED:
        owned = []
        steps = [try_refresh_stack_or_create(owned, CreateInfo(pb(s), c, "T"), groups) for c in ("A", "B")]
        w("one-slot-any-caster", [s], {"timeline": steps, "stack_amount": pb(s).stack_amount,
          "coords": "Unit.cpp:3390-3398; SpellInfo.cpp:1808-1812"}, ["db2-fact", "trinity-consumer"])
    for a, b in W_SPECIFIC_DEFECTS:
        w("legacy-spell-specific", [a, b], {"specific": [pb(a).spell_specific, pb(b).spell_specific],
          "same_caster": pair(pb, groups, a, b, True), "family_flags": [f"0x{pb(a).family_flags:032X}", f"0x{pb(b).family_flags:032X}"],
          "coords": "SpellInfo.cpp:2896, 2954; SpellAuras.cpp:1669-1671"}, ["db2-fact", "trinity-consumer", "structural-inference"])
    w("per-caster-spell-specific", list(W_HAND), {"same_caster": pair(pb, groups, *W_HAND, True),
      "other_caster": pair(pb, groups, *W_HAND, False), "coords": "SpellInfo.cpp:2926, 2093-2107"}, ["db2-fact", "trinity-consumer"])
    w("elemental-shields", list(W_SHIELDS), {"pairs": [pair(pb, groups, W_SHIELDS[0], W_SHIELDS[1], True),
      pair(pb, groups, W_SHIELDS[1], W_SHIELDS[0], True), pair(pb, groups, W_SHIELDS[0], W_SHIELDS[0], False)],
      "coords": "SpellInfo.cpp:2946; SpellAuras.cpp:1669-1671"}, ["db2-fact", "trinity-consumer"])
    from .identity import area_other_caster
    for s in W_AREA:
        w("area-second-owner", [s], {"signature": rows.get(s), "area": area_other_caster(pb(s), groups),
          "coords": "SpellAuras.cpp:732-745; Unit.cpp:3714-3731"}, ["db2-fact", "trinity-consumer"])
    for s in W_DYNOBJ:
        a = AuraObj(pb(s), "A", "T", kind="dynobj")
        b = AuraObj(pb(s), "A", "T", kind="dynobj")
        from .identity import can_stack_with
        why: list[str] = []
        same = can_stack_with(b, a, groups, trace=why)
        w("dynobj-per-cast", [s], {"signature": rows[s], "same_caster_second_cast_on_recipient": "coexist" if same else "older-removed",
          "why": why[-1]}, ["db2-fact", "trinity-consumer"])
    return out


def IDENTITY_RULES(counts: dict[str, Any]) -> list[dict[str, Any]]:
    pl, al = counts["player"], counts["all"]
    return [
        {"id": "AL-R-A-01", "name": "refresh lookup key",
         "definition": "A new unit-owned application refreshes an existing aura iff the target owns a non-multislot aura with the same SpellId, "
                       "the same caster unless IsStackableOnOneSlotWithDifferentCasters, the same cast item only with CU_ENCHANT_PROC, "
                       "and an identical aura effect mask. Difficulty, CastId, effect index and triggering spell are never keys.",
         "population": {"name": "all providers with unit-owned aura effects", "count": al["evaluated"]},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe", "differential"]},
        {"id": "AL-R-A-02", "name": "one shared object across casters",
         "definition": "StackAmount>1, not channeled, no ATTR3_DOT_STACKING_RULE -> all casters refresh/stack ONE object whose caster GUID stays the first applier "
                       "while every effect's base points become the LATEST applier's (Unit.cpp:3409-3419; += under AuraPointsStack): "
                       "amount = latest applier's base points x first caster's live bonuses; cast item also overwritten (3420-3429)",
         "population": {"name": "player providers", "count": pl["lookup"].get("any-caster", 0), "all": al["lookup"].get("any-caster", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "trinity-probe"]},
        {"id": "AL-R-A-03", "name": "multislot never refreshes",
         "definition": "IsPassive (or ids 55849/40075/44413) -> every application constructs a new object; same caster + same id + no cast item replaces the old one "
                       "(SpellAuras.cpp:1651-1652), otherwise coexists (1760-1761) unless IsPassiveStackableWithRanks skips no-stack removal. "
                       "With a cast item (item-sourced passives) 1651 is skipped and the same caster's copies COEXIST",
         "population": {"name": "player providers", "count": pl["lookup"].get("multislot", 0),
                        "item_sourced_candidates": pl["flags"].get("item-sourced", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-A-04", "name": "cross-caster outcome is decided by CanStackWith",
         "definition": "for per-caster target-held (non-area) spells a second caster's application coexists iff CanStackWith(new, existing) is true; "
                       "the deciding line distribution is in counts.*.other_caster_why",
         "population": {"name": "player providers without area-aura effects (per-caster + multislot)",
                        "count": pl["evaluated"] - pl["lookup"].get("any-caster", 0) - pl["flags"].get("area-aura", 0)},
         "counterexamples": [], "status": "holds-on-census", "evidence": ["trinity-consumer", "differential"]},
        {"id": "AL-R-A-05", "name": "dynamic-object auras are per cast",
         "definition": "PERSISTENT_AREA_AURA builds a new DynamicObject + DynObjAura per cast (no refresh); on a recipient, same caster + same spell replaces the older application, different casters coexist",
         "population": {"name": "player dynobj providers", "count": pl["dynobj"].get("dynobj-aura spells", 0), "all": al["dynobj"].get("dynobj-aura spells", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-A-11", "name": "area auras from a second owner are blocked, never replaced, on recipients",
         "definition": "an area aura is owned by its caster; a second owner's copy is admitted to a recipient only if CanStackWith holds against "
                       "every applied aura (SpellAuras.cpp:732-745), otherwise it is not added and nothing is removed. On the second owner itself "
                       "_ApplyAura removes the first owner's application (Unit.cpp:3714-3731) unless IsPassiveStackableWithRanks (coexist). Order dependent.",
         "population": {"name": "player providers with area-aura effects", "count": pl["flags"].get("area-aura", 0),
                        "all": al["flags"].get("area-aura", 0), "outcomes": {k: v for k, v in pl["other_caster"].items() if k.startswith("area:")},
                        "on_owner": pl["area_on_owner"]},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-A-12", "name": "item-sourced passives coexist for the same caster",
         "definition": "a multislot passive applied with a cast item skips the same-caster replace (SpellAuras.cpp:1651) and coexists (1760-1761); "
                       "player spells reachable only through gear/set/gem/enchant roots are evaluated this way (upper bound: cast-item propagation per route unproven)",
         "population": {"name": "player item-sourced passive candidates", "count": pl["same_caster_why"].get(
             "item-sourced: SpellAuras.cpp:1760-1761 multislot non-area same rank chain", 0),
                        "item_sourced_player_spells": pl["flags"].get("item-sourced", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "structural-inference"]},
        {"id": "AL-R-A-06", "name": "triggering link exempts from no-stack",
         "definition": "an aura never removes an aura whose spell triggers it or which it triggers (EffectTriggerSpell), checked before groups and SpellSpecific",
         "population": {"name": "rule over all pairs", "count": None}, "counterexamples": [], "status": "trinity-only",
         "evidence": ["trinity-consumer"]},
    ]


IDENTITY_FALSIFICATION = [
    {"id": "AL-F-A-01", "rule": "same SpellId + same caster always refreshes",
     "attempt": "census of multislot / effect-mask paths; probe create with mismatching mask",
     "result": "false for {multislot} player multislot (passive) spells and whenever the effect mask differs (Unit.cpp:3402-3406 recreate)",
     "action": "refined into AL-R-A-01 / AL-R-A-03"},
    {"id": "AL-F-A-02", "rule": "DoTs/HoTs from different casters always coexist",
     "attempt": "enumerate periodic spells with StackAmount>1 and no ATTR3_DOT_STACKING_RULE",
     "result": "such spells take the any-caster lookup (shared object) before CanStackWith is ever reached; periodic non-area coexistence only applies to per-caster spells",
     "action": "rule restricted; AL-R-A-02 added"},
    {"id": "AL-F-A-03", "rule": "different caster + same buff -> coexist (per-caster identity as in Core AuraKey)",
     "attempt": "CanStackWith over player providers with caster B vs A",
     "result": "{replace} player spells replace ({chain} via same-rank-chain fallthrough SpellAuras.cpp:1766, e.g. 1126 Mark of the Wild, 10060 Power Infusion, 1160 Demoralizing Shout)",
     "action": "kept as counterexample; see AL-K-A-01"},
    {"id": "AL-F-A-04", "rule": "the cast item is part of aura identity",
     "attempt": "probe: same spell, same caster, different cast item, CU_ENCHANT_PROC unset/set",
     "result": "unset: refresh and CastItemGUID overwritten (Unit.cpp:3420-3429); set: separate objects (lookup includes item, CanStackWith 1762-1764)",
     "action": "rule refined: item identity only for CU_ENCHANT_PROC (0 player providers)"},
    {"id": "AL-F-A-09", "rule": "a second caster's application of the same spell replaces the first (other_caster = replace) for every non-coexisting spell",
     "attempt": "R3-11 review: area auras are owned by their caster and reach others through the recipient map",
     "result": "false for area-aura providers: blocked at the recipient (SpellAuras.cpp:732-745), replace only on the second owner itself, coexist for "
               "IsPassiveStackableWithRanks area-only passives; before the fix {area_replace_before} player area providers were labelled replace",
     "action": "area outcome class added (AL-R-A-11); area providers excluded from replace"},
    {"id": "AL-F-A-10", "rule": "the same caster re-applying a passive replaces it",
     "attempt": "R1 review: item-sourced passives carry a cast item",
     "result": "false with a cast item (SpellAuras.cpp:1651 skipped -> 1760-1761 coexist); applied per spell via item-only reach",
     "action": "same_caster uses the cast-item outcome for item-sourced spells (AL-R-A-12)"},
    {"id": "AL-F-A-05", "rule": "SpellSpecific exclusivity only affects legacy spells",
     "attempt": "evaluate _LoadSpellSpecific over current-player providers",
     "result": "{specific} player spells classified (hardcoded ids reused: 48263/48265 PRESENCE; family-flag classes on Burning Rush 111400, Soul Leech 108370 (and 1311653, build skew), Demonic Circle: Teleport 48020)",
     "action": "recorded as AL-D-A-01/02"},
]

IDENTITY_UNKNOWNS = [
    {"id": "AL-U-A-01", "subject": "cross-caster raid buffs", "question": "Does a second caster's non-stacking buff (e.g. 1126 / 10060) replace the first caster's object or refresh/keep it?",
     "known": "Trinity removes the older object and creates the new one (remove callbacks, fresh duration)", "why_unresolved": "no Retail observation; DB2 has no identity field",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["SpellAuras.cpp:1757-1766", "Unit.cpp:3728-3729"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-A-01 result", "build_skew": False},
    {"id": "AL-U-A-02", "subject": "shared one-slot object caster", "question": "When caster B stacks onto caster A's shared aura, whose caster-dependent values (amount, mods, owner of procs) does Retail use?",
     "known": "Trinity keeps caster A's GUID but adopts B's base points (Unit.cpp:3409-3419); ModStackAmount recalculates amounts with GetCaster() = A", "why_unresolved": "no Retail observation",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["Unit.cpp:3390-3432", "SpellAuras.cpp:1056-1074"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-A-02 result", "build_skew": False},
    {"id": "AL-U-A-03", "subject": "SpellSpecific exclusivity on modern spells", "question": "Are HAND (1022/1044) and ELEMENTAL_SHIELD (974/52127) classes still mutually exclusive in Retail?",
     "known": "Trinity derives them from legacy family flags; no DB2 field encodes exclusivity", "why_unresolved": "no Retail observation",
     "evidence": ["trinity-consumer", "structural-inference", "retail-unknown"], "coords": ["SpellInfo.cpp:2803-2980", "SpellInfo.cpp:2061-2107"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-A-03 result", "build_skew": False},
    {"id": "AL-U-A-04", "subject": "effect-mask mismatch recreate", "question": "Is a partial-mask object (immunity-reduced AddAura) replaced or refreshed by a later full cast in Retail?",
     "known": "Trinity recreates (remove + create)", "why_unresolved": "rare, no observation", "evidence": ["trinity-consumer", "retail-unknown"],
     "coords": ["Unit.cpp:3402-3406", "Unit.cpp:12294-12306"], "blocker": "Retail observation", "reopen_condition": "none planned (low value)", "build_skew": False},
]

IDENTITY_EXPERIMENTS = [
    {"id": "AL-X-A-01", "question": "Two priests cast Power Infusion (10060) on one player 5 s apart",
     "models": [{"name": "trinity-replace", "prediction": "one aura remains, caster = second priest, remaining duration = full"},
                {"name": "keep-first", "prediction": "one aura remains, caster = first priest, duration unchanged"},
                {"name": "coexist", "prediction": "two aura entries"}],
     "setup": "two priests, one target (also: two druids with Mark of the Wild 1126), combat log + UnitAura source/expiration", "observable": "UnitAura(source, expirationTime), COMBAT_LOG SPELL_AURA_REMOVED/APPLIED/REFRESH",
     "discriminates": "replace vs keep-first vs coexist", "fidelity": "exact", "related": ["AL-U-A-01", "AL-R-A-04"]},
    {"id": "AL-X-A-02", "question": "Two casters apply a StackAmount>1 non-DOT_STACKING aura to one target (e.g. Divine Hymn 64844)",
     "models": [{"name": "trinity-shared", "prediction": "one aura, stacks = 2, source = first caster"},
                {"name": "per-caster", "prediction": "two auras with 1 stack each"}],
     "setup": "two priests channel Divine Hymn on the same group member", "observable": "UnitAura count/applications/sourceUnit",
     "discriminates": "shared-object vs per-caster identity", "fidelity": "exact", "related": ["AL-U-A-02", "AL-R-A-02"]},
    {"id": "AL-X-A-03", "question": "A paladin casts Blessing of Freedom then Blessing of Protection on the same ally",
     "models": [{"name": "trinity-HAND-exclusive", "prediction": "Freedom removed when Protection applies"},
                {"name": "independent", "prediction": "both present"}],
     "setup": "one paladin, one ally", "observable": "UnitAura list + SPELL_AURA_REMOVED for 1044", "discriminates": "SpellSpecific HAND exclusivity",
     "fidelity": "exact", "related": ["AL-U-A-03"]},
    {"id": "AL-X-A-04", "question": "A Blood DK with Veteran of the Third War (48263) uses Death's Advance (48265)",
     "models": [{"name": "trinity-PRESENCE", "prediction": "48263 removed (stamina bonus lost)"}, {"name": "independent", "prediction": "48263 stays"}],
     "setup": "Blood DK, check UnitAura / stamina before and after Death's Advance", "observable": "aura list, stamina stat",
     "discriminates": "legacy hardcoded SpellSpecific ids", "fidelity": "exact", "related": ["AL-D-A-01"]},
]

IDENTITY_DEFECTS = [
    {"id": "AL-D-A-01", "coords": ["SpellInfo.cpp:2954", "SpellInfo.cpp:2061-2078", "SpellAuras.cpp:1669-1671"],
     "description": "_LoadSpellSpecific hardcodes ids 48263/48265/48266 as SPELL_SPECIFIC_PRESENCE (WotLK presences); in 12.x 48263 is Veteran of the Third War (passive) and 48265 Death's Advance",
     "lifecycle_effect": "applying Death's Advance removes the Veteran of the Third War passive aura (exclusive, not per-caster); re-learning the passive removes Death's Advance",
     "oracle_behaviour": "reproduced (identity.can_stack_with -> existing-removed); not corrected"},
    {"id": "AL-D-A-02", "coords": ["SpellInfo.cpp:2896", "SpellAuras.cpp:1669-1671"],
     "description": "WARLOCK_ARMOR family-flag test (Flags[1] & 0x20000020 || Flags[2] & 0x10) matches modern warlock auras Burning Rush 111400 and Soul Leech 108370 (and 1311653, build skew)",
     "lifecycle_effect": "activating Burning Rush removes the Soul Leech auras (and vice versa)",
     "oracle_behaviour": "reproduced; not corrected"},
    {"id": "AL-D-A-03", "coords": ["SpellAuras.cpp:481", "SpellAuras.cpp:1099-1106"],
     "description": "Aura ctor stores createInfo.StackAmount unclamped (SpellModOp::Doses or SPELLVALUE_AURA_STACK above CumulativeAura), while ModStackAmount clamps on refresh",
     "lifecycle_effect": "initial stacks may exceed capacity on creation but not on refresh (asymmetry)", "oracle_behaviour": "reproduced in application.initial_state note; handed to track C"},
]

CORE_NAVIGATION = [
    {"id": "AL-K-A-01", "topic": "aura instance identity", "core_coords": ["crates/model/src/aura.rs:17 AuraKey(aura, source, affected)"],
     "research_ref": "identity.json counts.player.lookup / other_caster",
     "observation": "Core keys every aura instance per source actor; Trinity additionally has any-caster shared objects, multislot passives and cross-caster replacement (counts: identity.json counts.player)",
     "reopen_condition": "Core models multi-caster application of the same aura"},
]


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------

def _application_args(p) -> None:
    p.add_argument("spell", type=int, nargs="?")
    p.add_argument("--corpus", action="store_true")
    p.add_argument("--out")


def _application(args) -> int:
    ctx = _ctx()
    pb, groups = _tools(ctx)
    if args.corpus:
        emit(application_corpus(ctx, pb, groups), args.out)
        return 0
    if args.spell is None:
        raise FailClosed("application: give a spell id or --corpus")
    from .application import plan, require_unit_aura, timeline
    from procs.definition import ProcEntryStore
    p = pb(args.spell)
    if not (p.unit_aura_mask() or p.dynobj_aura_mask()):
        require_unit_aura(p)
    out = plan(ctx, p, ProcEntryStore(ctx.catalog, ctx.bundle.proc_overlay))
    out["timeline_new"] = timeline(_kinds(p))
    out["timeline_refresh"] = timeline(_kinds(p), existing=True)
    emit(out, args.out)
    return 0


def _kinds(p) -> list[dict[str, Any]]:
    from .application import DAMAGE_EFFECTS, HEAL_EFFECTS
    from .identity import APPLY_AURA, APPLY_AURA_ON_PET
    out = []
    for e in p.effects:
        if e.is_area_aura:
            kind = "area-aura"
        elif e.effect in (APPLY_AURA, APPLY_AURA_ON_PET):
            kind = "aura"
        elif e.effect in DAMAGE_EFFECTS:
            kind = "damage"
        elif e.effect in HEAL_EFFECTS:
            kind = "heal"
        else:
            kind = "other"
        out.append({"index": e.index, "kind": kind})
    return out


def application_corpus(ctx, pb, groups) -> dict[str, Any]:
    from procs.definition import ProcEntryStore

    from . import records
    from .application import DAMAGE_EFFECTS, HEAL_EFFECTS, plan, recipient_groups, same_cast_ordering, timeline
    from .providers import populations
    pops = populations(ctx)
    store = ProcEntryStore(ctx.catalog, ctx.bundle.proc_overlay)
    counts: dict[str, Any] = {}
    player_ordering: list[dict[str, Any]] = []
    for pop, members in sorted(pops.items()):
        c = Counter()
        for s in sorted(members):
            try:
                p = pb(s)
            except FailClosed:
                c["no DIFFICULTY_NONE SpellInfo (fail-closed)"] += 1
                continue
            unit = p.unit_aura_mask()
            if not unit:
                c["dynobj-only"] += 1
                continue
            c["unit-aura spells"] += 1
            o = same_cast_ordering(p)
            if o["damage_effects"] or o["heal_effects"]:
                c["with same-cast damage/heal"] += 1
                if o["damage_before_aura_index"]:
                    c["damage index < first aura index"] += 1
                if o["damage_after_aura_index"]:
                    c["damage index > first aura index"] += 1
                if o["damage_taken_modifier_effects"] and o["damage_effects"]:
                    c["damage + own damage-taken modifier"] += 1
                    if pop == "player":
                        player_ordering.append({"spell": s, "name": p.name, **o, "build_skew": ctx.is_skew(s)})
            if o["aura_created_at_effect"] is None:
                c["area-aura only (application deferred to UpdateTargetMap)"] += 1
            elif any(e.is_area_aura for e in p.effects):
                c["mixed APPLY_AURA + area aura"] += 1
            if len(recipient_groups(p)) > 1:
                c["aura effects with >1 implicit-target group (object carries effects for other recipients)"] += 1
            if any(e.is_unit_owned_aura and not e.aura for e in p.effects):
                c["APPLY_AURA with EffectAura 0"] += 1
            if p.stack_amount > 1:
                c["StackAmount>1 (initial stacks still 1 unless Doses/SPELLVALUE_AURA_STACK)"] += 1
            info = ctx.catalog.get(s)
            entry = store.lookup(s)
            if entry is not None and info is not None and int(entry.charges) != int(info.proc_charges):
                c["initial charges from spell_proc differ from ProcCharges"] += 1
            if (entry.charges if entry is not None else (info.proc_charges if info else 0)):
                c["initial charges > 0"] += 1
        counts[pop] = {"population": pop, "spells": len(members), **dict(sorted(c.items()))}
    witnesses = []
    for s in W_APPLICATION:
        p = pb(s)
        pl = plan(ctx, p, store)
        pl.pop("steps")
        witnesses.append({"player": s in ctx.scope.reach, "plan": pl,
                          "timeline_new": timeline(_kinds(p)), "timeline_refresh": timeline(_kinds(p), existing=True)})
    from .application import STEPS
    synthetic = {
        "damage_then_debuff": {"effects": [{"index": 0, "kind": "damage"}, {"index": 1, "kind": "aura"}],
                               "rules_out": "effect-index resolution (debuff applied before/with sibling damage)",
                               "timeline": timeline([{"index": 0, "kind": "damage"}, {"index": 1, "kind": "aura"}])},
        "debuff_then_damage": {"effects": [{"index": 0, "kind": "aura"}, {"index": 1, "kind": "damage"}],
                               "rules_out": "'aura effect index before damage index -> damage amplified by own debuff'",
                               "timeline": timeline([{"index": 0, "kind": "aura"}, {"index": 1, "kind": "damage"}])},
        "dead_target": {"timeline": timeline([{"index": 0, "kind": "aura"}], dead_target=True),
                        "rules_out": "'aura creation implies an application'"},
        "immune_effect": {"timeline": timeline([{"index": 0, "kind": "aura"}, {"index": 1, "kind": "aura"}], immune=frozenset({1})),
                          "rules_out": "'object effect mask == applied effect mask'"},
    }
    payload = {
        "provenance": records.provenance(APPLICATION_CMD),
        "steps": STEPS,
        "counts": counts,
        "player_damage_with_own_taken_modifier": player_ordering,
        "witnesses": witnesses,
        "synthetic_timelines": synthetic,
        "script_initial_stack_sites": SCRIPT_STACK_SITES,
        "rules": APPLICATION_RULES(counts),
        "falsification": APPLICATION_FALSIFICATION,
        "unknowns": APPLICATION_UNKNOWNS,
        "retail_experiments": APPLICATION_EXPERIMENTS,
    }
    records.validate_corpus(payload)
    return payload


SCRIPT_STACK_SITES = {
    "note": "SPELLVALUE_AURA_STACK overrides (initial stacks set by scripts); `rg -n SPELLVALUE_AURA_STACK src/server/scripts` at the pinned revision",
    "count": 39, "examples": ["spell_dk.cpp:797 Festering Wound", "spell_shaman.cpp:2010-2023 Maelstrom Weapon consumers",
                             "spell_priest.cpp:1626", "spell_warrior.cpp:149"], "evidence": "script-consumer", "handoff": "C, H"}


def APPLICATION_RULES(counts: dict[str, Any]) -> list[dict[str, Any]]:
    pl = counts["player"]
    return [
        {"id": "AL-R-A-07", "name": "object at first aura effect, effects applied after the hit",
         "definition": "For one target the aura object is created (or refreshed) at the first unit-owned aura effect in effect-index order, carrying AuraEffects for all "
                       "unit-owned aura effects; its effects become active (m_modAuras, handlers, OnEffectApply) only in DoDamageAndTriggers after the same spell's heal, damage and HIT-phase procs",
         "population": {"name": "player providers with unit-owned aura effects", "count": pl.get("unit-aura spells", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-A-08", "name": "same-cast damage never sees its own debuff",
         "definition": "direct damage/heal of a spell is computed at its effect index with the target's then-active auras and dealt before the spell's own aura is applied, regardless of index order",
         "population": {"name": "player spells with damage and a damage-taken modifier aura", "count": pl.get("damage + own damage-taken modifier", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
        {"id": "AL-R-A-09", "name": "initial stacks are not capacity",
         "definition": "a new aura starts with SpellValue AuraStackAmount (1 + Doses mods, or a script override), never CumulativeAura; creation does not clamp",
         "population": {"name": "player providers with StackAmount>1", "count": pl.get("StackAmount>1 (initial stacks still 1 unless Doses/SPELLVALUE_AURA_STACK)", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer", "script-consumer"]},
        {"id": "AL-R-A-10", "name": "area auras apply one owner update after the hit",
         "definition": "APPLY_AREA_AURA_* effects have no hit handler; the owner object exists at hit and every application (owner included) is created by the first UpdateTargetMap in the owner's next Aura::UpdateOwner, after that tick's duration decrement",
         "population": {"name": "player area-aura-only providers", "count": pl.get("area-aura only (application deferred to UpdateTargetMap)", 0)},
         "counterexamples": [], "status": "trinity-only", "evidence": ["trinity-consumer"]},
    ]


APPLICATION_FALSIFICATION = [
    {"id": "AL-F-A-06", "rule": "each effect resolves fully in index order (aura effect i active before effect i+1)",
     "attempt": "trace DoProcessTargetContainer / DoDamageAndTriggers call path", "result": "false: aura HandleEffect(REAL) runs in DoDamageAndTriggers after damage (Spell.cpp:2938 < 3040)",
     "action": "replaced by AL-R-A-07"},
    {"id": "AL-F-A-07", "rule": "one aura object per (spell, target) contains only the effects aimed at that target",
     "attempt": "read allAuraEffectMask construction (Spell.cpp:3241-3243)", "result": "false: every unit-owned aura effect is constructed on every recipient's object; the application mask selects",
     "action": "partial_failures entry"},
    {"id": "AL-F-A-08", "rule": "aura application exists as soon as the aura object exists",
     "attempt": "dead target / area aura / immune paths", "result": "false: dead target (Unit.cpp:3507-3510) and area-aura-only spells have an owned object with no application at hit",
     "action": "AL-R-A-10 + partial_failures"},
]

APPLICATION_UNKNOWNS = [
    {"id": "AL-U-A-05", "subject": "same-cast debuff ordering", "question": "Does Retail apply a spell's own damage-taken debuff before its direct damage (e.g. Haunt 48181)?",
     "known": "Trinity: never (AL-R-A-08)", "why_unresolved": "no Retail observation; DB2 effect order does not decide it",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["Spell.cpp:3980-3992", "SpellEffects.cpp:562-567", "Spell.cpp:3040"],
     "blocker": "Retail observation", "reopen_condition": "AL-X-A-05 result", "build_skew": False},
    {"id": "AL-U-A-06", "subject": "area aura first application", "question": "Is an area aura's first recipient set applied at hit or at the next aura update in Retail?",
     "known": "Trinity: next UpdateOwner (<= one map tick later), after one duration decrement", "why_unresolved": "sub-tick timing unobservable without combat-log timestamps",
     "evidence": ["trinity-consumer", "retail-unknown"], "coords": ["SpellEffects.cpp:127", "SpellAuras.cpp:837-842"],
     "blocker": "Retail observation", "reopen_condition": "track F / L experiment", "build_skew": False},
]

APPLICATION_EXPERIMENTS = [
    {"id": "AL-X-A-05", "question": "Haunt (48181) damage on a fresh target vs a target already carrying Haunt",
     "models": [{"name": "trinity-order", "prediction": "first Haunt hit not amplified by its own debuff; second hit amplified by the previous application"},
                {"name": "index-order", "prediction": "both hits amplified"}],
     "setup": "affliction warlock vs training dummy, fixed stats, compare direct damage of first and refresh casts", "observable": "combat log SPELL_DAMAGE amounts",
     "discriminates": "same-cast aura ordering", "fidelity": "approximate", "related": ["AL-U-A-05", "AL-R-A-08"]},
]


COMMANDS = {
    "identity": ("aura identity signature / pairwise stacking / census corpus (track A)", _identity_args, _identity),
    "application": ("aura construction path / ordering / census corpus (track A)", _application_args, _application),
}
