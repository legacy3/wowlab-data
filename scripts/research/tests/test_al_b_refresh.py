"""Track B: reapplication / refresh / pandemic carryover (aura_lifecycle.refresh).

Each synthetic test names the competing model it rules out.  The verbatim-Trinity confirmation of the
same timelines is tests/test_al_b_probe.py.
"""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, records
from aura_lifecycle.duration import (
    ATTR0_PASSIVE,
    ATTR1_AURA_UNIQUE,
    ATTR5_AURA_UNIQUE_PER_CASTER,
    ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION,
    Caster,
    DurationEntry,
    HitInputs,
    SpellFacts,
    SpellMods,
    empower_spells,
    facts,
)
from aura_lifecycle.refresh import (
    classify,
    pandemic_models,
    rolling_periodic_amount,
    timeline,
)

P13 = ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION


def spell(dur, periods=(3000,), stack=0, *attrs, spell_id=0):
    a = [0] * 17
    for w, m in attrs:
        a[w] |= m
    return SpellFacts(spell_id, tuple(a), DurationEntry(0, dur, dur, 0), stack_amount=stack, periods=periods)


def applies(tl):
    return [e for e in tl["events"] if e["event"] == "apply"]


def ticks(tl):
    return [e["t"] for e in tl["events"] if e["event"] == "tick"]


def expiry(tl):
    return [e["t"] for e in tl["events"] if e["event"] == "expire"]


def test_brief_example_18s_dot_refreshed_at_14s():
    """Rules out 'carry = remaining' (22000) for pinned Trinity: 23400, phase kept, 7 ticks, tick at 36 s suppressed."""
    tl = timeline(spell(18000, (3000,), 0, P13), [(0, HitInputs()), (14000, HitInputs())], end=40000, step=100)
    second = applies(tl)[1]
    assert second["refresh"] is True
    assert second["state"]["duration"] == 23400
    assert second["state"]["effects"][0]["timer"] == 2000
    assert ticks(tl) == [3000, 6000, 9000, 12000, 15000, 18000, 21000, 24000, 27000, 30000, 33000]
    assert expiry(tl) == [37400]
    assert any(e["event"] == "tick-suppressed" and e["t"] == 36000 for e in tl["events"])


def test_plain_refresh_resets_phase_and_duration():
    """Rules out 'refresh keeps the tick phase' for StackAmount<2 without ATTR13."""
    tl = timeline(spell(18000), [(0, HitInputs()), (14000, HitInputs())], end=40000, step=100)
    assert applies(tl)[1]["state"]["duration"] == 18000
    assert ticks(tl)[4:] == [17000, 20000, 23000, 26000, 29000, 32000]
    assert expiry(tl) == [32000]


def test_stackable_refresh_preserves_phase():
    """StackAmount>=2: stack +1, duration restart, phase kept (rules out 'every refresh resets the timer')."""
    tl = timeline(spell(12000, (2000,), 3), [(0, HitInputs()), (5000, HitInputs())], end=20000, step=100)
    a = applies(tl)[1]["state"]
    assert a["stacks"] == 2 and a["duration"] == 12000 and a["effects"][0]["timer"] == 1000
    assert ticks(tl)[2:4] == [6000, 8000]


def test_stack_one_capacity_resets_phase():
    """StackAmount == 1 still resets the phase (resetPeriodicTimer = StackAmount < 2): capacity alone does not decide."""
    tl = timeline(spell(12000, (2000,), 1), [(0, HitInputs()), (5000, HitInputs())], end=20000, step=100)
    assert applies(tl)[1]["state"]["effects"][0]["timer"] == 0


def test_unique_keeps_remaining_unless_hit_changes():
    """Rules out both 'unique = never touched' and 'unique = restart': kept when hit == max, reset otherwise."""
    u = spell(18000, (3000,), 0, ATTR1_AURA_UNIQUE)
    same = timeline(u, [(0, HitInputs()), (10000, HitInputs())], end=20000, step=100)
    assert applies(same)[1]["state"]["duration"] == 8000
    assert applies(same)[1]["state"]["effects"][0]["ticks"] == 3
    changed = timeline(u, [(0, HitInputs()), (10000, HitInputs(caster=Caster(duration_mods=SpellMods(1000, 1.0))))],
                       end=30000, step=100)
    assert applies(changed)[1]["state"]["duration"] == 19000


def test_passive_is_new_object():
    """Same SpellId, Passive -> multislot -> a new Aura (generation+1), never a refresh."""
    p = spell(-1, (0,), 0, ATTR0_PASSIVE)
    tl = timeline(p, [(0, HitInputs()), (100, HitInputs())], end=200, step=100, periodic_effects=(False,))
    assert [a["refresh"] for a in applies(tl)] == [False, False]
    assert applies(tl)[1]["state"]["generation"] == 1


def test_classify_branches():
    assert classify(spell(1000, (0,), 0, ATTR0_PASSIVE))["branch"] == "new-object"
    assert classify(spell(1000, (0,), 5))["branch"] == "stack-and-refresh"
    assert classify(spell(1000, (0,), 0, ATTR5_AURA_UNIQUE_PER_CASTER))["branch"] == "unique-no-timer-refresh"
    assert classify(spell(1000, (0,), 0, P13, ATTR1_AURA_UNIQUE))["carry"] == "pandemic-reads-live-remaining"
    assert classify(spell(1000, (0,), 0, P13))["carry"] == "pandemic-reads-refreshed-duration"
    assert classify(spell(1000, (0,), 2, ATTR1_AURA_UNIQUE))["branch"] == "stack-and-refresh"


def test_self_channel_split_and_finite_gate():
    """R2-07 / R1-04: a channel's own recast is cancel-then-create (never the refresh/pandemic branch), and the
    carry is n/a for non-positive durations (Spell.cpp:3268) -- rules out 'every ATTR13 refresh carries'."""
    from aura_lifecycle.duration import ATTR1_IS_CHANNELLED
    ch = classify(spell(6000, (1000,), 0, P13, ATTR1_IS_CHANNELLED))
    assert ch["branch"] == "self-channel-cancel-then-create" and ch["branch_if_found"] == "refresh"
    assert ch["carry"] == "none"
    perm = classify(spell(-1, (1000,), 0, P13))
    assert perm["branch"] == "refresh" and perm["carry"].startswith("n/a")


def test_models_disagree_where_expected():
    m = pandemic_models(18000, 4000, 18000)
    assert m["trinity-7f3d43b"]["value"] == 23400
    assert m["carry-before-refresh"]["value"] == m["simc"]["value"] == m["core-63f3a49"]["value"] == 22000
    assert m["trinity-pre-92773e2"]["value"] == 18000          # carry computed then discarded by the Spell commit
    big = pandemic_models(10000, 20000, 10000)                  # r > hit (e.g. after an extension)
    assert big["simc"]["value"] == big["core-63f3a49"]["value"] == 20000
    assert big["carry-before-refresh"]["value"] == 13000


def test_rolling_uses_old_totals():
    assert rolling_periodic_amount(100.0, 50.0, 60.0, 2, 6) == pytest.approx(120.0)
    assert rolling_periodic_amount(100.0, 50.0, None, 2, 6) == pytest.approx(100.0 + 50.0 / 3)
    assert rolling_periodic_amount(100.0, 50.0, 60.0, 2, 0) == 100.0


def test_real_witness_classes(al_ctx):
    emp = empower_spells(al_ctx.bundle.source)
    c = {s: classify(facts(al_ctx.data, s, emp)) for s in (146739, 980, 55233, 262115, 1943)}
    assert c[146739]["carry"] == "pandemic-reads-refreshed-duration"
    assert c[980]["branch"] == "stack-and-refresh" and c[980]["periodic_timer"] == "preserved (ATTR13)"
    assert c[262115]["rolling_periodic"] is True
    assert not any(facts(al_ctx.data, 55233, emp).periods)  # ATTR13 on a non-periodic aura


def test_corpora_valid_and_counts_regenerate(al_ctx):
    from aura_lifecycle import providers
    from aura_lifecycle.refresh import census
    for name in ("refresh.json", "carryover.json"):
        path = CORPORA / name
        if not path.exists():
            pytest.skip(f"{name} not generated")
        records.validate_corpus(json.loads(path.read_text(encoding="utf-8")))
    corpus = json.loads((CORPORA / "refresh.json").read_text(encoding="utf-8"))
    fresh = census(al_ctx, providers.populations(al_ctx))
    assert corpus["census"]["branches"] == fresh["branches"]
    assert corpus["census"]["carry"] == fresh["carry"]
    carry = json.loads((CORPORA / "carryover.json").read_text(encoding="utf-8"))
    assert carry["pandemic_population"]["counts"] == fresh["pandemic_population"]
    pp = fresh["pandemic_population"]["player"]
    assert pp["attr13_total"] == 61 and pp["finite"] == 59 and pp["channel"] == 9
    assert pp["carry:pandemic-reads-refreshed-duration"] == 50
