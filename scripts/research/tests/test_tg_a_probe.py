"""Track A: differential tests of the selector vocabulary against Trinity's compiled code.

The probe (tools/tc_target_selector_probe) compiles the verbatim
``SpellImplicitTargetInfo::_data`` / ``SpellEffectInfo::_data`` tables and the
functions that read them.  These tests rule out the "hand transcription is
right" model: every raw id 0..TOTAL_SPELL_TARGETS-1 is compared on all five
axes, the binary32 direction angle, IsArea, GetTargetFlagMask and
GetExplicitTargetMask for all four (srcSet, dstSet) inputs.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from targeting import TC_ROOT, FailClosed
from targeting import attributes as A
from targeting import composition as C
from targeting import selectors as S

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_target_selector_probe"
GAME = TC_ROOT / "src" / "server" / "game"


@pytest.fixture(scope="session")
def probe() -> Path:
    if not (GAME / "Spells" / "SpellInfo.cpp").is_file():
        pytest.skip("sibling TrinityCore checkout not present")
    if shutil.which("g++") is None:
        pytest.skip("g++ not available")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE_DIR / "probe"


def _run(probe: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run([str(probe), *args], check=True, capture_output=True, text=True, input=stdin).stdout


@pytest.mark.parametrize("rand_norm", ["0", "0.5", "0.999999"])
def test_every_selector_matches_probe(probe: Path, rand_norm: str) -> None:
    doc = json.loads(_run(probe, "selectors", rand_norm))
    assert doc["TOTAL_SPELL_TARGETS"] == S.TOTAL_SPELL_TARGETS
    assert len(doc["targets"]) == S.TOTAL_SPELL_TARGETS
    rnd = float.fromhex(float(rand_norm).hex())
    for t in doc["targets"]:
        i = S.info(t["id"])
        assert (i.object, i.reference, i.category, i.check, i.direction) == (
            S.OBJECT[t["object"]], S.REFERENCE[t["reference"]], S.CATEGORY[t["category"]],
            S.CHECK[t["check"]], S.DIRECTION[t["direction"]]), t["id"]
        assert S.f32_hex(i.direction_angle(S.f32(rnd))) == t["angle"], t["id"]
        assert i.is_area == t["is_area"], t["id"]
        assert i.target_flag_mask == t["target_flag_mask"], t["id"]
        for e in t["explicit"]:
            got = i.explicit_target_mask(bool(e["src_in"]), bool(e["dst_in"]))
            assert got == (e["mask"], bool(e["src_out"]), bool(e["dst_out"])), (t["id"], e)


def test_effect_static_table_matches_probe(probe: Path) -> None:
    doc = json.loads(_run(probe, "effects"))
    assert doc["TOTAL_SPELL_EFFECTS"] == S.TOTAL_SPELL_EFFECTS
    for r in doc["effects"]:
        assert S.effect_implicit_target_type(r["id"]) == r["implicit_target_type"], r["id"]
        assert S.effect_used_object(r["id"]) == r["used_object"], r["id"]
        assert C.missing_target_mask(C.EffectSlots(0, r["id"], 0, 0)) == r["missing_mask_no_targets"], r["id"]


def test_random_direction_fails_closed_without_draw() -> None:
    """Rules out 'RANDOM defaults to 0': the angle needs a rand_norm() draw."""
    with pytest.raises(FailClosed):
        S.info(72).direction_angle()


def test_out_of_table_selector_fails_closed() -> None:
    with pytest.raises(FailClosed):
        S.info(S.TOTAL_SPELL_TARGETS)


def test_synthetic_explicit_masks_match_probe(probe: Path) -> None:
    """Shared srcSet/dstSet across effects and A->B order (SpellInfo.cpp:4572) on crafted spells."""
    cases = [
        (1, [(2, 6, 16, 0)]),                       # unit + dest-area -> DEST requested
        (2, [(2, 18, 16, 0)]),                      # dest writer suppresses DEST request
        (3, [(2, 16, 0, 0), (2, 18, 0, 0)]),        # order matters: area first requests DEST
        (4, [(2, 18, 0, 0), (2, 16, 0, 0)]),        # writer first: no DEST
        (5, [(2, 116, 16, 0)]),                     # UNIT_AND_DEST sets dstSet without DEST flag
        (6, [(6, 0, 0, 0)]),                        # effect-type EXPLICIT fills UNIT
        (7, [(6, 0, 0, 0x100000)]),                 # DontFail -> not required
        (8, [(3, 0, 0, 0)]),                        # DUMMY (NONE) adds nothing
        (9, [(2, 89, 0, 0)]),                       # TRAJ requests SRC|DEST
        (10, [(2, 22, 15, 0), (2, 0, 30, 0)]),      # src writer then SRC-ref area
    ]
    lines, expected = [], {}
    for sid, effs in cases:
        for has_range, r0, r1, targets, a13 in ((1, 40.0, 0.0, 0, 0), (0, 0.0, 0.0, 0x40, 0x8000), (1, 0.0, 0.0, 0x10, 0)):
            key = sid * 10 + len(expected) % 10
            slots = [C.EffectSlots(i, e, a, b, at) for i, (e, a, b, at) in enumerate(effs)]
            expected[len(expected)] = C.explicit_target_mask(
                slots, max_range_negative=r0 if has_range else 0.0, max_range_positive=r1 if has_range else 0.0,
                targets=targets, attributes13=a13)
            parts = [str(len(expected) - 1), str(has_range), repr(r0), repr(r1), str(targets), str(a13), str(len(effs))]
            for e, a, b, at in effs:
                parts += [str(e), str(a), str(b), str(at)]
            lines.append(" ".join(parts))
            del key
    out = [json.loads(ln) for ln in _run(probe, "explicit", stdin="\n".join(lines) + "\n").splitlines()]
    assert len(out) == len(expected)
    for o in out:
        assert (o["explicit"], o["required"]) == expected[o["spell"]], o


@pytest.mark.snapshot
def test_explicit_masks_all_player_spells(probe: Path, tg_ctx) -> None:
    """Every current-player spell's ExplicitTargetMask / RequiredExplicitTargetMask equals the probe's."""
    spells = sorted(s for s in tg_ctx.scope.reach if any(e.is_effect for e in tg_ctx.data.effects(s)))
    lines = [C.probe_input_line(tg_ctx, s) for s in spells]
    out = [json.loads(ln) for ln in _run(probe, "explicit", stdin="\n".join(lines) + "\n").splitlines()]
    assert len(out) == len(spells)
    for o in out:
        assert "error" not in o, o
        assert (o["explicit"], o["required"]) == C.explicit_mask_for(tg_ctx, o["spell"]), o


def test_target_names_match_shared_defines() -> None:
    path = GAME / "Miscellaneous" / "SharedDefines.h"
    if not path.is_file():
        pytest.skip("sibling TrinityCore checkout not present")
    body = re.search(r"\nenum Targets\n\{(.*?)\n\};", path.read_text(encoding="utf-8"), re.S).group(1)
    names = {int(v): k for k, v in re.findall(r"(TARGET_\w+)\s*=\s*(\d+)", body)}
    assert names == S.TARGET_NAMES
    assert re.search(r"\bTOTAL_SPELL_TARGETS\b", body)


ANCHORS = {
    "Spells/Spell.cpp": {
        787: "SelectEffectImplicitTargets(spellEffectInfo, spellEffectInfo.TargetA",
        788: "SelectEffectImplicitTargets(spellEffectInfo, spellEffectInfo.TargetB",
        800: "AddDestTarget(*m_targets.GetDst(), spellEffectInfo.EffectIndex)",
        1021: "is not implemented yet",
        1284: "case TARGET_UNIT_CONE_180_DEG_ENEMY:",
        1301: "WorldObjectSpellConeTargetCheck check(*m_caster",
        1393: "case TARGET_UNIT_TARGET_ALLY_OR_RAID:",
        1403: "case TARGET_UNIT_CASTER_AND_SUMMONS:",
        1434: "m_targets.ModDst(dest);",
        1439: "TARGET_UNIT_SRC_AREA_FURTHEST_ENEMY",
        1619: "dist = objSize + (dist - objSize);",
        1696: "CheckDst();",
        1749: "case TARGET_UNIT_CASTER:",
        1790: "default:",
        1848: "TARGET_UNIT_TARGET_CHAINHEAL_ALLY",
        2466: "if (ihit != std::end(m_UniqueTargetInfo))",
        2469: "ihit->EffectMask |= effectMask;",
        7268: "void Spell::CheckDst()",
        7300: "SPELL_FACING_FLAG_INFRONT",
    },
    "Spells/SpellInfo.cpp": {
        246: "SpellImplicitTargetInfo::_data",
        1510: "MaxTargetLevel = _target->MaxTargetLevel;",
        2633: "TargetCreatureType",
        4572: "void SpellInfo::_InitializeExplicitTargetMask()",
    },
    "Spells/SpellMgr.cpp": {
        3244: "fuzzyNe(spellInfoMutable->Width, 0.0f)",
        3268: "_InitializeExplicitTargetMask();",
        5282: "fuzzyEq(spellInfo->ConeAngle, 0.f)",
        5283: "spellInfo->ConeAngle = 90.f;",
        5286: "IsAreaAuraEffect() && spellEffectInfo.IsTargetingArea()",
        5407: "void SpellMgr::LoadSpellInfoTargetCaps()",
    },
    "World/World.cpp": {1375: "LoadSpellInfoCorrections", 1381: "LoadSpellInfoCustomAttributes",
                        1390: "LoadSpellInfoTargetCaps"},
}


def test_consumer_anchors() -> None:
    """file:line citations used by the corpora still point at the cited code."""
    if not GAME.is_dir():
        pytest.skip("sibling TrinityCore checkout not present")
    for rel, anchors in ANCHORS.items():
        lines = (GAME / rel).read_text(encoding="utf-8").split("\n")
        for line, text in anchors.items():
            assert text in lines[line - 1], (rel, line, lines[line - 1])


def test_column_consumer_anchors() -> None:
    if not GAME.is_dir():
        pytest.skip("sibling TrinityCore checkout not present")
    files = {"Spell.cpp": "Spells/Spell.cpp", "SpellInfo.cpp": "Spells/SpellInfo.cpp",
             "SpellMgr.cpp": "Spells/SpellMgr.cpp", "Unit.cpp": "Entities/Unit/Unit.cpp"}
    keywords = {"ConeDegrees": "Cone", "Width": "Width", "MaxTargets": "MaxAffectedTargets", "Targets": "Targets",
                "TargetCreatureType": "TargetCreatureType", "MaxTargetLevel": "MaxTargetLevel",
                "FacingCasterFlags": "FacingCasterFlags", "ShapeshiftMask/Exclude": "CheckShapeshift"}
    for col, rows in A.COLUMN_CONSUMERS.items():
        leaf = col.split(".")[1]
        kw = keywords.get(leaf, leaf)
        for r in rows:
            f, line = r["consumer"].split(":")
            text = (GAME / files[f]).read_text(encoding="utf-8").split("\n")[int(line) - 1]
            if kw == "Cone":
                assert "Cone" in text or "cone" in text, (col, r, text)
            elif leaf == "MaxTargets" and r["stage"] == "load-rewrite":
                assert "IsSingleTarget" in text, (col, r, text)
            else:
                assert kw in text or (leaf == "Targets" and "Targets" in text), (col, r, text)


def test_target_caps_parse_matches_table() -> None:
    if not GAME.is_dir():
        pytest.skip("sibling TrinityCore checkout not present")
    assert A.target_caps_from_source() == A.TARGET_CAPS


def test_consumer_scan_finds_known_reads() -> None:
    if not GAME.is_dir():
        pytest.skip("sibling TrinityCore checkout not present")
    by = A.consumers_by_token()
    assert {"function": "Spell::SearchChainTargets", "stage": "chain", "consumer": "Spell.cpp:2253"} in \
        by["SPELL_ATTR2_CHAIN_FROM_CASTER"]
    assert any(r["consumer"] == "Spell.cpp:2150" for r in by["SpellEffectAttributes::PlayersOnly"])
    # CheckCast region excludes non-target reads (e.g. ONLY_OUTDOORS)
    assert all(r["function"] != "Spell::CheckCast" for r in by.get("SPELL_ATTR0_ONLY_OUTDOORS", []))
    assert any(r["function"] == "Spell::CheckCast" for r in by["SPELL_ATTR0_CU_REQ_CASTER_BEHIND_TARGET"])


def test_core_catalog_axes_agree() -> None:
    try:
        x = S.core_catalog_crosscheck()
    except FailClosed:
        pytest.skip("core checkout not present")
    assert x["core_rows"] == S.TOTAL_SPELL_TARGETS
    assert x["axis_mismatches"] == []
