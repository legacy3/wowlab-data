"""Per-Aura proc state, RNG consumption and ordering."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from proc_synthetic import Eff, FakeOverlay, Spell, world

from procs import SourceError, UnsupportedSource
from procs import enums as E
from procs.chance import RppmInputs
from procs.eligibility import ActorFacts
from procs.events import melee_swing, spell_hit
from procs.state import Provider, StreamItem, Timeline, iteration_order

HIT = 200
PROVIDER = 100
DEAL = E.PROC_FLAG_DEAL_HARMFUL_SPELL
FACTS = ActorFacts(actor_level=80)


def setup(tmp_path, providers: list[Spell], ppm=(), extra=(), overlay=None):
    spells = providers + [Spell(HIT, dmg_class=1, effects=[Eff(effect=2, aura=0)])] + list(extra)
    cat, defs, ev = world(tmp_path, spells, ppm=ppm, overlay=overlay)
    return cat, defs, ev


def provider_spell(sid=PROVIDER, **kw):
    base = dict(proc_flags=DEAL, proc_chance=50, effects=[Eff(aura=42, trigger=HIT)])
    base.update(kw)
    return Spell(sid, **base)


def hit_event(cat, **kw):
    return spell_hit(cat.require(HIT), damage=10, **kw)


def ev_item(t, cat, rolls, **kw):
    return StreamItem(t, "event", event=hit_event(cat, **kw), rolls=rolls, facts=FACTS)


def run(ev, providers, stream):
    tl = Timeline(ev, providers)
    for p in providers:
        tl.apply(p.spell_id, 0)
    return tl, tl.run(stream)


def test_fixed_chance_roll_boundary_and_consumption(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell()])
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [ev_item(1000, cat, [49.99]), ev_item(2000, cat, [50.0])])
    assert log[0]["rolls_consumed"] == [49.99] and log[0]["steps"][0]["success"] is True
    assert log[0]["steps"][0]["triggered"][0]["cast_spell"] == HIT
    assert log[1]["steps"][0]["success"] is False and log[1]["steps"][0]["triggered"] == []


def test_guaranteed_chance_still_consumes_a_roll(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_chance=101)])
    p = Provider(defs.get(PROVIDER), "actor")
    with pytest.raises(SourceError, match="needs another roll"):
        run(ev, [p], [ev_item(1, cat, [])])


def test_rejected_before_rng_consumes_no_roll(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell()])
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [StreamItem(1, "event", event=melee_swing(), rolls=[], facts=FACTS)])
    step = log[0]["steps"][0]
    assert step["reached"] and step["verdict"] == "ineligible" and step["roll"] is None
    with pytest.raises(SourceError, match="unused roll"):
        run(ev, [Provider(defs.get(PROVIDER), "actor")],
            [StreamItem(1, "event", event=melee_swing(), rolls=[1.0], facts=FACTS)])


def test_icd_blocks_before_rng_and_starts_on_success_only(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_cooldown=5000)])
    p = Provider(defs.get(PROVIDER), "actor")
    stream = [ev_item(1000, cat, [99.0]),          # fail: no ICD
              ev_item(1500, cat, [0.0]),           # success at 1500 -> ICD to 6500
              ev_item(3000, cat, []),              # blocked, no roll
              ev_item(6500, cat, [0.0])]           # cooldown until 6500 is not > now
    _, log = run(ev, [p], stream)
    assert log[0]["steps"][0]["state_after"]["proc_cooldown_until"] == float("-inf")
    assert log[1]["steps"][0]["state_after"]["proc_cooldown_until"] == 6500
    assert log[2]["steps"][0]["reason"] == "proc-cooldown" and log[2]["rolls_consumed"] == []
    assert log[3]["steps"][0]["success"] is True


def test_cooldown_on_failure_attribute(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_cooldown=5000,
                                                    attributes={2: E.ATTR2_PROC_COOLDOWN_ON_FAILURE[1]})])
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [ev_item(1000, cat, [99.0]), ev_item(2000, cat, [])])
    assert log[0]["steps"][0]["state_after"]["proc_cooldown_until"] == 6000
    assert log[1]["steps"][0]["reason"] == "proc-cooldown"
    # even an event rejected on flags (not only a failed roll) starts the cooldown
    _, log2 = run(ev, [Provider(defs.get(PROVIDER), "actor")],
                  [StreamItem(10, "event", event=melee_swing(), facts=FACTS)])
    assert log2[0]["steps"][0]["state_after"]["proc_cooldown_until"] == 5010


def test_charges_drop_and_remove(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_charges=2, proc_chance=100)])
    p = Provider(defs.get(PROVIDER), "actor")
    tl, log = run(ev, [p], [ev_item(1, cat, [0.0]), ev_item(2, cat, [0.0]), ev_item(3, cat, [])])
    assert log[0]["steps"][0]["state_after"]["charges"] == 1
    assert log[1]["steps"][0]["state_after"]["charges"] == 0
    assert log[1]["steps"][0]["state_after"]["removed"] is True
    assert log[2]["steps"] == []          # aura gone: not visited at all


def test_failed_roll_does_not_drop_charge(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_charges=2)])
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [ev_item(1, cat, [80.0])])
    assert log[0]["steps"][0]["state_after"]["charges"] == 2


def test_failure_burns_charge(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_charges=2,
                                                    attributes={0: E.ATTR0_PROC_FAILURE_BURNS_CHARGE[1]})])
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [ev_item(1, cat, [80.0]),
                           StreamItem(2, "event", event=melee_swing(), facts=FACTS)])
    assert log[0]["steps"][0]["state_after"]["charges"] == 1
    assert log[1]["steps"][0]["state_after"]["charges"] == 0
    assert log[1]["steps"][0]["state_after"]["removed"] is True


def test_do_not_consume_resources_event_keeps_charge(tmp_path):
    nc = Spell(300, dmg_class=1, attributes={6: E.ATTR6_DO_NOT_CONSUME_RESOURCES[1]}, effects=[Eff(effect=2, aura=0)])
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_charges=1, proc_chance=100)], extra=[nc])
    p = Provider(defs.get(PROVIDER), "actor")
    stream = [StreamItem(1, "event", event=spell_hit(cat.require(300), damage=1), rolls=[0.0], facts=FACTS)]
    _, log = run(ev, [p], stream)
    after = log[0]["steps"][0]["state_after"]
    assert after["charges"] == 1 and not after["removed"]


def test_stack_charges(tmp_path):
    ov = FakeOverlay()
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_chance=100)], overlay=ov)
    d = defs.get(PROVIDER)
    from dataclasses import replace
    d.entry = replace(d.entry, attributes_mask=E.PROC_ATTR_USE_STACKS_FOR_CHARGES, charges=3)
    p = Provider(d, "actor")
    tl = Timeline(ev, [p])
    tl.apply(PROVIDER, 0, stack_amount=2)
    log = tl.run([ev_item(1, cat, [0.0]), ev_item(2, cat, [0.0])])
    assert log[0]["steps"][0]["state_after"]["charges"] == 3      # charges untouched
    assert log[0]["steps"][0]["state_after"]["stack_amount"] == 1
    assert log[1]["steps"][0]["state_after"]["removed"] is True


def test_refresh_resets_charges_not_timers(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_charges=3, proc_chance=100, proc_cooldown=100)])
    p = Provider(defs.get(PROVIDER), "actor")
    tl, log = run(ev, [p], [ev_item(1, cat, [0.0]), StreamItem(2, "refresh", spell_id=PROVIDER)])
    assert log[1]["state"]["charges"] == 3
    assert log[1]["state"]["proc_cooldown_until"] == 101


def test_prepare_proc_veto_still_triggers(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_charges=1, proc_chance=100, proc_cooldown=100)])
    p = Provider(defs.get(PROVIDER), "actor", prepare_proc_result=False)
    _, log = run(ev, [p], [ev_item(5, cat, [0.0])])
    step = log[0]["steps"][0]
    assert step["triggered"] and step["state_after"]["charges"] == 1
    assert step["state_after"]["proc_cooldown_until"] == float("-inf")
    assert step["state_after"]["last_success_ms"] == -120000
    assert step["state_after"]["last_attempt_ms"] == 5


def rppm_world(tmp_path, rate=1.0):
    return setup(tmp_path, [provider_spell(ppm_id=7, proc_chance=0)], ppm=[(7, rate, 1)])


def test_rppm_state_updates(tmp_path):
    cat, defs, ev = rppm_world(tmp_path)
    p = Provider(defs.get(PROVIDER), "actor", rppm_inputs=RppmInputs())
    _, log = run(ev, [p], [ev_item(1000, cat, [99.0]), ev_item(3000, cat, [0.0]), ev_item(4000, cat, [99.9])])
    first, second, third = (e["steps"][0] for e in log)
    # applied at 0 -> attempt clock 11s (clamped 10), success clock 121s:
    # (1 + (121/60 - 1.5) * 3) * 1 * 10 / 60
    assert first["chance"]["chance_percent"] == pytest.approx(42.5, rel=1e-5)
    assert first["success"] is False
    assert first["state_after"]["last_attempt_ms"] == 1000            # failed roll still stamps attempt
    assert first["state_after"]["last_success_ms"] == -120000
    assert second["state_after"]["last_success_ms"] == 3000
    # second: 2s since attempt, 123s since success
    assert second["chance"]["chance_percent"] == pytest.approx((1 + (123 / 60 - 1.5) * 3) * 2 / 60 * 100, rel=1e-5)
    # third: 1s since attempt, 1s since success -> bad-luck floor 1 -> 1/60
    assert third["chance"]["chance_percent"] == pytest.approx(100 / 60, rel=1e-5)


def test_rppm_without_caster_uses_fixed_chance(tmp_path):
    cat, defs, ev = rppm_world(tmp_path)
    p = Provider(defs.get(PROVIDER), "actor", has_caster=False)
    _, log = run(ev, [p], [ev_item(1000, cat, [0.0])])
    assert log[0]["steps"][0]["chance"]["chance_percent"] == 0.0
    assert log[0]["steps"][0]["success"] is False


def test_rejected_attempt_does_not_advance_rppm_clock(tmp_path):
    cat, defs, ev = rppm_world(tmp_path)
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [StreamItem(500, "event", event=melee_swing(), facts=FACTS)])
    assert log[0]["steps"][0]["state_after"]["last_attempt_ms"] == -10000


def test_ordering_actor_before_target_and_spell_id_order(tmp_path):
    a = provider_spell(300, proc_chance=100)
    b = provider_spell(150, proc_chance=100)
    t = provider_spell(50, proc_flags=E.PROC_FLAG_TAKE_HARMFUL_SPELL, proc_chance=100)
    cat, defs, ev = setup(tmp_path, [a, b, t])
    provs = [Provider(defs.get(300), "actor", sequence=0), Provider(defs.get(50), "target", sequence=1),
             Provider(defs.get(150), "actor", sequence=2)]
    assert [p.spell_id for p in iteration_order(provs)] == [150, 300, 50]
    _, log = run(ev, provs, [ev_item(1, cat, [0.1, 0.2, 0.3])])
    assert [(s["spell_id"], s["roll"]) for s in log[0]["steps"]] == [(150, 0.1), (300, 0.2), (50, 0.3)]


def test_same_spell_id_uses_insertion_order(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_chance=100)])
    d = defs.get(PROVIDER)
    first, second = Provider(d, "actor", sequence=5), Provider(d, "actor", sequence=2)
    assert iteration_order([first, second]) == [second, first]


def test_all_rolls_precede_all_triggers(tmp_path):
    """Two actor providers: both roll before either trigger runs, so a provider
    removed by an earlier trigger (charges) still consumed its roll."""
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_chance=100, proc_charges=1),
                                     provider_spell(101, proc_chance=100)])
    provs = [Provider(defs.get(PROVIDER), "actor"), Provider(defs.get(101), "actor")]
    _, log = run(ev, provs, [ev_item(1, cat, [0.0, 0.0])])
    assert log[0]["rolls_consumed"] == [0.0, 0.0]
    assert all(s["triggered"] for s in log[0]["steps"])


def test_target_procs_on_caster_changes_target(tmp_path):
    t = provider_spell(proc_flags=E.PROC_FLAG_TAKE_HARMFUL_SPELL, proc_chance=100)
    t8 = provider_spell(101, proc_flags=E.PROC_FLAG_TAKE_HARMFUL_SPELL, proc_chance=100,
                        attributes={8: E.ATTR8_TARGET_PROCS_ON_CASTER[1]})
    cat, defs, ev = setup(tmp_path, [t, t8])
    provs = [Provider(defs.get(PROVIDER), "target"), Provider(defs.get(101), "target")]
    _, log = run(ev, provs, [ev_item(1, cat, [0.0, 0.0])])
    assert [s["triggered"][0]["explicit_target"] for s in log[0]["steps"]] == ["actor", "actor"]
    d = provider_spell(proc_chance=100)
    cat2, defs2, ev2 = setup(tmp_path / "d", [d])
    _, log2 = run(ev2, [Provider(defs2.get(PROVIDER), "actor")], [ev_item(1, cat2, [0.0])])
    assert log2[0]["steps"][0]["triggered"][0]["explicit_target"] == "action_target"


def test_chain_limit_blocks_every_provider(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell(proc_chance=100)])
    p = Provider(defs.get(PROVIDER), "actor")
    _, log = run(ev, [p], [ev_item(1, cat, [], proc_chain_length=10)])
    assert log[0]["steps"][0]["reached"] is False


def test_unknown_fact_fails_closed(tmp_path):
    ov = FakeOverlay(conditions={PROVIDER: [{"x": 1}]})
    cat, defs, ev = setup(tmp_path, [provider_spell()], overlay=ov)
    p = Provider(defs.get(PROVIDER), "actor")
    with pytest.raises(UnsupportedSource):
        run(ev, [p], [ev_item(1, cat, [0.0])])


def test_stream_must_be_ordered(tmp_path):
    cat, defs, ev = setup(tmp_path, [provider_spell()])
    with pytest.raises(SourceError):
        run(ev, [Provider(defs.get(PROVIDER), "actor")], [ev_item(5, cat, [0.0]), ev_item(4, cat, [0.0])])


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 3000), st.floats(0, 99.999)), min_size=1, max_size=12))
def test_replay_is_deterministic_and_rolls_match_eligible_attempts(tmp_path_factory, events):
    tmp = tmp_path_factory.mktemp("replay")
    cat, defs, ev = setup(tmp, [provider_spell(proc_cooldown=700, proc_charges=4)])
    stream, t = [], 0
    for dt, roll in events:
        t += dt
        stream.append((t, roll))

    def replay():
        p = Provider(defs.get(PROVIDER), "actor")
        tl = Timeline(ev, [p])
        tl.apply(PROVIDER, 0)
        out = []
        for time, roll in stream:
            st_ = p.state
            blocked = st_.removed or st_.proc_cooldown_until > time
            item = StreamItem(time, "event", event=hit_event(cat), rolls=[] if blocked else [roll], facts=FACTS)
            out.extend(tl.run([item]))
        return out

    first, second = replay(), replay()
    assert first == second
    for entry in first:
        for step in entry["steps"]:
            assert (step["roll"] is not None) == (step["verdict"] == "eligible")
