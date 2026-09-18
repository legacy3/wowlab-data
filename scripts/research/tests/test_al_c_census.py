"""Track C: real-data tests (fixture ``al_ctx``) of the stack/charge census and witnesses."""

from __future__ import annotations

import json

import pytest

from aura_lifecycle import CORPORA, records
from aura_lifecycle import charges as C
from aura_lifecycle import cmd_c
from aura_lifecycle import stacks as S

PHALANX = 1269312


def test_phalanx_capacity_is_not_initial(al_ctx):
    """Rules out 'Phalanx starts with 2 stacks' and 'a passive with capacity 2 stacks on reapplication'."""
    f = S.stack_facts(al_ctx, PHALANX)
    assert al_ctx.name(PHALANX) == "Phalanx"
    assert f["capacity"] == 2 and f["passive"] and f["multislot"]
    assert S.stack_family(f) == "multislot-dormant-capacity"
    ex = cmd_c.explain_stacks(al_ctx, PHALANX)
    assert ex["initial_stacks"]["default"] == 1 and ex["initial_stacks"]["doses_modifiers"] == []
    assert [s["state"]["stacks"] for s in ex["default_timeline"]["timeline"]] == [1, 1, 1]
    assert ex["external_mutators"]["modify_aura_stacks"] == []


def test_witness_modifiers_resolve(al_ctx):
    """The spell-mod witnesses used in the section resolve structurally (family mask / label)."""
    cens = cmd_c._census_facts(al_ctx)
    by_target: dict[int, set[tuple[int, str]]] = {}
    for m in cens["spellmods"]:
        for t in cens["spellmod_targets"][(m["spell"], m["index"])]:
            by_target.setdefault(t, set()).add((m["spell"], m["op"]))
    assert {(1217622, "MaxAuraStacks"), (1217622, "Doses"), (443441, "Doses")} <= by_target[974]
    assert (300346, "MaxAuraStacks") in by_target[192081]
    assert (383155, "Doses") in by_target[260708]
    assert (406907, "ProcCharges") in by_target[360827]


def test_family_partition_and_populations(al_ctx):
    cens = cmd_c._census_facts(al_ctx)
    facts, pops = cens["facts"], cens["pops"]
    assert len(facts) + len(cens["missing"]) == len(pops["all"])
    assert pops["player"] <= pops["all"] and pops["controlled"] <= pops["all"]
    fams = {f["stack"]["family"] for f in facts.values()}
    assert fams <= set(S.FAMILY_DOC)
    assert {f["charge"]["family"] for f in facts.values()} <= set(C.FAMILY_DOC)
    # every multi-slot provider is passive or one of the three hardcoded ids
    for s, f in facts.items():
        if f["stack"]["multislot"]:
            assert f["stack"]["passive"] or s in S.MULTISLOT_IDS


def test_charge_facts_witnesses(al_ctx):
    """Rules out 'USE_STACKS_FOR_CHARGES auras use their charges' and 'a proc entry is required for charges'."""
    fof = C.charge_facts(al_ctx, 44544)
    assert fof["use_stacks_for_charges"] and C.charge_family(fof) == "stacks-as-charges"
    sk = C.charge_facts(al_ctx, 191634)
    assert sk["initial_charges_no_mods"] == 1 and sk["proc_entry"] is None
    assert C.charge_family(sk) == "charges-without-proc-entry"


def test_corpora_regenerate_identically(al_ctx):
    """The checked-in corpora are exactly what the registered commands produce."""
    for kind, build in (("stacks", cmd_c.stacks_corpus), ("charges", cmd_c.charges_corpus)):
        path = CORPORA / f"{kind}.json"
        if not path.exists():
            pytest.skip(f"{path.name} not generated")
        cmd = f"aura_lifecycle.py {kind} --census --out docs/research/aura-lifecycle-corpora/{kind}.json"
        payload = build(al_ctx, cmd)
        records.validate_corpus(payload)
        fresh = json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        assert fresh == path.read_text(encoding="utf-8"), kind
