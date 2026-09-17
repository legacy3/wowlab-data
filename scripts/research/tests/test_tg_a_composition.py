"""Track A: TargetA/TargetB composition discriminators (pure, symbolic).

Each test names the wrong model it rules out.  The symbolic model mirrors
Spell.cpp:621-800 (InitExplicitTargets + SelectSpellTargets ordering and the
m_targets reads/writes of every SelectImplicit* routine); geometry and checks
are not modelled here (other tracks).
"""

from __future__ import annotations

import pytest

from targeting import FailClosed
from targeting import composition as C

E = C.EffectSlots
DAMAGE, AURA, DUMMY = 2, 6, 3


def run(effects, client, caster="caster"):
    effs = [E(i, eff, a, b) for i, (eff, a, b) in enumerate(effects)]
    mask, _ = C.explicit_target_mask(effs, max_range_negative=40.0, max_range_positive=0.0)
    return C.simulate(effs, caster, client, mask), mask


def test_b_never_intersects_a() -> None:
    """Rules out "B refines A": (6,16) yields the explicit unit AND the area set, unioned by AddUnitTarget."""
    out, _ = run([(DAMAGE, 6, 16)], {"unit": "t1"})
    sets = [r["set"] for r in out["recipient_sets"]]
    assert sets == ["t1", "area[ENEMY]@pos(t1)"]
    assert all(r["mask"] == 1 for r in out["recipient_sets"])
    assert all(C.classify(a, b)["relation"] != "refine" for a in range(0, 153) for b in (0, 6, 15, 16, 31))


def test_b_uses_a_dest_not_client_dest() -> None:
    """Rules out "DEST-referenced B uses the client's ground point": (18,16) centres on the caster (A's SetDst);
    the client dst is even removed because the mask no longer requests DEST_LOCATION."""
    out, mask = run([(DAMAGE, 18, 16)], {"unit": "t1", "dst": "ground"})
    assert mask == 0
    assert out["recipient_sets"][0]["centre"] == "pos(caster)"
    assert any(t.get("removed_dst") == "ground" for t in out["trace"])
    assert C.classify(18, 16)["relation"] == "b-uses-a-dest"


def test_unit_then_dest_area_reads_explicit_dest() -> None:
    """Rules out "B's centre is A's unit": (6,16) requests DEST_LOCATION; with a client dst the area is centred on it,
    without one InitExplicitTargets synthesises the explicit target position (Spell.cpp:666-677)."""
    with_dst, mask = run([(DAMAGE, 6, 16)], {"unit": "t1", "dst": "ground"})
    assert mask & 0x40
    assert with_dst["recipient_sets"][1]["centre"] == "ground"
    no_dst, _ = run([(DAMAGE, 6, 16)], {"unit": "t1"})
    assert no_dst["recipient_sets"][1]["centre"] == "pos(t1)"


def test_effect_order_changes_explicit_request_and_centre() -> None:
    """Rules out "effects are independent": dst written by effect 0 is read by effect 1 (m_targets is spell-wide),
    and the shared dstSet flag removes the DEST request (SpellInfo.cpp:4572)."""
    writer_first, m1 = run([(DAMAGE, 18, 0), (DAMAGE, 16, 0)], {"unit": "t1", "dst": "ground"})
    area_first, m2 = run([(DAMAGE, 16, 0), (DAMAGE, 18, 0)], {"unit": "t1", "dst": "ground"})
    assert not m1 & 0x40 and m2 & 0x40
    assert writer_first["recipient_sets"][0]["centre"] == "pos(caster)"
    assert area_first["recipient_sets"][0]["centre"] == "ground"
    # AddDestTarget snapshots after each effect
    assert area_first["dests"] == {0: "ground", 1: "pos(caster)"}


def test_src_writer_overrides_client_src() -> None:
    """Rules out "SRC area uses the client src": (22,15) removes the client src (no SOURCE_LOCATION request) and A sets caster."""
    out, mask = run([(AURA, 22, 15)], {"unit": None, "dst": None, "src": "client-src", "selection": None})
    assert not mask & 0x20
    assert out["recipient_sets"][0]["centre"] == "pos(caster)"


def test_dest_dest_is_relative_to_existing_dest() -> None:
    """Rules out "DEST_DEST is relative to the caster": A=53 puts dst at the target, B=87 keeps it (no-op branch)."""
    out, _ = run([(DAMAGE, 53, 87)], {"unit": "t1"})
    assert out["final_dst"] == "pos(t1)"
    moved, _ = run([(DAMAGE, 53, 148)], {"unit": "t1"})
    assert moved["final_dst"] == "moved(pos(t1),148)"


def test_dest_dest_without_dest_falls_back_to_caster() -> None:
    """CheckDst (Spell.cpp:1696, 7268): a DEST_DEST selector with no dst uses the caster position."""
    effs = [E(0, DAMAGE, 87, 0)]
    out = C.simulate(effs, "caster", {"unit": None, "selection": None}, explicit_mask=0)
    assert out["final_dst"] == "pos(caster)"


def test_last_reference_centres_on_a_recipient() -> None:
    """Rules out "LAST = caster": B=37 centres on the last unique target carrying the effect bit (A's target)."""
    out, _ = run([(AURA, 21, 37)], {"unit": "ally"})
    assert out["recipient_sets"][1]["centre"] == "pos(ally)"
    lone = C.simulate([E(0, AURA, 37, 0)], "caster", {"unit": None, "selection": None}, 0)
    assert lone["recipient_sets"][0]["centre"] == "pos(caster)"


def test_unit_and_dest_without_dst_asserts() -> None:
    """TARGET_UNIT_AND_DEST_LAST_ENEMY alone: explicit mask has no DEST flag, ModDst ASSERTs (Spell.cpp:1434)."""
    effs = [E(0, DAMAGE, 116, 0)]
    mask, _ = C.explicit_target_mask(effs, max_range_negative=40.0, max_range_positive=0.0)
    assert not mask & 0x40
    with pytest.raises(FailClosed):
        C.simulate(effs, "caster", {"unit": None, "dst": "ground", "selection": None}, mask)
    ok = C.simulate([E(0, DAMAGE, 18, 116)], "caster", {"unit": None, "selection": None}, 0)
    assert ok["final_dst"] == "pos(caster)"


def test_b_overwrites_a_dest() -> None:
    out, _ = run([(28, 18, 32)], {"unit": None, "selection": None})
    assert out["final_dst"].endswith("/FRONT_LEFT")
    assert C.classify(18, 32)["relation"] == "b-overwrites-a-dest"


def test_caster_to_dest_cone_ignores_dest() -> None:
    """Rules out "CONE_CASTER_TO_DEST orients to dst" under Trinity: centre/orientation is the caster (Spell.cpp:1301)."""
    out, _ = run([(DAMAGE, 18, 104)], {"unit": None, "selection": None})
    assert out["recipient_sets"][0]["set"] == "cone[ENEMY]@pos(caster)"
    assert C.classify(18, 104)["relation"] == "a-dest-b-independent"


def test_grouped_effects_select_once() -> None:
    """Rules out "each effect selects separately": identical (A,B) effects share one selection with a combined mask."""
    out, _ = run([(DAMAGE, 6, 0), (AURA, 6, 0), (AURA, 1, 0)], {"unit": "t1"})
    assert [(r["set"], r["mask"]) for r in out["recipient_sets"]] == [("t1", 0b011), ("caster", 0b100)]


def test_empty_pair_is_effect_type_driven() -> None:
    assert C.classify(0, 0)["relation"] == "empty"
    # SCHOOL_DAMAGE with no selectors requests an explicit unit (EffectImplicitTargetTypes EXPLICIT)
    m, _ = C.explicit_target_mask([E(0, DAMAGE, 0, 0)], max_range_negative=40.0, max_range_positive=0.0)
    assert m == 0x2
    # ... but not when the spell has no range at all (SpellInfo.cpp:4592-4594)
    m0, _ = C.explicit_target_mask([E(0, DAMAGE, 0, 0)], max_range_negative=0.0, max_range_positive=0.0)
    assert m0 == 0


def test_nearby_dest_then_area() -> None:
    out, _ = run([(DAMAGE, 46, 16)], {"unit": None, "selection": None})
    assert out["recipient_sets"][0]["centre"] == "nearest[ENTRY]"


def test_every_pair_has_a_relation() -> None:
    """The classification is total over 153 x 153 raw ids (NYI slots behave as empty, Spell.cpp:1020)."""
    rels = {C.classify(a, b)["relation"] for a in range(153) for b in range(153)}
    assert rels <= set(C.RELATIONS)
    assert C.classify(10, 6)["relation"] == "b-only"
