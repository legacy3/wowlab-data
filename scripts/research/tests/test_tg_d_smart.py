"""Track D: smart / injured / ranked recipient selection.

Synthetic fixtures discriminate the Trinity helpers from plausible smart-heal
models; probe differentials compile Trinity's SelectRandomInjuredTargets,
SortTargetsWithPriorityRules (+ Radiance rules), HealthPctOrderPred and
std::partition.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from targeting import CORPORA, FailClosed
from targeting import smart
from targeting.fixture import World
from targeting.trace import Trace


def actor(aid, kind="player", hp=100, mhp=100, group="g1", owner=None, raid_unit=False, faction=35, auras=(), pos=(0, 0, 0),
          **facts):
    a = {"id": aid, "kind": kind, "pos": list(pos), "orientation": 0.0, "alive": True, "health": hp, "max_health": mhp,
         "combat_reach": 1.0, "owner": owner, "charmer": None,
         "auras": [{"spell": s, "caster": c} for s, c in auras],
         "facts": {"treated_as_raid_unit": raid_unit, "faction": faction, **facts}}
    if kind == "player":
        a["group"] = None if group is None else {"id": group, "subgroup": 0}
    return a


def world(actors, order=None, draws=(), caster="me", explicit=None, spell_value=None, relations=()):
    return World.from_dict({"schema": "targeting-fixture/1", "name": "smart", "spell": {"synthetic": True},
                            "caster": caster, "actors": actors, "visit_order": order,
                            "rng": {"draws": list(draws)}, "explicit": {"unit": explicit} if explicit else {},
                            "spell_value": spell_value or {}, "relations": list(relations)})


# ---------------------------------------------------------------------------
# SelectRandomInjuredTargets
# ---------------------------------------------------------------------------
def test_injured_noop_without_draws():
    w = world([actor("me"), actor("a", hp=1)], draws=[])
    assert smart.select_random_injured_targets(w, ["a", "me"], 2, True, "me") == ["a", "me"]
    assert w.draws_consumed == 0


def test_injured_is_binary_not_deficit_ranked():
    """Rules out deficit / health-% ranking: a barely injured unit ties with a nearly dead one."""
    acts = [actor("me"), actor("slight", hp=99), actor("dying", hp=1), actor("full")]
    # class 0 (injured grouped players) = [slight, dying] fills max=2 exactly -> shuffled anyway (1 draw)
    w = world(acts, draws=[0])
    got = smart.select_random_injured_targets(w, ["full", "slight", "dying"], 2, True, "me")
    assert sorted(got) == ["dying", "slight"] and w.draws_consumed == 1
    assert got == ["dying", "slight"]            # j_1 = 0 swaps the pair
    w = world(acts, draws=[1])
    tr = Trace()
    assert smart.select_random_injured_targets(w, ["full", "slight", "dying"], 2, True, "me", tr) == ["slight", "dying"]
    assert any(s.defect == "TG-D-DEF-10" for s in tr.stages)       # exact-fill shuffle is marked


def test_injured_priority_order_group_player_injured():
    """Bit significance NOT_INJURED(4) > NOT_PLAYER(2) > NOT_GROUPED(1): an injured outsider beats a
    full-health group member (rules out 'group members first')."""
    acts = [actor("me"), actor("out_hurt", hp=10, group="g2"), actor("in_full"), actor("pet_hurt", kind="pet", hp=5, owner="me"),
            actor("in_hurt", hp=50)]
    w = world(acts, draws=[])
    prio = {t: smart.injured_priority(w, t, True, "me") for t in ("out_hurt", "in_full", "pet_hurt", "in_hurt")}
    assert prio == {"out_hurt": 1, "in_full": 4, "pet_hurt": 2, "in_hurt": 0}
    # max 3: classes 0 (in_hurt), 1 (out_hurt), 2 (pet_hurt; fills exactly, single element -> 0 draws)
    got = smart.select_random_injured_targets(w, ["out_hurt", "in_full", "pet_hurt", "in_hurt"], 3, True, "me")
    assert got == ["in_hurt", "out_hurt", "pet_hurt"]
    # without group preference the full-health group member loses to every injured unit
    got = smart.select_random_injured_targets(w, ["out_hurt", "in_full", "pet_hurt", "in_hurt"], 3, True, None)
    assert got == ["out_hurt", "in_hurt", "pet_hurt"]


def test_injured_boundary_shuffle_only_that_class():
    acts = [actor("me")] + [actor(f"h{i}", hp=50) for i in range(2)] + [actor(f"f{i}") for i in range(4)]
    targets = ["f0", "h0", "f1", "f2", "h1", "f3"]
    w = world(acts, draws=[0, 2, 1])          # boundary = 4 full-health units -> 3 FY draws
    got = smart.select_random_injured_targets(w, targets, 4, True, "me")
    assert got[:2] == ["h0", "h1"]
    # class order after stable sort: f0 f1 f2 f3; j1=0 -> f1 f0 f2 f3; j2=2 -> same; j3=1 -> f1 f3 f2 f0
    assert got[2:] == ["f1", "f3"] and w.draws_consumed == 3


def test_sort_fails_closed_for_long_tied_ranges():
    with pytest.raises(FailClosed):
        smart.libstdcxx_sort(list(range(17)), key=lambda x: x % 2)
    assert smart.libstdcxx_sort(list(range(17)), key=lambda x: -x)[0] == 16


# ---------------------------------------------------------------------------
# SortTargetsWithPriorityRules / Radiance
# ---------------------------------------------------------------------------
ATON = smart.SPELL_PRIEST_ATONEMENT_EFFECT


def test_radiance_explicit_first_and_atonement_preference():
    """Rules out 'injured first': full-health allies without Atonement beat injured allies with it."""
    acts = [actor("me"), actor("expl", auras=[(ATON, "me")]), actor("aton_hurt", hp=10, auras=[(ATON, "me")]),
            actor("fresh_full"), actor("fresh_hurt", hp=90)]
    w = world(acts, explicit="expl", spell_value={"radiance_effect2_value": 2})
    got = smart.power_word_radiance(w, ["aton_hurt", "fresh_full", "expl", "fresh_hurt"], Trace())
    assert got == ["expl", "fresh_hurt", "fresh_full"]
    assert w.draws_consumed == 0


def test_radiance_explicit_not_in_list_is_not_added():
    acts = [actor("me"), actor("expl"), actor("a", hp=5), actor("b", hp=5), actor("c2", hp=5)]
    w = world(acts, explicit="expl", draws=[0, 2], spell_value={"radiance_effect2_value": 1})
    got = smart.power_word_radiance(w, ["a", "b", "c2"], Trace())
    assert "expl" not in got and len(got) == 2 and w.draws_consumed == 2


def test_priority_tie_shuffle_covers_whole_equal_range():
    """The shuffle range includes equal-score elements *before* the cutoff (Spell.cpp:9600-9608)."""
    acts = [actor("me")] + [actor(x) for x in ("a", "b", "c3")]
    w = world(acts, draws=[0, 0])
    rules = [lambda w_, t: False]
    got = smart.sort_targets_with_priority_rules(w, ["a", "b", "c3"], 1, rules)
    # range [a, b, c3]: j1=0 -> b a c3 ; j2=0 -> c3 a b
    assert got == ["c3"] and w.draws_consumed == 2
    w = world(acts, draws=[])
    rules = [lambda w_, t: t == "b", lambda w_, t: t != "c3"]
    assert smart.sort_targets_with_priority_rules(w, ["a", "b", "c3"], 2, rules) == ["b", "a"]


# ---------------------------------------------------------------------------
# script adapters
# ---------------------------------------------------------------------------
def test_seed_of_corruption_select():
    S = smart.SPELL_WARLOCK_SEED_OF_CORRUPTION
    acts = [actor("me"), actor("t", kind="creature"), actor("x", kind="creature", auras=[(S, "me")]),
            actor("y", kind="creature"), actor("z", kind="creature")]
    w = world(acts, explicit="t")
    assert smart.seed_of_corruption_select(w, ["x", "y"], Trace()) == ["t"]          # primary lacks Seed
    acts[1] = actor("t", kind="creature", auras=[(S, "me")])
    w = world(acts, explicit="t", draws=[2, 1])
    assert smart.seed_of_corruption_select(w, ["t", "x", "y", "z"], Trace()) == ["z"]  # y,z remain -> 2 draws
    w = world(acts, explicit="t")
    assert smart.seed_of_corruption_select(w, ["t", "x"], Trace()) == ["t"]          # nothing left -> explicit
    assert smart.seed_of_corruption_select(w, ["x"], Trace()) == ["x"]               # < 2: unchanged


def test_select_nearby_target_filters_and_draw():
    acts = [actor("me", kind="creature", pos=(0, 0, 0), victim="v"),
            actor("v", kind="creature", pos=(1, 0, 0), spirit_service=False, critter=False),
            actor("ex", kind="creature", pos=(1, 1, 0), spirit_service=False, critter=False),
            actor("n1", kind="creature", pos=(2, 0, 0), spirit_service=False, critter=False),
            actor("tot", kind="totem", pos=(2, 1, 0), spirit_service=False, critter=False),
            actor("fr", kind="creature", pos=(0, 2, 0), spirit_service=False, critter=False),
            actor("far", kind="creature", pos=(9, 0, 0), spirit_service=False, critter=False),
            actor("n2", kind="creature", pos=(0, -3, 0), spirit_service=False, critter=False)]
    rel = [{"from": "me", "to": t, "friendly": t == "fr"} for t in ("v", "ex", "n1", "tot", "fr", "far", "n2")]
    w = World.from_dict({"schema": "targeting-fixture/1", "name": "sn", "spell": {"synthetic": True}, "caster": "me",
                         "actors": acts, "visit_order": ["v", "ex", "n1", "tot", "fr", "far", "n2"],
                         "relations": rel, "los": {"default": "clear", "blocked": [["me", "n2"]]},
                         "rng": {"draws": [0]}})
    assert smart.select_nearby_target(w, "me", "ex") == "n1"
    w2 = World.from_dict({**w.raw, "rng": {"draws": []}, "los": {"default": "clear", "blocked": [["me", "n1"], ["me", "n2"]]}})
    assert smart.select_nearby_target(w2, "me", "ex") is None and w2.draws_consumed == 0


def test_killing_spree_redraws_after_stale_guid():
    acts = [actor("me"), actor("a", kind="creature", resolvable=False), actor("b", kind="creature", resolvable=True)]
    w = world(acts, draws=[0, 0])
    assert smart.killing_spree_pick(w, ["a", "b"], Trace()) == "b" and w.draws_consumed == 2


def test_molten_assault_partition_and_shuffle():
    F = smart.SPELL_SHAMAN_FLAME_SHOCK
    acts = [actor("me")] + [actor(f"e{i}", kind="creature", auras=[(F, "me")] if i in (1, 3) else []) for i in range(5)]
    w = world(acts, draws=[0, 1])
    got = smart.molten_assault(w, ["e0", "e1", "e2", "e3", "e4"], 2, Trace())
    # partition: e3 e1 | e2 e0 e4 ; shuffle tail [e2, e0, e4] with j=0,1 -> e0 e2 e4
    assert got == ["e3", "e1", "e0"]
    w = world(acts, draws=[])
    assert smart.molten_assault(w, ["e0", "e1", "e2", "e3", "e4"], 1, Trace()) == ["e3", "e1"]  # missing 0: no shuffle


def test_min_element_first_and_health_pct_sort():
    assert smart.min_element_first([("a", 5), ("b", 3), ("c", 3)]) == "b"
    acts = [actor("me"), actor("a", hp=50, mhp=100), actor("b", hp=1, mhp=2), actor("z", hp=0, mhp=0)]
    w = world(acts)
    assert smart.list_sort_health_pct(w, ["a", "b", "z"]) == ["z", "a", "b"]
    assert smart.list_sort_health_pct(w, ["a", "b", "z"], ascending=False) == ["a", "b", "z"]


# ---------------------------------------------------------------------------
# probe differentials
# ---------------------------------------------------------------------------
def _probe():
    from targeting import cmd_d
    if not cmd_d.probe_available():
        pytest.skip("TrinityCore checkout absent")
    cmd_d.ensure_probe()
    return cmd_d


def _fy_indices(cmd_d, n: int, words: list[int]) -> list[int]:
    if n <= 1:
        return []
    res = cmd_d.run_probe([f"shuffle {n}\nwords {' '.join(map(str, words))}\ngo"])[0]
    assume("error" not in res)   # degenerate engine words can make uniform_int_distribution reject forever
    assert [a for a, _ in res["swaps"]] == list(range(1, n)), res
    return [b for _, b in res["swaps"]]


def _objs(w, names, idx, group_of=None, caster=None):
    lines = []
    for n in names:
        a = w.actor(n)
        typ = 6 if a.kind == "player" else 5
        raid_unit = int(a.kind != "player" and smart.treated_as_raid_unit(w, n))
        lines.append(f"obj {idx[n]} {typ} 0x0p+0 0x0p+0 0x0p+0 0x1p+0 {a.health} {a.max_health} 1 {raid_unit}")
        partners = [o for o in (group_of, caster) if o is not None and smart.in_raid_with(w, n, o)]
        if partners:
            lines.append(f"raid {idx[n]} " + " ".join(str(idx[p]) for p in partners))
        casters = [c for c in names if a.has_aura(smart.SPELL_PRIEST_ATONEMENT_EFFECT, c)]
        if casters:
            lines.append(f"aton {idx[n]} " + " ".join(str(idx[c]) for c in casters))
    return lines


member = st.tuples(st.sampled_from(["player", "player", "pet", "creature"]), st.integers(0, 2),
                   st.sampled_from(["g1", "g2", None]), st.booleans(), st.booleans())


def _population(spec):
    acts = [actor("me")]
    names = []
    for i, (kind, hurt, group, raid_unit, aton) in enumerate(spec):
        n = f"u{i}"
        names.append(n)
        acts.append(actor(n, kind=kind, hp=100 - 30 * hurt, group=group, owner="me" if kind == "pet" else None,
                          raid_unit=raid_unit and kind == "creature", faction=35 if raid_unit else 7,
                          auras=[(smart.SPELL_PRIEST_ATONEMENT_EFFECT, "me")] if aton else []))
    return acts, names


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(spec=st.lists(member, min_size=1, max_size=14), maxt=st.integers(0, 8), prio=st.booleans(),
       grouped=st.booleans(), words=st.lists(st.integers(0, 2 ** 32 - 1), min_size=40, max_size=40))
def test_probe_differential_injured(spec, maxt, prio, grouped, words):
    cmd_d = _probe()
    acts, names = _population(spec)
    w0 = world(acts)
    group_of = "me" if grouped else None
    prios = {t: smart.injured_priority(w0, t, prio, group_of) for t in names}
    counts = [sum(1 for t in names if prios[t] == c) for c in range(8)]
    found, boundary = 0, 0
    if len(names) > maxt:
        for c in range(8):
            if found + counts[c] >= maxt:
                boundary = counts[c]
                break
            found += counts[c]
    js = _fy_indices(cmd_d, boundary, words)
    w = world(acts, draws=js)
    got = smart.select_random_injured_targets(w, names, maxt, prio, group_of)
    idx = {"me": 0, **{n: i + 1 for i, n in enumerate(names)}}
    case = [f"injured {maxt} {int(prio)} {0 if grouped else -1}"] + _objs(w, ["me"] + names, idx, group_of) + [
        "order " + " ".join(str(idx[n]) for n in names), "words " + " ".join(map(str, words)), "go"]
    res = cmd_d.run_probe(["\n".join(case)])[0]
    assert "error" not in res, res
    inv = {v: k for k, v in idx.items()}
    assert [inv[i] for i in res["targets"]] == got


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(spec=st.lists(member, min_size=1, max_size=14), maxt=st.integers(0, 6), expl=st.integers(-1, 13),
       words=st.lists(st.integers(0, 2 ** 32 - 1), min_size=40, max_size=40))
def test_probe_differential_radiance_rules(spec, maxt, expl, words):
    cmd_d = _probe()
    acts, names = _population(spec)
    explicit = names[expl] if 0 <= expl < len(names) else None
    w0 = world(acts, explicit=explicit)
    rules = smart.radiance_rules("me", explicit)
    score = {t: sum(1 << (len(rules) - 1 - i) for i, r in enumerate(rules) if r(w0, t)) for t in names}
    assume(maxt > 0 or len(names) <= maxt)  # maxTargets 0 with a longer list reads prioritizedTargets[-1] (UB)
    m = 0
    if len(names) > maxt:
        ordered = sorted(names, key=lambda t: -score[t])
        tie = score[ordered[maxt - 1]] if maxt else None
        if maxt and score[ordered[maxt]] == tie:
            m = sum(1 for t in names if score[t] == tie)
    js = _fy_indices(cmd_d, m, words)
    w = world(acts, draws=js, explicit=explicit)
    got = smart.sort_targets_with_priority_rules(w, names, maxt, rules)
    idx = {"me": 0, **{n: i + 1 for i, n in enumerate(names)}}
    case = [f"rules {maxt} {idx[explicit] if explicit else -1} 0"] + _objs(w, ["me"] + names, idx, None, "me") + [
        "order " + " ".join(str(idx[n]) for n in names), "words " + " ".join(map(str, words)), "go"]
    res = cmd_d.run_probe(["\n".join(case)])[0]
    assert "error" not in res, res
    inv = {v: k for k, v in idx.items()}
    assert [inv[i] for i in res["targets"]] == got


@settings(max_examples=80, deadline=None)
@given(hp=st.lists(st.tuples(st.integers(0, 10), st.integers(0, 10)), min_size=1, max_size=12), asc=st.booleans())
def test_probe_differential_health_pct_sort(hp, asc):
    cmd_d = _probe()
    acts = [actor("me")] + [actor(f"u{i}", hp=min(h, m), mhp=m) for i, (h, m) in enumerate(hp)]
    names = [f"u{i}" for i in range(len(hp))]
    w = world(acts)
    got = smart.list_sort_health_pct(w, names, asc)
    idx = {n: i + 1 for i, n in enumerate(names)}
    lines = [f"hpsort {int(asc)}"] + [f"obj {idx[n]} 6 0x0p+0 0x0p+0 0x0p+0 0x0p+0 {w.actor(n).health} {w.actor(n).max_health} 1 0"
                                      for n in names] + ["order " + " ".join(str(idx[n]) for n in names), "go"]
    res = cmd_d.run_probe(["\n".join(lines)])[0]
    inv = {v: k for k, v in idx.items()}
    assert [inv[i] for i in res["targets"]] == got


@settings(max_examples=80, deadline=None)
@given(flags=st.lists(st.booleans(), max_size=12))
def test_probe_differential_partition(flags):
    cmd_d = _probe()
    res = cmd_d.run_probe([f"partition {' '.join(str(int(f)) for f in flags)}\ngo"])[0]
    got, split = smart.libstdcxx_partition(list(range(len(flags))), lambda i: flags[i])
    assert res["order"] == got and res["split"] == split


# ---------------------------------------------------------------------------
# real data
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus(tg_ctx):
    from targeting import cmd_d
    return cmd_d.build_smart()


def test_smart_corpus_regenerates(corpus):
    committed = json.loads((CORPORA / "smart-selection.json").read_text())
    assert committed == json.loads(json.dumps(corpus, sort_keys=True)), \
        "regenerate: targeting.py smart-selection --out docs/research/targeting-corpora/smart-selection.json"


def test_smart_real_families(corpus):
    ec = corpus["effect_classes"]
    assert ec["48438:0"]["class"] == "understood" and "smart" in ec["48438:0"]["tags"]
    assert ec["194509:1"]["class"] == "understood"
    assert ec["1064:0"]["family"] == "chain-heal-deficit"
    ext = {s for site in corpus["sites"] for s in site["spells_script_extended"]}
    assert 155793 in ext and 73921 in ext            # PoM jump, Healing Rain heal: script-extended only
    helpers = [s for s in corpus["sites"] if s["call"] == "SelectRandomInjuredTargets"]
    assert any(48438 in s["spells_in_reach"] for s in helpers)


def test_script_layout_drift_families(corpus):
    """R3-01 / R3-06: Blade Flurry draws without a recipient; Killing Spree never runs."""
    fams = {f["id"]: f for f in corpus["families"]}
    assert fams["proc-random-adjacent"]["status"].startswith("draw-only")
    assert fams["killing-spree"]["status"].startswith("dead")
    roles = {(s["file"].rsplit("/", 1)[-1], s["line"]): s["role"] for s in corpus["sites"]}
    assert roles[("spell_rogue.cpp", 285)] == "draw-no-recipient"
    assert ("spell_rogue.cpp", 668) not in roles
