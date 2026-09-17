"""Shared helpers for the part-D weapon_combat tests (swing / attack table / proc events).

Owns the bridge to ``tools/tc_swing_probe``: the probe compiles TrinityCore's own
``RollMeleeOutcomeAgainst`` / ``MeleeSpellHitResult`` / chance getters / attack-time
arithmetic, and reads its inputs from ``set <key> <value>`` lines.  This module
translates the Python oracle's dataclasses into those keys.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from weapon_combat.attack_table import Attacker, SpellFacts, Victim

PROBE = Path(__file__).resolve().parents[1] / "tools" / "tc_swing_probe" / "probe"


class Probe:
    def __init__(self) -> None:
        self.process = subprocess.Popen([str(PROBE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        text=True, bufsize=1)

    def ask(self, request: str) -> str:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(request + "\n")
        self.process.stdin.flush()
        return self.process.stdout.readline().strip()

    def configure(self, a: Attacker, v: Victim, spell: SpellFacts | None = None) -> None:
        assert self.ask("clear") == "ok"
        for key, value in probe_settings(a, v, spell).items():
            assert self.ask(f"set {key} {value!r}") == "ok"

    def close(self) -> None:
        assert self.process.stdin
        self.process.stdin.close()
        self.process.wait(timeout=10)


def probe_fixture():
    if not PROBE.exists():
        pytest.skip(f"probe not built; run: make -C {PROBE.parent}")
    probe = Probe()
    yield probe
    probe.close()


def probe_settings(a: Attacker, v: Victim, spell: SpellFacts | None) -> dict[str, float]:
    s: dict[str, float] = {
        "a.player": float(a.is_player), "a.pet": float(a.is_pet), "a.cbp": float(a.is_controlled_by_player),
        "a.level": float(a.level_for_target), "a.offhand": float(a.have_offhand_weapon), "a.feral": float(a.in_feral_form),
        "a.meleespell": float(a.current_melee_spell), "a.has.458": float(a.ignore_dual_wield_penalty),
        "a.aura.54": a.mod_hit_chance_aura, "a.crit": a.crit_done_pct,
        "a.aura.52": a.creature_weapon_crit_aura, "a.aura.290": a.creature_crit_pct_aura,
        "a.flags_extra": float((0x20000 if a.creature_no_crit else 0) | (0x20 if a.creature_no_crushing else 0)),
        "a.aura.334": a.autoattack_crit_aura, "a.expmh": float(a.expertise_mainhand), "a.expoh": float(a.expertise_offhand),
        "a.aura.240": a.creature_expertise_aura, "a.auramisc.248.2": a.mod_combat_result_dodge_aura,
        "a.aura.251": a.mod_enemy_dodge_aura, "a.tempsummon": float(a.is_temp_summon),
        "a.modowner": float(a.is_player or a.has_spell_mod_owner),
        "a.icr.dodge": float("dodge" in a.ignore_combat_result), "a.icr.parry": float("parry" in a.ignore_combat_result),
        "a.icr.block": float("block" in a.ignore_combat_result),
        "v.player": float(v.is_player), "v.pet": float(v.is_pet), "v.totem": float(v.is_totem), "v.level": float(v.level_for_target),
        "v.evading": float(v.evading), "v.stand": float(v.stand_state), "v.facing": float(v.facing_attacker),
        "v.has.288": float(v.ignore_hit_direction), "v.casting": float(v.casting), "v.controlled": float(v.controlled),
        "v.dodgepct": v.dodge_percentage, "v.parrypct": v.parry_percentage, "v.blockpct": v.block_percentage,
        "v.canparry": float(v.can_parry), "v.canblock": float(v.can_block), "v.weapon": float(v.has_useable_weapon),
        "v.shield": float(v.has_useable_shield),
        "v.flags_extra": float((0x4 if v.creature_no_parry else 0) | (0x10 if v.creature_no_block else 0)),
        "v.aura.49": v.mod_dodge_percent_aura, "v.aura.47": v.mod_parry_percent_aura, "v.aura.51": v.mod_block_percent_aura,
        "v.aura.187": v.attacker_melee_crit_aura, "v.aura.183": v.crit_vs_target_health_aura, "v.aura.306": v.crit_for_caster_aura,
        "v.aura.339": v.crit_for_caster_pet_aura, "v.aura.197": v.spell_and_weapon_crit_aura,
        "v.aura.184": v.attacker_melee_hit_aura, "v.aura.185": v.attacker_ranged_hit_aura,
        "v.resist": v.mechanic_resist_pct, "v.aura.287": v.deflect_aura,
    }
    if a.mod_melee_hit_chance is not None:
        s["a.hitmelee"] = a.mod_melee_hit_chance
    if a.mod_ranged_hit_chance is not None:
        s["a.hitranged"] = a.mod_ranged_hit_chance
    if spell is not None:
        s.update({
            "s.ranged": float(spell.dmg_class_ranged),
            "s.attr0": float(0x00200000 if spell.no_active_defense else 0),
            "s.attr3": float(0x40 if spell.no_avoidance else 0),
            "s.attr7": float((0x00800000 if spell.no_attack_dodge else 0) | (0x01000000 if spell.no_attack_parry else 0)
                             | (0x02000000 if spell.no_attack_miss else 0)),
            "s.attr8": float(1 if spell.no_attack_block else 0),
            "s.attrcu": float(0x20000 if spell.req_caster_behind_target else 0),
            "a.spellmod_hit": spell.hit_chance_spellmod_pct,
        })
    return s


def rle(outcome_for_roll) -> str:
    """Same run-length format the probe prints: ``name@first-roll``."""
    parts: list[str] = []
    last = None
    for roll in range(10000):
        name = outcome_for_roll(roll)
        if name != last:
            parts.append(f"{name}@{roll}")
            last = name
    return " ".join(parts)
