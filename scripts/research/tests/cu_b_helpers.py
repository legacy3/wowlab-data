"""Track B test helpers: one case description drives both the Python oracle and the C++ probe."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from controlled_units import stats as st

PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_pet_probe"
PROBE = PROBE_DIR / "probe"


@dataclass
class Case:
    entry: int
    unit_class: str  # Pet/SUMMON_PET | Pet/HUNTER_PET | Guardian
    owner_class: int
    petlevel: int
    creature_unit_class: int = 1
    base_attack_time: int = 2000
    calc_power: int = 0
    power_npc_ok: bool = True
    max_base_power: int = 0
    pinfo: st.PetLevelInfo | None = None
    owner_stats: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    owner_armor: int = 0
    owner_ap: int = 0
    owner_rap: int = 0
    pos: tuple[int, ...] = (0,) * 7
    neg: tuple[int, ...] = (0,) * 7
    sbdb: tuple[int, int, int, int] = (0, 0, 0, 0)  # frost nature fire shadow
    health_modifier: float = 1.0
    mana_modifier: float = 1.0
    base_mana: int = 0
    es_health_pet: float = 1.0
    es_dps_pet: float = 1.0
    select_level: int = 1
    es_health_sel: float = 1.0
    es_dps_sel: float = 1.0
    extra: dict = field(default_factory=dict)

    @property
    def is_pet(self) -> bool:
        return self.unit_class.startswith("Pet/")

    def display_power(self) -> int:
        return self.calc_power if (self.calc_power == 0 or self.power_npc_ok) else 0


def run_python(c: Case) -> dict:
    owner = st.OwnerFacts(class_id=c.owner_class, level=c.petlevel, stats=c.owner_stats, armor=c.owner_armor,
                          attack_power_melee=float(c.owner_ap), attack_power_ranged=float(c.owner_rap),
                          mod_damage_done_pos=c.pos, mod_damage_done_neg=c.neg,
                          spell_base_damage_bonus=dict(zip(("frost", "nature", "fire", "shadow"), c.sbdb)))
    cinfo = st.CreatureFacts(entry=c.entry, unit_class=c.creature_unit_class, base_attack_time=c.base_attack_time,
                             calc_display_power=c.calc_power, display_power_type=c.display_power(),
                             max_base_power=c.max_base_power if c.power_npc_ok else 0)
    eng = st.EngineInputs(expected_health_at_petlevel=c.es_health_pet, health_modifier=c.health_modifier,
                          base_mana=c.base_mana, base_damage_for_level=c.es_dps_pet)
    if not c.is_pet:
        st.guardian_pre_state(eng, c.es_health_sel, c.health_modifier, c.mana_modifier, c.es_dps_sel)
    store = {} if c.pinfo is None else None
    return st.run_oracle(c.entry, c.unit_class, owner, petlevel=c.petlevel, engine=eng, cinfo=cinfo,
                         pinfo_override=c.pinfo, pet_store=store)


def probe_line(c: Case) -> str:
    p = c.pinfo
    vals = [int(c.is_pet), int(c.unit_class == "Pet/HUNTER_PET"), c.owner_class, c.petlevel, c.entry,
            c.creature_unit_class, c.base_attack_time, c.calc_power, int(c.power_npc_ok), c.max_base_power,
            int(p is not None), p.health if p else 0, p.mana if p else 0, p.armor if p else 0,
            *(p.stats if p else (0,) * 5), *c.owner_stats, c.owner_armor, c.owner_ap, c.owner_rap,
            *c.pos, *c.neg, *c.sbdb]
    fl = [c.health_modifier, c.mana_modifier]
    tail = [c.base_mana]
    es = [c.es_health_pet, c.es_dps_pet]
    pre = [int(not c.is_pet), c.select_level]
    es2 = [c.es_health_sel, c.es_dps_sel]
    parts = [str(v) for v in vals] + [float(x).hex() for x in fl] + [str(v) for v in tail] + \
            [float(x).hex() for x in es] + [str(v) for v in pre] + [float(x).hex() for x in es2]
    return "init " + " ".join(parts)


def parse_probe(line: str) -> dict:
    t = line.split()
    return {"stats": [int(x) for x in t[0:5]], "max_health": int(t[5]), "max_power": int(t[6]),
            "armor": (int(t[7]), int(t[8])), "attack_power": int(t[9]), "attack_power_multiplier": float.fromhex(t[10]),
            "bonus_spell_damage": int(t[11]), "min_damage": float.fromhex(t[12]), "max_damage": float.fromhex(t[13]),
            "weapon_damage": [float.fromhex(t[14]), float.fromhex(t[15])], "create_health": int(t[16]),
            "create_mana": int(t[17]), "base_attack_time": int(t[19]), "display_power": int(t[20])}


def run_probe(lines: list[str]) -> list[str]:
    out = subprocess.run([str(PROBE)], input="\n".join(lines) + "\n", capture_output=True, text=True, check=True, timeout=60)
    return out.stdout.splitlines()


def comparable_python(r: dict) -> dict:
    return {"stats": list(r["stats"].values()), "max_health": r["max_health"],
            "max_power": r["max_power"].get("value"), "armor": (r["armor"]["base"], r["armor"]["bonus"]),
            "attack_power": r["attack_power"], "attack_power_multiplier": r["attack_power_multiplier"],
            "bonus_spell_damage": r["bonus_spell_damage"], "min_damage": r["min_damage"], "max_damage": r["max_damage"],
            "weapon_damage": [float(x) for x in r["weapon_damage"]], "create_health": r["create_health"],
            "create_mana": r["create_mana"], "base_attack_time": r["base_attack_time_ms"]}


def comparable_probe(p: dict) -> dict:
    q = dict(p)
    q.pop("display_power")
    return q
