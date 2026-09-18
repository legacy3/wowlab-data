"""Track C: differential of ``aura_lifecycle.stacks`` / ``charges`` against verbatim Trinity code.

The probe (tools/tc_aura_stack_probe) compiles Trinity's own ``Aura::ModStackAmount``, ``SetStackAmount``,
``CalcMaxStackAmount``, ``IsUsingStacks``, ``ModCharges``, ``SetCharges``, ``CalcMaxCharges``,
``RefreshTimers``/``RefreshDuration``/``SetDuration``, ``PrepareProcChargeDrop``, ``ConsumeProcCharges``, the
charge statements of ``Aura::Aura``, ``Unit::_TryStackingOrRefreshingExistingAura`` (the refresh branch of
``TryRefreshStackOrCreate``), ``SpellInfo::IsMultiSlotAura`` / ``IsStackableOnOneSlotWithDifferentCasters`` /
``IsChanneled`` / ``IsPassive``, ``Player::ApplySpellMod<T>`` and the ResetPeriodicTimer statement of
``Spell::DoSpellEffectHit``.  Random scenarios (fixed seed) compare every state field after every step.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from aura_lifecycle import TC_ROOT
from aura_lifecycle import charges as C
from aura_lifecycle import stacks as S

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_aura_stack_probe"
PROBE = PROBE_DIR / "probe"

A0_PASSIVE = 0x40
A1_CHANNELLED, A1_SELF_CHANNELLED, A1_AURA_UNIQUE = 0x4, 0x40, 0x800
A3_DOT_STACKING_RULE = 0x80
A5_AURA_UNIQUE_PER_CASTER = 0x20000000
A13_PANDEMIC = 0x00100000
CU_ENCHANT_PROC = 0x1
PROC_ATTR_USE_STACKS = 0x10
TRIGGERED_DONT_RESET_PERIODIC_TIMER = 0x00020000
MODES = {1: "AURA_REMOVE_BY_DEFAULT", 4: "AURA_REMOVE_BY_ENEMY_SPELL", 5: "AURA_REMOVE_BY_EXPIRE"}


@pytest.fixture(scope="module")
def probe():
    if not (TC_ROOT / "src/server/game/Spells/Auras/SpellAuras.cpp").exists():
        pytest.skip("sibling TrinityCore checkout absent")
    if shutil.which("g++") is None and not PROBE.exists():
        pytest.skip("no compiler")
    subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True, capture_output=True)
    return PROBE


def run(probe: Path, lines: list[str]) -> list[dict]:
    out = subprocess.run([str(probe)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True)
    return [json.loads(x) for x in out.stdout.splitlines()]


class Scenario:
    """One aura: probe protocol lines + the Python model driven in lock step."""

    def __init__(self, rng: random.Random) -> None:
        self.cap = rng.choice([0, 0, 1, 2, 3, 5, 9, 255, 300])
        self.charges_db2 = rng.choice([0, 0, 1, 3, 15])
        self.a0 = A0_PASSIVE if rng.random() < 0.15 else 0
        self.a1 = rng.choice([0, 0, A1_AURA_UNIQUE, A1_CHANNELLED])
        self.a3 = A3_DOT_STACKING_RULE if rng.random() < 0.2 else 0
        self.a5 = A5_AURA_UNIQUE_PER_CASTER if rng.random() < 0.15 else 0
        self.a13 = A13_PANDEMIC if rng.random() < 0.2 else 0
        self.cu = CU_ENCHANT_PROC if rng.random() < 0.15 else 0
        self.proc = rng.random() < 0.6
        self.proc_charges = rng.choice([0, 0, 2, 5])
        self.use_stacks = rng.random() < 0.3
        self.mods: dict[int, tuple[int, float]] = {}
        if rng.random() < 0.4:
            self.mods[37] = (rng.choice([-99, -3, 1, 2, 19]), rng.choice([1.0, 1.0, 0.5, 2.0]))
        if rng.random() < 0.3:
            self.mods[4] = (rng.choice([-16, -1, 1, 3]), 1.0)
        self.effects = [(0, float(rng.choice([1, 7, 20, -5])), rng.choice([0, 0x40, 0x200])),
                        (1, float(rng.choice([0, 3])), rng.choice([0, 0x200]))]
        self.maxdur = rng.choice([-1, 10000, 600000])

    # -- derived model inputs ------------------------------------------------
    def max_stacks(self) -> int:
        return S.calc_max_stack_amount(self.cap, self.mods.get(37) if self.mods else None)

    def max_charges(self) -> int:
        entry = (self.proc_charges or self.charges_db2) if self.proc else None
        return C.calc_max_charges(self.charges_db2, entry, self.mods.get(4) if self.mods else None)

    def shape(self) -> S.AuraShape:
        return S.AuraShape(capacity=self.cap, max_stacks=self.max_stacks(), max_charges=self.max_charges(),
                           aura_unique=bool(self.a1 & A1_AURA_UNIQUE), aura_unique_per_caster=bool(self.a5),
                           pandemic=bool(self.a13), max_duration=self.maxdur)

    def header(self) -> list[str]:
        lines = ["reset", f"spell 900 {self.cap} {self.charges_db2} {self.a0} {self.a1} {self.a3} {self.a5} 0 {self.a13} "
                          f"{self.cu} {self.maxdur}"]
        lines += [f"effect {i} {bp!r} {ea}" for i, bp, ea in self.effects]
        # a spell_proc row with Charges 0 falls back to the DB2 value at load (SpellMgr.cpp:1576)
        entry_charges = (self.proc_charges or self.charges_db2) if self.proc else 0
        lines.append(f"proc {int(self.proc)} {entry_charges} {PROC_ATTR_USE_STACKS if self.use_stacks else 0}")
        for op, (flat, pct) in sorted(self.mods.items()):
            lines.append(f"mod {op} {flat} {pct!r}")
        lines.append(f"calcmaxdur {self.maxdur}")
        return lines


def expected_refresh(events: list[dict]) -> tuple[bool, bool | None]:
    for e in events:
        if e["op"] == "refresh_timers":
            return True, e["reset_periodic_timer"]
    return False, None


def probe_refresh(events: list[str]) -> tuple[bool, bool | None]:
    if "calc_max_duration" not in events:
        return False, None
    flags = {e.split(":")[2][0] == "1" for e in events if e.startswith("calculate_periodic:")}
    return True, (flags.pop() if len(flags) == 1 else None)


def compare(state: S.StackState, got: dict, where: str) -> None:
    g = got["state"]
    want = {"stacks": state.stacks, "charges": state.charges, "using_charges": state.using_charges,
            "removed": state.removed}
    have = {k: g[k] for k in want}
    assert have == want, f"{where}: model {want} probe {have}"
    if not state.removed:
        assert (g["duration"], g["max_duration"]) == (state.duration, state.max_duration), where
    else:
        mode = {v: k for k, v in MODES.items()}[state.remove_mode]
        assert g["remove_mode"] == mode, where


def test_random_scenarios_match_probe(probe):
    """Rules out: cap-on-decrease, refresh-on-decrease, CumulativeAura-as-initial-stacks, charges consumed without an
    entry, AURA_UNIQUE ignored, pandemic not suppressing the periodic reset, no uint8 wrap."""
    rng = random.Random(0xA11C)
    checked = 0
    for n in range(400):
        sc = Scenario(rng)
        shape = sc.shape()
        lines = sc.header()
        requested = rng.choice([1, 1, 0, 2, 7, 300])
        lines.append(f"create {requested} 11 0 {sc.maxdur}")
        state = S.initial_state(shape, requested)
        state.duration = state.max_duration = sc.maxdur
        steps: list[tuple[str, object]] = []
        for _ in range(rng.randint(1, 8)):
            op = rng.choice(["modstack", "modstack", "setstack", "modcharges", "proc", "reapply"])
            if op == "modstack":
                num, mode, reset = rng.choice([-3, -1, 0, 1, 1, 2, 5]), rng.choice([1, 4, 5]), rng.random() < 0.5
                lines.append(f"modstack {num} {mode} {int(reset)}")
                steps.append((op, lambda st, num=num, mode=mode, reset=reset:
                              S.mod_stack_amount(st, shape, num, MODES[mode], reset)))
            elif op == "setstack":
                v = rng.choice([0, 1, 4, 255, 256, 300])
                lines.append(f"setstack {v}")
                steps.append((op, lambda st, v=v: S.set_stack_amount(st, S.u8(v))))
            elif op == "modcharges":
                num, mode = rng.choice([-2, -1, 1, 4]), rng.choice([1, 4])
                lines.append(f"modcharges {num} {mode}")
                steps.append((op, lambda st, num=num, mode=mode: C.mod_charges(st, shape, num, MODES[mode])))
            elif op == "proc":
                if not sc.proc:
                    continue
                noc = rng.random() < 0.2
                lines += [f"prepare {int(noc)}", "consume"]
                steps.append(("prepare", lambda st, noc=noc: C.prepare_proc_charge_drop(st, sc.use_stacks, noc)))
                steps.append(("consume", lambda st: C.consume_proc_charges(st, shape, sc.use_stacks)))
            else:
                amount, reset = rng.choice([1, 1, 2, 0]), rng.random() < 0.5
                caster = rng.choice([11, 11, 12])
                item = rng.choice([0, 0, 5])
                match = rng.random() < 0.9
                lines.append(f"reapply {amount} {int(reset)} {caster} {item} {3 if match else 1} - -")
                one_slot = S.is_stackable_on_one_slot_with_different_casters(
                    sc.cap, bool(sc.a1 & (A1_CHANNELLED | A1_SELF_CHANNELLED)), bool(sc.a3))
                key_caster, key_item = S.owned_aura_key(one_slot, caster, bool(sc.cu), item)
                found = (key_caster in (0, 11)) and (key_item in (0, 0))
                inc = S.Reapply(stack_amount=S.create_info_stack_amount(amount), reset_periodic=reset,
                                effect_mask_matches=match, multislot=S.is_multi_slot(900, bool(sc.a0)), found=found)
                steps.append(("reapply", lambda st, inc=inc: S.reapply(st, shape, inc)))
        results = run(probe, lines)
        assert len(results) == 1 + len(steps), f"scenario {n}: step count"
        compare(state, results[0], f"scenario {n} create")
        for i, ((op, fn), got) in enumerate(zip(steps, results[1:])):
            if state.removed:
                break
            events = fn(state)
            where = f"scenario {n} step {i} {op} ({lines[len(sc.header()) + 1:]})"
            compare(state, got, where)
            if op in ("modstack", "reapply"):
                want = expected_refresh(events)
                have = probe_refresh(got["events"])
                assert want[0] == have[0], where
                if want[0]:
                    assert want[1] == have[1], where
            if op == "reapply":
                created = events and events[0]["op"] == "create_new"
                assert got["ret"] is (not created and not state.removed), where
            checked += 1
    assert checked > 1000


def test_reset_periodic_on_hit_matches_probe(probe):
    """Rules out: 'stacking auras reset their periodic timer on reapply' (the hit path passes false for capacity>=2)."""
    for cap in (0, 1, 2, 3, 255):
        for flags in (0, TRIGGERED_DONT_RESET_PERIODIC_TIMER):
            out = run(probe, ["reset", f"spell 900 {cap} 0 0 0 0 0 0 0 0 -1", f"resetflag {flags}"])
            assert out[0]["ret"] is S.reset_periodic_on_hit(cap, bool(flags))


def test_predicates_match_probe(probe):
    for a0 in (0, A0_PASSIVE):
        for a1 in (0, A1_CHANNELLED, A1_SELF_CHANNELLED):
            for a3 in (0, A3_DOT_STACKING_RULE):
                for cap in (0, 1, 2, 9):
                    out = run(probe, ["reset", f"spell 900 {cap} 0 {a0} {a1} {a3} 0 0 0 0 -1", "create 1 11 0 -1",
                                      "query"])[1]["ret"]
                    assert out["multislot"] is S.is_multi_slot(900, bool(a0))
                    assert out["one_slot"] is S.is_stackable_on_one_slot_with_different_casters(cap, bool(a1), bool(a3))
                    assert out["using_stacks"] is S.is_using_stacks(cap, 1)


def test_therazane_wrap_witness(probe):
    """Earth Shield 974 (capacity 9) under Therazane's Resilience 1217622 (MaxAuraStacks -99, Doses -99):
    Trinity creates 1 stack, and the next reapplication stores uint8(-90) = 166 stacks without a refresh.
    Rules out: 'a negative max stack spell mod makes the aura non-stacking'."""
    out = run(probe, ["reset", "spell 974 9 0 0 0 0 0 0 0 0 600000", "effect 0 20 64", "effect 1 0 64",
                      "mod 37 -99 1", "calcmaxdur 600000", "create 1 1 0 600000", "reapply 1 0 1 0 3 20 0"])
    assert out[0]["state"]["stacks"] == 1
    assert out[1]["state"]["stacks"] == 166 and "calc_max_duration" not in out[1]["events"]
    shape = S.AuraShape.plain(9, max_stacks=S.calc_max_stack_amount(9, (-99, 1.0)), max_duration=600000)
    st = S.initial_state(shape, S.initial_stacks(doses_mod=S.apply_spell_mod(1, -99, 1.0, "int32")))
    S.reapply(st, shape, S.Reapply(reset_periodic=False))
    assert st.stacks == 166


def test_regenerative_chitin_charges_witness(probe):
    """Blistering Scales 360827 (15 charges) under Regenerative Chitin 406907 (ProcCharges flat -16):
    uint32(-1.0) is undefined behaviour; gcc/x86-64 at the pinned flags yields 255 charges."""
    out = run(probe, ["reset", "spell 360827 0 15 0 0 0 0 0 0 0 10000", "effect 0 0 64", "proc 1 15 0",
                      "mod 4 -16 1", "create 1 1 0 10000"])
    assert out[0]["state"]["charges"] == 255
    assert C.calc_max_charges(15, 15, (-16, 1.0)) == 255


def test_creation_unclamped_refresh_clamps_al_d_a_03(probe):
    """Confirms track A's AL-D-A-03 with verbatim code: construction does not clamp the requested stack amount to
    CalcMaxStackAmount, the next increasing reapplication does -- and because the clamped count is lower, it does
    not refresh.  Rules out 'stacks <= capacity is an invariant'."""
    out = run(probe, ["reset", "spell 900 2 0 0 0 0 0 0 0 0 10000", "effect 0 5 0", "calcmaxdur 10000",
                      "create 7 11 0 10000", "reapply 1 1 11 0 1 5",
                      "reset", "spell 900 0 0 0 0 0 0 0 0 0 10000", "effect 0 5 0", "calcmaxdur 10000",
                      "create 3 11 0 10000", "reapply 1 1 11 0 1 5"])
    assert [o["state"]["stacks"] for o in out] == [7, 2, 3, 1]
    assert all("calc_max_duration" not in o["events"] for o in out)
    for cap, created, after in ((2, 7, 2), (0, 3, 1)):
        sh = S.AuraShape.plain(cap, max_duration=10000)
        st = S.initial_state(sh, created)
        ev = S.reapply(st, sh, S.Reapply())
        assert st.stacks == after and ev[-1]["op"] == "no_refresh"
