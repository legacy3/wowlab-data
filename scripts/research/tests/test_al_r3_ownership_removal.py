"""Hostile review R3 (ownership / removal): source-anchored checks against pinned Trinity and the corpora.

Each ``xfail(strict=True)`` test encodes a corpus claim R3 found to be wrong or incomplete; it passes while the
defect exists and turns into an XPASS failure once the corpus is corrected (then drop the marker).  The plain
tests pin the Trinity facts the review relies on.  No context load: fast, stdlib + pinned sources only.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

import pytest

from aura_lifecycle import CORPORA, TC_ROOT

GAME = TC_ROOT / "src" / "server" / "game"


def _src(rel: str) -> str:
    path = GAME / rel
    if not path.exists():
        pytest.skip("pinned TrinityCore absent")
    return path.read_text(encoding="utf-8", errors="replace")


def _body(text: str, signature: str) -> str:
    """Brace-matched body of the first function whose definition line starts with ``signature``."""
    start = text.index(signature)
    i = text.index("\n{", start) + 1
    depth = 0
    for j in range(i, len(text)):
        depth += text[j] == "{"
        depth -= text[j] == "}"
        if depth == 0:
            return text[i:j + 1]
    raise AssertionError(signature)


@lru_cache(maxsize=None)
def _corpus(name: str) -> dict:
    path = CORPORA / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{name}.json absent")
    return json.loads(path.read_text())


def _all_game_sources() -> str:
    if not GAME.exists():
        pytest.skip("pinned TrinityCore absent")
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in GAME.rglob("*.cpp"))


# --- Trinity facts --------------------------------------------------------------------------------------------

def test_ghost_only_sweep_has_a_single_login_caller() -> None:
    """Rules out 'resurrect runs RemoveAllAurasRequiringDeadTarget': the only caller is Player::LoadFromDB."""
    callers = [line for line in _all_game_sources().splitlines()
               if "RemoveAllAurasRequiringDeadTarget()" in line and "void" not in line]
    assert len(callers) == 1
    player = _src("Entities/Player/Player.cpp")
    resurrect = _body(player, "void Player::ResurrectPlayer(")
    assert "RemoveAllAurasRequiringDeadTarget" not in resurrect
    assert "RemoveAurasDueToSpell(8326)" in resurrect and "RemoveAurasDueToSpell(20584)" in resurrect


def test_single_target_cap_removes_oldest_in_add_aura() -> None:
    """Unit::_AddAura evicts single-target auras over MaxAffectedTargets with Aura::Remove() (DEFAULT)."""
    body = _body(_src("Entities/Unit/Unit.cpp"), "void Unit::_AddAura(")
    assert "MaxAffectedTargets - 1" in body
    assert "aurasSharingLimit.back()->Remove();" in body


def test_spell_cancel_removes_dynobjects_of_the_spell() -> None:
    """Spell::cancel (death -> InterruptNonMeleeSpells) removes every dynobject of that spell id of the caster."""
    body = _body(_src("Spells/Spell.cpp"), "void Spell::cancel(")
    assert "m_originalCaster->RemoveDynObject(m_spellInfo->Id);" in body


def test_owner_death_releases_charms_and_vehicle_before_sweep() -> None:
    """Caster death removes auras it cast on OTHER holders: charm auras (RemoveAllControlled) and the vehicle's
    CONTROL_VEHICLE aura (ExitVehicle); both run inside setDeathState before RemoveAllAurasOnDeath."""
    unit = _src("Entities/Unit/Unit.cpp")
    death = _body(unit, "void Unit::setDeathState(")
    order = [death.index(k) for k in ("CombatStop()", "InterruptNonMeleeSpells(false)", "ExitVehicle()",
                                      "UnsummonAllTotems()", "RemoveAllControlled()", "RemoveAllAurasOnDeath();")]
    assert order == sorted(order)
    assert "target->RemoveCharmAuras();" in _body(unit, "void Unit::RemoveAllControlled(")
    assert "RemoveAurasByType(SPELL_AURA_CONTROL_VEHICLE, GetGUID())" in _body(unit, "void Unit::ExitVehicle(")


def test_passive_stackable_with_ranks_skips_no_stack_removal() -> None:
    """IsPassiveStackableWithRanks = passive without SPELL_EFFECT_APPLY_AURA -> _RemoveNoStackAurasDueToAura returns."""
    info = _src("Spells/SpellInfo.cpp")
    assert "return IsPassive() && !HasEffect(SPELL_EFFECT_APPLY_AURA);" in _body(
        info, "bool SpellInfo::IsPassiveStackableWithRanks(")
    body = _body(_src("Entities/Unit/Unit.cpp"), "void Unit::_RemoveNoStackAurasDueToAura(")
    assert body.index("IsPassiveStackableWithRanks()") < body.index("IsHighestExclusiveAura(aura)")


def test_recipient_map_blocks_rather_than_replaces() -> None:
    """A second owner's area aura is not added to a unit already holding a non-stackable aura (target != owner);
    nothing is removed on that path.  Rules out 'other caster replaces' for the area part."""
    body = _body(_src("Spells/Auras/SpellAuras.cpp"), "void Aura::UpdateTargetMap(")
    block = body[body.index("if (itr->first != GetOwner())"):body.index("if (!addUnit)")]
    assert "if (!CanStackWith(aura))" in block and "addUnit = false;" in block
    assert "Remove" not in block


def test_shared_object_adopts_latest_base_points() -> None:
    """Any-caster shared object: the refresh overwrites m_baseAmount with the new applier's base points while the
    Aura keeps its first caster (Unit.cpp _TryStackingOrRefreshingExistingAura)."""
    body = _body(_src("Entities/Unit/Unit.cpp"), "Aura* Unit::_TryStackingOrRefreshingExistingAura(")
    assert "*oldBP = bp;" in body and "createInfo.BaseAmount" in body
    assert "m_casterGuid" not in body


UNCONSUMED_INTERRUPT_FLAGS = {
    ("1", "DamageCancelsScript"), ("2", "NotMoving"), ("2", "Disconnect"), ("2", "SeamlessTransfer"),
    ("2", "TouchingGround"), ("2", "ChromieTime"), ("2", "SplineFlightOrFreeFlight"),
    ("2", "ProcOrPeriodicAttacking"),
}


def _interrupt_flag_names(defines: str, enum: str) -> list[str]:
    block = re.search(rf"enum class {enum}\b[^{{]*{{(.*?)}};", defines, re.S)
    assert block
    return re.findall(r"^\s+([A-Za-z0-9]+)\s*=", block.group(1), re.M)


def test_interrupt_flags_without_consumer() -> None:
    """Eight aura-interrupt bits are never passed to RemoveAurasWithInterruptFlags / referenced anywhere else."""
    defines = _src("Spells/SpellDefines.h")
    code = _all_game_sources()
    found = set()
    for tag, enum in (("1", "SpellAuraInterruptFlags"), ("2", "SpellAuraInterruptFlags2")):
        for name in _interrupt_flag_names(defines, enum):
            if name in ("None",):
                continue
            if not re.search(rf"{enum}::{name}\b", code):
                found.add((tag, name))
    assert found == UNCONSUMED_INTERRUPT_FLAGS


# --- corpus claims R3 rejects (strict xfail: flip when fixed) -------------------------------------------------

def test_rm19_does_not_claim_resurrect() -> None:
    row = next(r for r in _corpus("removal")["taxonomy"] if r["id"] == "RM-19")
    assert "resurrect" not in row["reason"]


def test_taxonomy_has_single_target_cap_row() -> None:
    rows = _corpus("removal")["taxonomy"]
    assert any("MaxAffectedTargets" in r["predicate"] or "cap" in r["reason"] for r in rows)


def test_lifetime_does_not_claim_dynobjects_survive_death_unconditionally() -> None:
    rows = _corpus("lifetime")["matrix"]
    assert not any("dynobjects are not removed on death" in r.get("outcome", "") for r in rows)


def test_area_only_passive_providers_not_replace() -> None:
    rows = _corpus("identity")["player_rows"]
    witnesses = ("1270083", "363558", "368412", "400129", "404752")
    assert all(rows[w]["other_caster"] != "replace" for w in witnesses if w in rows)


def test_death_order_lists_exit_vehicle() -> None:
    steps = _corpus("lifetime")["death_order_fixture"]["steps"]
    assert any("ExitVehicle" in s["step"] for s in steps)
