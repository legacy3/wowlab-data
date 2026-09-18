"""Track J commands: ``census``, ``signatures``, ``drift``.

``aura_lifecycle.py census --out docs/research/aura-lifecycle-corpora/provider-census.json``
``aura_lifecycle.py signatures --out docs/research/aura-lifecycle-corpora/lifecycle-signatures.json``
``aura_lifecycle.py drift --out docs/research/aura-lifecycle-corpora/drift.json``
``aura_lifecycle.py census --spell 980``      one provider's axes, signature ids, Core view

Every count names its population (``all`` / ``player`` / ``controlled``, see
:mod:`aura_lifecycle.providers`).  Census membership never implies executable support.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from . import PINS, TC_ROOT, FailClosed, census, records, signatures
from .cli import emit

CORPUS = "docs/research/aura-lifecycle-corpora/"
POPS = ("all", "player", "player+class-skills", "controlled")
MEMBER_POPS = ("player", "player+class-skills", "controlled")


# ---------------------------------------------------------------------------
# shared state
# ---------------------------------------------------------------------------


class State:
    """Context + census records, built once per process."""

    def __init__(self, ctx) -> None:
        from . import providers
        self.ctx = ctx
        self.pops = providers.populations(ctx, with_class_skills=True)
        self.h_flags = census.external_flags(ctx)
        self.records = census.build(ctx, flags=self.h_flags)
        self.by_spell = {r["spell"]: r for r in self.records}
        self.catalogs = signatures.core_catalogs()
        self.skew = frozenset(s for s in self.pops["all"] if ctx.is_skew(s))
        self.ids = {r["spell"]: signatures.record_ids(r) for r in self.records}
        self._recs: dict[str, list[dict[str, Any]]] = {}
        self._attr_table: list[dict[str, Any]] | None = None

    def recs(self, pop: str) -> list[dict[str, Any]]:
        if pop not in self._recs:
            p = self.pops[pop]
            self._recs[pop] = [r for r in self.records if r["spell"] in p]
        return self._recs[pop]


_STATE: State | None = None


def state() -> State:
    global _STATE
    if _STATE is None:
        from . import context
        _STATE = State(context.get())
    return _STATE


def _player_first(st: State, spells) -> list[int]:
    return sorted(spells, key=lambda s: (s not in st.pops["player"], s))


# ---------------------------------------------------------------------------
# provider-census.json
# ---------------------------------------------------------------------------


def attr_table(st: State) -> list[dict[str, Any]]:
    if st._attr_table is None:
        st._attr_table = _attr_table(st)
    return st._attr_table


def _attr_table(st: State) -> list[dict[str, Any]]:
    consumers = census.trinity_consumers(TC_ROOT, [a.trinity for a in census.LIFECYCLE_ATTRS])
    core_attrs = st.catalogs[1] if st.catalogs else {}
    out = []
    for a in census.LIFECYCLE_ATTRS:
        sites = consumers.get(a.trinity, []) if a.trinity else []
        counts = {}
        for p in POPS:
            if a.label == "PASSIVE":
                counts[p] = sum(1 for r in st.recs(p) if r["axes"]["passive"] is True)
            else:
                counts[p] = sum(1 for r in st.recs(p) if a.label in r["axes"]["attrs"])
        out.append({
            "label": a.label, "word": a.word, "bit": f"0x{a.bit:08X}", "trinity": a.trinity or None,
            "trinity_named": bool(a.trinity), "core_raw": a.core_raw,
            "core_support": core_attrs.get(a.core_raw, "unknown") if core_attrs else None,
            "trinity_consumers": sites, "trinity_consumer_count": len(sites),
            "topic_track": a.topic, "counts": counts,
            "evidence": ["db2-fact", "trinity-consumer" if sites else "retail-unknown", "core-navigation"],
            "witnesses_player": [r["spell"] for r in st.recs("player")
                                 if (r["axes"]["passive"] is True if a.label == "PASSIVE" else a.label in r["axes"]["attrs"])][:5],
        })
    return out


def derived_counts(st: State) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in POPS:
        c: Counter = Counter()
        wit: dict[str, list[int]] = defaultdict(list)
        for r in st.recs(p):
            for k, v in census.derived_predicates(r).items():
                if v:
                    c[k] += 1
                    if len(wit[k]) < 5:
                        wit[k].append(r["spell"])
        out[p] = {"counts": dict(sorted(c.items())), "witnesses": dict(sorted(wit.items()))}
    return out


def core_summary(st: State) -> dict[str, Any]:
    if not st.catalogs:
        return {"available": False}
    out: dict[str, Any] = {"available": True, "evidence": "core-navigation",
                           "definition": signatures.core_view.__doc__.strip()}
    for p in POPS:
        c: Counter = Counter()
        blk: Counter = Counter()
        sub: Counter = Counter()
        wit: list[int] = []
        for r in st.recs(p):
            v = signatures.core_view(r, st.catalogs)
            c["catalog_admissible"] += v["catalog_admissible"]
            c["lifecycle_only_blocked"] += v["lifecycle_only_blocked"]
            c["subtypes_all_implemented"] += v["subtypes"] == ["implemented"]
            sub["+".join(v["subtypes"])] += 1
            if v["lifecycle_only_blocked"]:
                blk.update(v["lifecycle_blockers"])
                if len(wit) < 10:
                    wit.append(r["spell"])
        out[p] = {"spells": dict(sorted(c.items())), "subtype_support_mix": dict(sorted(sub.items())),
                  "lifecycle_only_blockers": dict(sorted(blk.items())), "lifecycle_only_witnesses": wit}
    return out


def external_summary(st: State) -> dict[str, Any]:
    """Externally touched lifecycle surfaces per population (Track H's single definition)."""
    per = {}
    for p in POPS:
        surf: Counter = Counter()
        pol: Counter = Counter()
        n = 0
        for r in st.recs(p):
            if r["axes"]["external"]:
                n += 1
            for x in r["axes"]["external"]:
                k, v = x.split(":", 1)
                surf[k] += 1
                pol[v] += 1
        per[p] = {"spells_touched": n, "surfaces": dict(sorted(surf.items())), "policy_sources": dict(sorted(pol.items()))}
    return {"source": "aura_lifecycle.overlays.surface_flags (Track H); axis value = sorted 'surface:policy'",
            "caveat": "absence of a touch is not proof of absence (overlays module docstring coverage list)",
            "counts": per, "evidence": ["world-db-fact", "script-consumer", "trinity-consumer"]}


def name_fragmentation(st: State, pop: str) -> dict[str, Any]:
    """How many spell names map to more than one ``full`` signature (group-by-name falsifier)."""
    by_name: dict[str, set[str]] = defaultdict(set)
    for r in st.recs(pop):
        by_name[str(st.ctx.name(r["spell"]))].add(st.ids[r["spell"]]["full"])
    multi = sorted((n for n, s in by_name.items() if len(s) > 1), key=lambda n: (-len(by_name[n]), n))
    return {"population": pop, "names": len(by_name), "names_with_multiple_signatures": len(multi),
            "examples": [{"name": n, "signatures": len(by_name[n])} for n in multi[:10]]}


def spell_row(st: State, spell: int) -> dict[str, Any]:
    r = st.by_spell.get(spell)
    if r is None:
        raise FailClosed(f"spell {spell} is not a DIFFICULTY_NONE aura provider")
    row = {"spell": spell, "name": str(st.ctx.name(spell)), "build_skew": spell in st.skew,
           "populations": [p for p in POPS if spell in st.pops[p]],
           "axes": r["axes"], "effects": r["effects_exact"], "provider_indices": r["provider_indices"],
           "duration_ms": r["duration_ms"], "stack_amount": r["stack_amount"], "proc_charges": r["proc_charges"],
           "derived": census.derived_predicates(r), "signature_ids": st.ids[spell]}
    if st.catalogs:
        row["core"] = signatures.core_view(r, st.catalogs)
    return row


def census_payload(st: State) -> dict[str, Any]:
    from . import providers
    effects = providers.provider_effects(st.ctx.data)
    rules_eval = census.evaluate_rules(st.records, st.pops, skew=st.skew)
    payload: dict[str, Any] = {
        "provenance": records.provenance(f"aura_lifecycle.py census --out {CORPUS}provider-census.json"),
        "track": "J",
        "caveat": "census membership never implies executable support; Core columns are catalog navigation only",
        "populations": {p: {"provider_spells": len(st.pops[p]),
                            "provider_effects": sum(1 for e in effects if e["spell"] in st.pops[p]),
                            "build_skew_spells": sum(1 for s in st.pops[p] if s in st.skew)} for p in POPS},
        "axes": {"spell": list(census.SPELL_AXES), "effect": list(census.EFFECT_AXES),
                 "doc": census.__doc__.split("Axis vocabulary", 1)[1].strip()},
        "lifecycle_attributes": attr_table(st),
        "marginals": {p: census.marginals(st.recs(p)) for p in POPS},
        "derived_predicates": derived_counts(st),
        "external_surfaces": external_summary(st),
        "core": core_summary(st),
        "name_fragmentation": {p: name_fragmentation(st, p) for p in ("all", "player")},
        "rule_evaluation": rules_eval,
        "spells": [spell_row(st, s) for s in sorted(st.pops["player"] | st.pops["player+class-skills"]
                                                    | st.pops["controlled"])],
        "spells_population": "player | player+class-skills | controlled (all-population rows are regenerable with --spell)",
    }
    payload.update(cross_cutting(st, rules_eval))
    records.validate_corpus(payload)
    return payload


# ---------------------------------------------------------------------------
# cross-cutting lists
# ---------------------------------------------------------------------------


def _rule_status(per: dict[str, Any]) -> str:
    if per["all"]["counterexamples"] == 0:
        return "holds-on-census"
    if per["player"]["counterexamples"] == 0:
        return "refined"
    return "discarded"


def cross_cutting(st: State, rules_eval: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rules, fals = [], []
    for i, r in enumerate(rules_eval, 1):
        per = r["per_population"]
        status = _rule_status(per)
        rid = f"AL-R-J-{r['suffix']}"
        rules.append({
            "id": rid, "name": r["name"], "definition": r["definition"],
            "population": {p: per[p]["population"] for p in POPS},
            "counterexamples": {p: {"count": per[p]["counterexamples"], "witnesses": per[p]["witnesses"],
                                    "build_skew": per[p]["counterexamples_build_skew"]} for p in POPS},
            "population_build_skew": {p: per[p]["population_build_skew"] for p in POPS},
            "status": status, "evidence": ["db2-fact", "structural-inference"],
            "note": "holds-on-census on 'all' is a data regularity, not a semantic rule; "
                    "'refined' = holds on player but not on all",
        })
        fals.append({"id": f"AL-F-J-{i:02d}", "rule": rid,
                     "attempt": f"search every provider spell (all/player/controlled) for a counterexample to: {r['definition']}",
                     "result": {p: per[p]["counterexamples"] for p in POPS},
                     "action": {"holds-on-census": "kept", "refined": "kept for player population only; all-population counterexamples recorded",
                                "discarded": "discarded; counterexamples in the player population"}[status]})
    n = len(fals)
    frag = name_fragmentation(st, "player")
    fals.append({"id": f"AL-F-J-{n + 1:02d}", "rule": "group-by-name",
                 "attempt": "group player providers by spell name and check whether one name maps to one full lifecycle signature",
                 "result": f"{frag['names_with_multiple_signatures']} of {frag['names']} player names map to >1 full signature",
                 "action": "grouping by name rejected; signatures are the grouping key"})
    fals.append({"id": f"AL-F-J-{n + 2:02d}", "rule": "signature-id-stability",
                 "attempt": "recompute signature ids from shuffled record order and from a round-tripped JSON signature "
                            "(tests/test_al_j_signatures.py)",
                 "result": "ids identical (derived from canonical tuple only)", "action": "kept"})

    attrs = {a["label"]: a for a in attr_table(st)}
    unknowns = []

    def u(subject, question, known, why, evidence, coords, blocker, reopen, skew=False):
        unknowns.append({"id": f"AL-U-J-{len(unknowns) + 1:02d}", "subject": subject, "question": question,
                         "known": known, "why_unresolved": why, "evidence": evidence, "coords": coords,
                         "blocker": blocker, "reopen_condition": reopen, "build_skew": skew})

    fam = {p: Counter(r["axes"]["duration"] for r in st.recs(p)) for p in POPS}
    if fam["all"]["finite-varying"]:
        u("finite-varying durations",
        "How does a provider whose SpellDuration has Duration != MaxDuration and DurationPerResource == 0 pick its duration?",
        f"finite-varying providers: all {fam['all']['finite-varying']}, player {fam['player']['finite-varying']}; "
        "Trinity CalcSpellDuration interpolates only via DurationPerResource and combo points",
        "no consumer interpolates without a per-resource step", ["db2-fact", "trinity-consumer", "retail-unknown"],
        ["TrinityCore/src/server/game/Entities/Object/Object.cpp:1707"], "no Retail observation",
        "Track B / L experiment observing such an aura's duration")
    client_only = [a for a in census.LIFECYCLE_ATTRS if not a.trinity]
    u("client-named lifecycle attributes Trinity leaves UNK",
      "What lifecycle behaviour do " + ", ".join(a.label for a in client_only) + " select?",
      "; ".join(f"{a.label} (attr{a.word} {a.bit:#x}, Core raw {a.core_raw}): all {attrs[a.label]['counts']['all']}, "
                f"player {attrs[a.label]['counts']['player']}" for a in client_only),
      "pinned Trinity names these bits SPELL_ATTRn_UNKm and never reads them; only the client/Core name exists",
      ["db2-fact", "core-navigation", "retail-unknown"],
      ["TrinityCore/src/server/game/Miscellaneous/SharedDefines.h", "core/crates/dbc/src/spell_attribute.rs"],
      "no consumer at the pin", "a newer Trinity/simc consumer, or the Retail experiments AL-X-J-01/02")
    nyi = [a for a in census.LIFECYCLE_ATTRS if a.trinity and attrs[a.label]["trinity_consumer_count"] == 0]
    u("Trinity-named lifecycle attributes with no consumer",
      "Do " + ", ".join(a.label for a in nyi) + " change aura lifetime in Retail?",
      "; ".join(f"{a.label}: all {attrs[a.label]['counts']['all']}, player {attrs[a.label]['counts']['player']}" for a in nyi),
      "no non-comment reference outside SharedDefines.h / enuminfo in the pinned tree (some tagged NYI)",
      ["trinity-consumer", "retail-unknown"], ["TrinityCore/src/server/game/Miscellaneous/SharedDefines.h:467,496,497"],
      "Trinity implements nothing to read", "Retail observation; Track E for IGNORE_OWNERS_DEATH")
    pvp = {p: sum(1 for r in st.recs(p) if r["axes"]["pvp_duration"]) for p in POPS}
    u("PvP duration index",
      "When does SpellMisc.PvPDurationIndex replace the base duration for an aura?",
      f"providers with a PvP duration: all {pvp['all']}, player {pvp['player']}; Trinity never reads the column",
      "no Trinity consumer; PvE simulation may never need it", ["db2-fact", "retail-unknown"],
      ["TrinityCore/src/server/game/Spells/SpellInfo.cpp:1337-1372"], "no consumer",
      "Track B decides whether PvP duration is in scope")
    skew = {p: sum(1 for s in st.pops[p] if s in st.skew) for p in POPS}
    u("build-skewed providers",
      "Do providers newer than Trinity's supported build follow the lifecycle of their signature peers?",
      f"skewed providers: all {skew['all']}, player {skew['player']}, controlled {skew['controlled']}",
      "pinned Trinity supports client <= 12.0.7.68453; newer spells have no scripts/overlays/corrections authored",
      ["build-skew", "structural-inference"], ["docs/research/dummy-corpora/build-skew.json"],
      "build skew", "a Trinity revision supporting 12.1.x", True)
    u("abs-negative duration entries",
      "Why does a SpellDuration row carry a negative non-sentinel duration, and what does Retail do with it?",
      f"abs-negative providers: all {fam['all']['abs-negative']}, player {fam['player']['abs-negative']}; Trinity takes abs()",
      "client/Retail meaning unknown", ["db2-fact", "trinity-consumer", "retail-unknown"],
      ["TrinityCore/src/server/game/Spells/SpellInfo.cpp:3986-3998"], "no Retail observation", "Track B")

    uid = {"client-named": next(x["id"] for x in unknowns if x["subject"].startswith("client-named")),
           "no-consumer": next(x["id"] for x in unknowns if x["subject"].startswith("Trinity-named"))}

    def w(label: str) -> list[int]:
        return attrs[label]["witnesses_player"] or [r["spell"] for r in st.records if label in r["axes"]["attrs"]][:3]

    experiments = [
        {"id": "AL-X-J-01", "question": "Does AURA_DOES_NOT_REFRESH (attr15 0x200) make a reapplication a no-op on duration?",
         "models": [{"name": "trinity-default", "prediction": "reapplication refreshes duration (RefreshDuration)"},
                    {"name": "client-name", "prediction": "reapplication leaves the remaining duration unchanged"},
                    {"name": "core-catalog", "prediction": "active reapplication applies and caps stacks but preserves "
                     "application time and expiry deadline (core/crates/dbc/src/spell_attribute.rs, AuraDoesNotRefresh raw 489)"}],
         "setup": f"apply a witness ({w('AURA_DOES_NOT_REFRESH')[:3]}) twice on one target mid-duration",
         "observable": "remaining duration after the second application (aura tooltip / UNIT_AURA)",
         "discriminates": "refresh vs no-refresh", "fidelity": "approximate", "related": [uid["client-named"]]},
        {"id": "AL-X-J-02", "question": "Does ASYNCHRONOUS_STACKING_BUFF (attr15 0x400) give each stack its own expiry?",
         "models": [{"name": "shared-timer", "prediction": "all stacks expire together; each application refreshes"},
                    {"name": "per-stack-timer", "prediction": "stacks fall off one by one at their own deadlines "
                     "(Core's catalog text for AsynchronousStackingBuff raw 490 adopts this for finite non-periodic carriers)"}],
         "setup": f"gain two stacks of a witness ({w('ASYNCHRONOUS_STACKING_BUFF')[:3]}) seconds apart, stop re-triggering",
         "observable": "stack count over time until 0",
         "discriminates": "single aura timer vs per-application timers", "fidelity": "exact", "related": [uid["client-named"]]},
        {"id": "AL-X-J-03", "question": "Does IGNORE_OWNERS_DEATH keep an aura on a unit after its owner dies?",
         "models": [{"name": "trinity-nyi", "prediction": "no effect (owner death handled as for any aura)"},
                    {"name": "client-name", "prediction": "aura survives owner death"}],
         "setup": f"pet/guardian with a witness aura ({w('IGNORE_OWNERS_DEATH')[:3]}); kill the owner",
         "observable": "aura presence on the controlled unit after owner death",
         "discriminates": "NYI vs honoured", "fidelity": "insufficient", "related": [uid["no-consumer"]]},
    ]

    core_nav = []
    if st.catalogs:
        cs = core_summary(st)

        def k(topic, coords, ref, obs, reopen):
            core_nav.append({"id": f"AL-K-J-{len(core_nav) + 1:02d}", "topic": topic, "core_coords": coords,
                             "research_ref": ref, "observation": obs, "reopen_condition": reopen})
        k("lifecycle-only blockers", "core/crates/dbc/src/spell_attribute.rs; core/crates/dbc/src/aura_subtype.rs",
          "provider-census.json core",
          f"player providers whose subtypes are all Core-implemented but carry a disabled/unimplemented/uncatalogued "
          f"lifecycle attribute: {cs['player']['spells']['lifecycle_only_blocked']} "
          f"(blockers {cs['player']['lifecycle_only_blockers']})",
          "Core changes the support class of one of these attributes")
        k("DOT_STACKING_RULE catalog class", "core/crates/dbc/src/spell_attribute.rs (DotStackingRule raw 103: ignored)",
          "AL-R-J-05; Track A",
          f"Core marks the attribute ignored while Trinity reads it in Aura::CanStackWith "
          f"(SpellAuras.cpp:1698); carriers: player {attrs['DOT_STACKING_RULE']['counts']['player']}, all {attrs['DOT_STACKING_RULE']['counts']['all']}",
          "Track A settles the observable stacking consequence")
        k("narrow 'implemented' attribute rows", "core/crates/dbc/src/spell_attribute.rs (HasteAffectsDuration raw 273)",
          "lifecycle-signatures.json core columns",
          f"Core's implemented text for raw 273 admits one spell (391428); census carriers: player "
          f"{attrs['HASTE_AFFECTS_DURATION']['counts']['player']}, all {attrs['HASTE_AFFECTS_DURATION']['counts']['all']}; "
          "catalog_admissible is therefore an upper bound",
          "Core generalizes the row")
        k("client-named attributes Core implements without a Trinity oracle",
          "core/crates/dbc/src/spell_attribute.rs (AuraDoesNotRefresh raw 489, AsynchronousStackingBuff raw 490: implemented)",
          f"{uid['client-named']}; AL-X-J-01/02",
          f"Core declares these implemented while pinned Trinity never reads the bits (SPELL_ATTR15_UNK9/UNK10); "
          f"carriers: AURA_DOES_NOT_REFRESH all {attrs['AURA_DOES_NOT_REFRESH']['counts']['all']} / player "
          f"{attrs['AURA_DOES_NOT_REFRESH']['counts']['player']}, ASYNCHRONOUS_STACKING_BUFF all "
          f"{attrs['ASYNCHRONOUS_STACKING_BUFF']['counts']['all']} / player {attrs['ASYNCHRONOUS_STACKING_BUFF']['counts']['player']}; "
          "no consumer oracle exists to differential-test them",
          "a Retail observation (AL-X-J-01/02) or a newer consumer")
    return {"unknowns": unknowns, "retail_experiments": experiments, "rules": rules, "falsification": fals,
            "trinity_defects": [], "core_navigation": core_nav}


# ---------------------------------------------------------------------------
# lifecycle-signatures.json
# ---------------------------------------------------------------------------


def signatures_payload(st: State) -> dict[str, Any]:
    skew = st.skew.__contains__
    out: dict[str, Any] = {
        "provenance": records.provenance(f"aura_lifecycle.py signatures --out {CORPUS}lifecycle-signatures.json"),
        "track": "J",
        "caveat": "a signature groups authored lifecycle inputs; it does not imply identical runtime behaviour "
                  "(scripts, overlays and Retail may differ) nor executable support",
        "granularities": {"path": signatures.signature.__doc__.split("* ``path``", 1)[1].strip(),
                          "full": "every spell axis + multiset of effect tuples (kind, class, period, target)",
                          "exact": "full + raw EffectAura per effect",
                          "id": "prefix + sha256(canonical JSON of the signature)[:12]; stable across runs"},
        "distribution": {}, "top": {}, "catalog": {},
    }
    for g in signatures.GRANULARITIES:
        cat = signatures.group(st.records, st.pops, skew=skew, catalogs=st.catalogs, granularity=g,
                               member_pops=MEMBER_POPS)
        out["distribution"][g] = {p: signatures.distribution(cat, p) for p in POPS}
        out["top"][g] = {p: signatures.top(cat, p, 15 if g != "path" else 25) for p in POPS}
        if g in ("path", "full"):
            out["catalog"][g] = [e for e in cat if any(e["counts"][p] for p in MEMBER_POPS)]
        out.setdefault("core_by_signature", {})[g] = _core_sig_summary(cat) if st.catalogs else None
        out.setdefault("skew_by_signature", {})[g] = {
            p: {"signatures_only_skewed": sum(1 for e in cat if e["counts"][p] and _all_skewed(st, e, p)),
                "signatures_with_skew": sum(1 for e in cat if e["counts"][p] and _any_skewed(st, e, p))}
            for p in MEMBER_POPS}
    out["axis_agreement"] = axis_agreement(st)
    out["catalog_scope"] = {"path": "signatures with at least one player, player+class-skills or controlled member",
                            "full": "signatures with at least one player, player+class-skills or controlled member",
                            "exact": "distribution + top only (regenerate with the CLI)"}
    return out


def axis_agreement(st: State) -> dict[str, Any]:
    """Where J signature classes and the lead semantic axes (``model.axes``) disagree.

    For every multi-member signature class of a member population, count the classes whose members
    carry more than one distinct lead axis tuple, and which lead axes differ inside them.  Spells for
    which ``model.axes`` fails closed are counted separately (never guessed)."""
    try:
        from . import model
    except ImportError:
        return {"available": False, "reason": "aura_lifecycle.model absent"}
    tuples: dict[int, tuple | None] = {}
    failed: dict[int, str] = {}
    spells = sorted(set().union(*(st.pops[p] for p in MEMBER_POPS)))
    for s in spells:
        try:
            ax = model.axes(st.ctx, s)
            tuples[s] = tuple(sorted(ax.items()))
        except FailClosed as exc:
            tuples[s] = None
            failed[s] = str(exc)[:160]
    out: dict[str, Any] = {"available": True, "evidence": "structural-inference",
                           "axes_fail_closed": {"count": len(failed), "sample": dict(list(sorted(failed.items()))[:10])}}
    for g in signatures.GRANULARITIES:
        classes: dict[str, list[int]] = defaultdict(list)
        for s in spells:
            classes[st.ids[s][g]].append(s)
        per = {}
        for p in MEMBER_POPS:
            multi = hetero = hetero_spells = multi_spells = 0
            differing: Counter = Counter()
            examples = []
            for sid, members in sorted(classes.items()):
                m = [s for s in members if s in st.pops[p] and tuples[s] is not None]
                if len(m) < 2:
                    continue
                multi += 1
                multi_spells += len(m)
                distinct = {tuples[s] for s in m}
                if len(distinct) > 1:
                    hetero += 1
                    hetero_spells += len(m)
                    keys = {k for t in distinct for k, _ in t}
                    diff = sorted(k for k in keys if len({dict(t)[k] for t in distinct}) > 1)
                    differing.update(diff)
                    if len(examples) < 8:
                        examples.append({"id": sid, "members": len(m), "axis_tuples": len(distinct),
                                         "differing_axes": diff, "witnesses": m[:4]})
            per[p] = {"multi_member_classes": multi, "spells_in_multi": multi_spells,
                      "classes_with_multiple_axis_tuples": hetero, "spells_in_those": hetero_spells,
                      "differing_axis_counts": dict(sorted(differing.items())), "examples": examples}
        out[g] = per
    return out


def _members(st: State, e: dict[str, Any], p: str) -> list[int]:
    return e.get(p + "_members") or []


def _all_skewed(st: State, e, p) -> bool:
    m = _members(st, e, p)
    return bool(m) and all(s in st.skew for s in m)


def _any_skewed(st: State, e, p) -> bool:
    return any(s in st.skew for s in _members(st, e, p))


def _core_sig_summary(cat: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for p in POPS:
        rows = [e for e in cat if e["counts"][p]]
        out[p] = {
            "signatures": len(rows),
            "fully_catalog_admissible": sum(1 for e in rows if e["core"]["catalog_admissible"][p] == e["counts"][p]),
            "any_catalog_admissible": sum(1 for e in rows if e["core"]["catalog_admissible"][p]),
            "any_lifecycle_only_blocked": sum(1 for e in rows if e["core"]["lifecycle_only_blocked"][p]),
            "spells_in_fully_admissible": sum(e["counts"][p] for e in rows
                                              if e["core"]["catalog_admissible"][p] == e["counts"][p]),
        }
    return out


# ---------------------------------------------------------------------------
# drift.json
# ---------------------------------------------------------------------------


def drift_payload(st: State) -> dict[str, Any]:
    from . import providers
    ctx = st.ctx
    base = {"provenance": records.provenance(f"aura_lifecycle.py drift --out {CORPUS}drift.json"),
            "track": "J", "primary_snapshot": PINS["data_snapshot"], "drift_snapshot": PINS["drift_snapshot"],
            "drift_release": PINS["drift_release"], "evidence": "build-drift",
            "caveat": "drift flags lifecycle-field changes between builds; the primary snapshot stays authoritative"}
    if ctx.drift is None:
        return {**base, "available": False, "reason": "drift snapshot not fetched (tools/fetch_dbc_release.py)"}
    dd = ctx.drift
    new_spells = frozenset(providers.provider_spells(dd))
    old_spells = st.pops["all"]
    compared_tables = sorted(census.DRIFT_FIELDS) + ["SpellDuration"]
    for t in compared_tables:
        dd.table(t)
    new_recs = {r["spell"]: r for r in census.build(ctx, dd, flags=st.h_flags)}
    changed: dict[int, dict[str, Any]] = {}
    field_counts: Counter = Counter()
    bit_counts: Counter = Counter()
    for s in sorted(old_spells & new_spells):
        a, b = census.drift_fields(ctx.data, s), census.drift_fields(dd, s)
        diff = census.diff_fields(a, b)
        if not diff:
            continue
        ids_old, ids_new = st.ids[s], signatures.record_ids(new_recs[s])
        bits = census.attr_bit_changes(a, b)
        changed[s] = {"spell": s, "name": str(ctx.name(s)), "fields": diff, "attr_bits": bits,
                      "signature_changed": {g: ids_old[g] != ids_new[g] for g in signatures.GRANULARITIES},
                      "signature_ids": {"old": ids_old, "new": ids_new},
                      "populations": [p for p in POPS if s in st.pops[p]], "build_skew": s in st.skew}
        for f, _, _ in diff:
            field_counts[f.split("].", 1)[-1] if f.startswith("SpellEffect[") else f] += 1
        bit_counts.update(bits)
    summary = {}
    for p in POPS:
        pop = st.pops[p]
        rows = [c for s, c in changed.items() if s in pop]
        summary[p] = {
            "providers_primary": len(pop),
            "providers_removed_in_drift": len(pop - new_spells),
            "lifecycle_field_changes": len(rows),
            "signature_changes": {g: sum(1 for c in rows if c["signature_changed"][g]) for g in signatures.GRANULARITIES},
            "skewed_among_changed": sum(1 for c in rows if c["build_skew"]),
        }
    summary["all"]["providers_added_in_drift"] = len(new_spells - old_spells)
    added = sorted(new_spells - old_spells)
    player_ctl = st.pops["player"] | st.pops["player+class-skills"] | st.pops["controlled"]
    payload = {
        **base, "available": True,
        "compared_tables": compared_tables, "compared_fields": {t: list(c) for t, c in census.DRIFT_FIELDS.items()},
        "absent_in_drift": sorted(dd.absent),
        "summary": summary,
        "field_change_counts": dict(sorted(field_counts.items())),
        "attr_bit_change_counts": dict(sorted(bit_counts.items())),
        "changes_player_class_skills_or_controlled": [changed[s] for s in sorted(changed) if s in player_ctl],
        "changed_spells_all": sorted(changed),
        "signature_changed_spells_all": {g: sorted(s for s, c in changed.items() if c["signature_changed"][g])
                                         for g in signatures.GRANULARITIES},
        "removed_providers": {p: sorted(st.pops[p] - new_spells) for p in POPS},
        "added_providers": {"count": len(added), "sample": added[:50],
                            "note": "providers only in the drift build; not in any primary population"},
    }
    return payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_census(p) -> None:
    p.add_argument("--spell", type=int, help="explain one provider instead of writing the census")
    p.add_argument("--out")


def _add_out(p) -> None:
    p.add_argument("--out")


def _census(args) -> int:
    st = state()
    emit(spell_row(st, args.spell) if args.spell else census_payload(st), args.out)
    return 0


def _signatures(args) -> int:
    emit(signatures_payload(state()), args.out)
    return 0


def _drift(args) -> int:
    emit(drift_payload(state()), args.out)
    return 0


COMMANDS = {
    "census": ("track J: provider census along lifecycle axes (provider-census.json) or one spell", _add_census, _census),
    "signatures": ("track J: lifecycle-signature catalog (lifecycle-signatures.json)", _add_out, _signatures),
    "drift": ("track J: lifecycle-field build drift 69497 -> 69814 (drift.json)", _add_out, _drift),
}
