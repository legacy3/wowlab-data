"""Track E: synthetic tests that discriminate competing removal / dispel / lifetime models (no DB2)."""

from __future__ import annotations

import pytest

from aura_lifecycle import FailClosed
from aura_lifecycle import dispel as D
from aura_lifecycle import lifetime as L
from aura_lifecycle import records, removal as R


def test_anchors_are_live() -> None:
    try:
        assert R.verify_anchors() == []
    except FailClosed:
        pytest.skip("TrinityCore checkout absent")


def test_taxonomy_rows_cite_known_anchors_and_modes() -> None:
    ids = [s.id for s in R.SITES]
    assert len(ids) == len(set(ids))
    for s in R.SITES:
        assert set(s.mode.split("|")) <= set(R.REMOVE_MODES.values()) | {"caller"}
        for a in s.anchors:
            assert a in R.ANCHORS
        assert s.final_tick


def test_death_class_is_not_expiry() -> None:
    """Rules out 'passives and death-persistent auras are removed at death' and 'ATTR7 alone removes'."""
    base = [0] * 17
    assert R.death_class(tuple(base)) == "removed-death"
    p = list(base); p[0] = 0x40
    assert R.death_class(tuple(p)) == "survives-passive"
    q = list(base); q[3] = 0x00100000; q[7] = 0x4
    assert R.death_class(tuple(q)) == "survives-death-persistent+disabled-while-dead"
    r = list(base); r[7] = 0x4
    assert R.death_class(tuple(r)) == "removed-death"


def test_death_order_combat_exit_and_channel_precede_sweep() -> None:
    """Rules out 'RemoveAllAurasOnDeath is the only death-time removal' (AL-F-E-02)."""
    fx = L.DeathFixture(auras=[L.HeldAura("lc", leaving_combat_interrupt=True, death_persistent=True),
                               L.HeldAura("dot"), L.HeldAura("chan", channel_of_dying=True, owned_by_dying=False, applied_on_dying=False)])
    steps = L.death_order(fx)
    removed = [(r["aura"], r["mode"]) for s in steps for r in s["removed"]]
    assert removed == [("lc", "INTERRUPT"), ("chan", "CANCEL"), ("dot", "DEATH")]
    fx_pvp = L.DeathFixture(auras=fx.auras, pve_combat_only=False)
    removed = [(r["aura"], r["mode"]) for s in L.death_order(fx_pvp) for r in s["removed"]]
    assert ("lc", "INTERRUPT") not in removed          # PvP: unknown (AL-U-E-02), not asserted as removed


def test_death_order_caster_side_removals() -> None:
    """Rules out 'caster death touches no aura it cast on other holders' (R3-06/07): vehicle, totem, charm, dynobjects."""
    fx = L.DeathFixture(auras=[L.HeldAura("dot")], in_vehicle=True, charmed_auras=("charm",), totem_auras=("totem",),
                        casting_dynobj_auras=("rof",))
    removed = [(r["aura"], r["mode"]) for s in L.death_order(fx) for r in s["removed"]]
    assert removed == [("rof", "DEFAULT"), ("vehicle_control", "DEFAULT"), ("totem", "DEFAULT"), ("charm", "DEFAULT"),
                       ("dot", "DEATH")]


def test_trigger_cast_failure_expires_early() -> None:
    """R2-10 / TL-E-11: a failed periodically triggered cast ends the aura with EXPIRE long before its duration."""
    t = {x["id"]: x for x in L.standard_timelines()}["TL-E-11"]
    removed = [e for e in t["events"] if e["event"] == "removed"]
    assert removed[0]["t"] == 6000 and removed[0]["mode"] == "EXPIRE" and t["final"]["ticks_done"] == 2


def test_new_rows_present() -> None:
    rows = {r["id"]: r for r in R.taxonomy()}
    assert "MaxAffectedTargets" in rows["RM-38"]["predicate"] and rows["RM-38"]["mode"] == "DEFAULT"
    assert rows["RM-47"]["mode"] == "EXPIRE" and "resurrect" not in rows["RM-19"]["reason"]
    assert rows["RM-09"]["foreign_owned_outcome"] and "CATCH-ALL" in rows["RM-34"]["reason"]


def test_timeline_death_vs_expiry_and_same_timestamp() -> None:
    tl = {t["id"]: t["final"] for t in L.standard_timelines()}
    assert (tl["TL-E-01"]["ticks_done"], tl["TL-E-01"]["removed"]) == (4, "EXPIRE")
    assert (tl["TL-E-02"]["ticks_done"], tl["TL-E-02"]["removed"]) == (3, "DEATH")
    assert (tl["TL-E-03"]["ticks_done"], tl["TL-E-03"]["removed"]) == (3, "DEATH")
    assert (tl["TL-E-04"]["ticks_done"], tl["TL-E-04"]["removed"]) == (4, "EXPIRE")
    assert (tl["TL-E-05"]["ticks_done"], tl["TL-E-05"]["removed"]) == (1, "ENEMY_SPELL")
    assert (tl["TL-E-06"]["ticks_done"], tl["TL-E-06"]["removed"], tl["TL-E-06"]["stacks"]) == (4, "EXPIRE", 2)
    assert (tl["TL-E-07"]["ticks_done"], tl["TL-E-07"]["effective_ticks"]) == (4, 4)
    assert (tl["TL-E-09"]["ticks_done"], tl["TL-E-09"]["effective_ticks"], tl["TL-E-09"]["removed"]) == (4, 1, "EXPIRE")
    assert (tl["TL-E-10"]["ticks_done"], tl["TL-E-10"]["effective_ticks"]) == (4, 1)


def test_tick_gates() -> None:
    assert L.tick_effective(3, "dead", True) is True           # dead caster still found
    assert L.tick_effective(3, "absent", False) is False       # dead holder
    assert L.tick_effective(62, "dead", True) is False
    assert L.tick_effective(162, "dead", True) is True
    with pytest.raises(FailClosed):
        L.tick_effective(70, "absent", True)
    with pytest.raises(FailClosed):
        L.tick_effective(23, "alive", True)


def test_dispel_mask_all_excludes_enrage_and_bleed() -> None:
    assert D.dispel_mask(7) == 0b11110
    assert not D.dispel_mask(7) & D.dispel_mask(9)
    assert not D.dispel_mask(7) & D.dispel_mask(11)
    assert D.dispel_mask(11) == 1 << 11


def test_selection_is_per_identity_not_per_stack() -> None:
    """Rules out stack-weighted selection: 5-stack and 1-stack auras are two equal entries."""
    lst = D.dispellable_list([D.DAura("A", 1, "c"), D.DAura("B", 2, "c", stacks=5)], D.dispel_mask(1), True)
    assert [(c.aura.key, c.charges) for c in lst] == [("A", 1), ("B", 5)]


def test_failed_roll_consumes_attempt() -> None:
    lst = D.dispellable_list([D.DAura("R", 3, "c", resist_pct=90)], D.dispel_mask(1), True)
    rng = D.ScriptedRng(ints=[0, 99, 0, 10])
    res = D.effect_dispel(lst, 2, rng)
    assert res.success == [] and res.failed_spells == [3, 3] and len(rng.calls) == 4


def test_relation_and_reflect_filter() -> None:
    buff = D.DAura("buff", 1, "c", positive=True)
    debuff = D.DAura("debuff", 2, "c", positive=False)
    assert [c.aura.key for c in D.dispellable_list([buff, debuff], 2, target_friendly_to_dispeller=True)] == ["debuff"]
    assert [c.aura.key for c in D.dispellable_list([buff, debuff], 2, target_friendly_to_dispeller=False)] == ["buff"]
    assert [c.aura.key for c in D.dispellable_list([buff, debuff], 2, False, reflect=True)] == ["debuff"]


def test_mechanic_dispel_rolls_every_applied_aura_first() -> None:
    """Rules out 'only mechanic-matching auras consume a roll' and 'passives are exempt' (AL-R-E-13)."""
    owned = [D.DAura("p", 1, "c", passive=True, mechanic_mask=1 << 7), D.DAura("x", 2, "c"),
             D.DAura("m", 3, "c", mechanic_mask=1 << 7), D.DAura("off", 4, "c", applied_on_owner=False, mechanic_mask=1 << 7)]
    rng = D.ScriptedRng(ints=[0, 0, 0])
    assert D.mechanic_dispel(owned, 7, rng) == ["p", "m"]
    assert len(rng.calls) == 3


def test_steal_filter_positive_only() -> None:
    owned = [D.DAura("b", 1, "c", positive=True), D.DAura("d", 2, "c", positive=False),
             D.DAura("ns", 3, "c", positive=True, cannot_be_stolen=True)]
    assert [c.aura.key for c in D.steal_candidates(owned, 2)] == ["b"]


def test_records_validate() -> None:
    from aura_lifecycle import cmd_e
    for recs in (cmd_e.REMOVAL_RECORDS, cmd_e.DISPEL_RECORDS, cmd_e.LIFETIME_RECORDS):
        records.validate_corpus(recs)
    ids = [e["id"] for recs in (cmd_e.REMOVAL_RECORDS, cmd_e.DISPEL_RECORDS, cmd_e.LIFETIME_RECORDS)
           for v in recs.values() for e in v]
    assert len(ids) == len(set(ids))


def test_dispel_fixtures_are_deterministic() -> None:
    from aura_lifecycle import cmd_e
    a, b = cmd_e.dispel_fixtures(), cmd_e.dispel_fixtures()
    assert a == b
    f = {x["id"]: x for x in a}
    assert f["DF-01"]["success"] == [("B", 1), ("A", 1)]
    assert f["DF-05"]["success"] == [("X1", 2)]
    assert f["DF-04"]["removed"] is None
