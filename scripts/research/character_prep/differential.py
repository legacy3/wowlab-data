"""Differential of prepared values: charstats/gearing (Python double) vs tools/tc_prep_probe (C++ float).

The probe runs TrinityCore's own bodies (extracted verbatim at build time, see
``tools/tc_prep_probe/extract.py``) for the bounded, pure arithmetic of
``Player::InitStatsForLevel`` -> ``UpdateAllStats``.  This module drives it
with the same inputs the Python oracle receives and records every field with
its C++ types, so a ``float``-vs-``double`` divergence would surface here.

Witness inputs are *synthetic caller-supplied contributions* (round numbers),
not Retail gear; they exist to exercise the arithmetic, never to assert a
value.  Base stats come from the TDB corpus (``basestats_tdb``), level-90
witnesses opt into Trinity's fill rule and are tagged accordingly.

``--fixture FILE`` reads a compiled Track-C fixture (agent E's compiler) when
present.  Its shape is validated; an unknown shape fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import CORPORA, ROOT, SourceError, TABLES
from .basestats_tdb import (FILL_RULE_NONE, FILL_RULE_TRINITY, TRINITY_DEFAULT_MAX_PLAYER_LEVEL,
                            TaggedBaseStatTable, load, playercreateinfo_pairs)
from .server_inputs import PROBE, provenance, write_corpus
from charstats.character import CharacterResolver, Contributions
from charstats.derived import BASE_CRIT_PERCENT
from charstats.identity import (MAX_STATS, STAT_AGILITY, STAT_INTELLECT, STAT_NAMES, STAT_STAMINA,
                                STAT_STRENGTH)
from gearing.enums import CR_INDEX, RATING_DIMINISHING_GLOBAL_CURVE
from gearing.tables import Tables

#: Spell 114585 "Mastery": SkillLineAbility 25920 on skill line 183 (GENERIC (DND)),
#: AcquireMethod 2 (AutomaticCharLevel), ClassMask 0, RaceMask 0, SpellLevel 0;
#: SkillRaceClassInfo 5 (SkillID 183, ClassMask 16383, RaceMasks -1, Availability 1).
#: Effect 0: EffectAura 318 (SPELL_AURA_MASTERY), EffectBasePointsF 8.  Trinity learns it
#: for every player through LoadPlayerInfo skills (ObjectMgr.cpp:4059-4072) ->
#: Player::LearnDefaultSkills (Player.cpp:25259) -> LearnSkillRewardedSpells (25391-25441).
GENERIC_MASTERY_SPELL = 114585
GENERIC_MASTERY_BASE_POINTS = 8.0

RATING_TO_CR = {"CritMelee": "CR_CRIT_MELEE", "CritRanged": "CR_CRIT_RANGED", "CritSpell": "CR_CRIT_SPELL",
                "HasteMelee": "CR_HASTE_MELEE", "HasteRanged": "CR_HASTE_RANGED", "HasteSpell": "CR_HASTE_SPELL",
                "Mastery": "CR_MASTERY", "VersatilityDamageDone": "CR_VERSATILITY_DAMAGE_DONE",
                "VersatilityHealingDone": "CR_VERSATILITY_HEALING_DONE",
                "VersatilityDamageTaken": "CR_VERSATILITY_DAMAGE_TAKEN"}
COMPARED_RATINGS = tuple(RATING_TO_CR)
BINARY32_CLASS = "binary32-vs-binary64"


@dataclass
class Witness:
    name: str
    race_id: int
    class_id: int
    spec_id: int | None
    level: int
    fill_rule: str = FILL_RULE_NONE
    stats: dict[int, int] = field(default_factory=dict)
    ratings: dict[str, int] = field(default_factory=dict)
    armor: int = 0
    health: int = 0
    attack_power: int = 0
    spell_power: int = 0
    note: str = ""
    source: str = "synthetic caller-supplied contributions (round numbers; not Retail gear)"
    compiled_python: dict[str, Any] | None = None
    apply_armor_specialization: bool = False

    def contributions(self) -> Contributions:
        return Contributions(stats=dict(self.stats), ratings=dict(self.ratings), armor=self.armor,
                             attack_power=self.attack_power, spell_power=self.spell_power,
                             health=self.health, source=self.source)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "race_id": self.race_id, "class_id": self.class_id,
                "spec_id": self.spec_id, "level": self.level, "fill_rule": self.fill_rule,
                "contributions": self.contributions().to_dict(), "note": self.note,
                "apply_armor_specialization": self.apply_armor_specialization}


GEAR_A = dict(stats={STAT_STRENGTH: 10000, STAT_AGILITY: 300, STAT_STAMINA: 15000, STAT_INTELLECT: 300},
              # sized so the level-80 conversions land inside the diminishing curves
              # (crit/haste/mastery past the 30% knee, versatility below it)
              ratings={"CritMelee": 500, "CritRanged": 500, "CritSpell": 500, "HasteMelee": 400,
                       "HasteRanged": 400, "HasteSpell": 400, "Mastery": 400, "VersatilityDamageDone": 300,
                       "VersatilityHealingDone": 300, "VersatilityDamageTaken": 300},
              armor=5000, health=0, attack_power=0, spell_power=0)
GEAR_INT = dict(stats={STAT_STRENGTH: 300, STAT_AGILITY: 300, STAT_STAMINA: 15000, STAT_INTELLECT: 10000},
                ratings=dict(GEAR_A["ratings"]), armor=3000, health=0, attack_power=0, spell_power=500)
GEAR_AGI = dict(stats={STAT_STRENGTH: 300, STAT_AGILITY: 10000, STAT_STAMINA: 15000, STAT_INTELLECT: 300},
                ratings=dict(GEAR_A["ratings"]), armor=4000, health=0, attack_power=0, spell_power=0)


def default_witnesses() -> list[Witness]:
    return [
        Witness("warrior-arms-human-80", 1, 1, 71, 80, **GEAR_A),
        Witness("warrior-arms-orc-80", 2, 1, 71, 80, **GEAR_A, note="race modifier +3 str / -3 agi / +1 sta / -1 int"),
        Witness("priest-shadow-human-80", 1, 5, 258, 80, **GEAR_INT),
        Witness("hunter-bm-orc-80", 2, 3, 253, 80, **GEAR_AGI),
        Witness("evoker-devastation-dracthyr-80", 52, 13, 1467, 80, **GEAR_INT,
                note="class 13 has rows 1, 10..80; race 52 modifier authored as zeros"),
        Witness("warrior-arms-human-90-fill", 1, 1, 71, 90, fill_rule=FILL_RULE_TRINITY, **GEAR_A,
                note="no rows above 80: base stats are the level-80 cell copied forward (trinity-consumer(fill-rule))"),
        Witness("priest-shadow-human-90-fill", 1, 5, 258, 90, fill_rule=FILL_RULE_TRINITY, **GEAR_INT),
        Witness("evoker-devastation-dracthyr-5-fill", 52, 13, 1467, 5, fill_rule=FILL_RULE_TRINITY, **GEAR_INT,
                note="level 5 has no row for class 13: Trinity serves the level-1 cell (rows 2..9 absent)"),
        Witness("warrior-arms-human-80-armorspec", 1, 1, 71, 80, **GEAR_A, apply_armor_specialization=True,
                note="armour specialisation applied: TOTAL_PCT = static_cast<float>(double 1 + pct/100)"),
        Witness("warrior-arms-human-80-armorspec-crossing", 1, 1, 71, 80, apply_armor_specialization=True,
                stats={STAT_STRENGTH: 9993, STAT_AGILITY: 300, STAT_STAMINA: 15000, STAT_INTELLECT: 300},
                ratings=dict(GEAR_A["ratings"]), armor=5000,
                note="17647 + 9993 = 27640 Strength before the 5% stage: double gives 29022, Trinity float 29021"),
        Witness("warrior-arms-haranir-80", 86, 1, 71, 80, **GEAR_A,
                note="race 86 row is all zeros, added by 2026_03_07_01_world.sql (DELETE+INSERT)"),
        Witness("naked-warrior-human-80", 1, 1, 71, 80, note="no contributions: pure base-stat pipeline"),
        Witness("naked-warrior-human-90-fill", 1, 1, 71, 90, fill_rule=FILL_RULE_TRINITY),
    ]


# ---------------------------------------------------------------------------
# probe driver
# ---------------------------------------------------------------------------

class Probe:
    def __init__(self, path: Path = PROBE) -> None:
        if not path.exists():
            raise SourceError(f"probe not built; run: make -C {path.parent}")
        self.process = subprocess.Popen([str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        text=True, bufsize=1)

    def ask(self, request: str) -> list[str]:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(request + "\n")
        self.process.stdin.flush()
        reply = self.process.stdout.readline().strip()
        if reply.startswith("ERR"):
            raise SourceError(f"probe: {reply} for {request!r}")
        return reply.split()

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=10)


def f32(x: float) -> float:
    """Round a Python double to the nearest binary32 (what a C++ float load does)."""
    import struct
    return struct.unpack("f", struct.pack("f", x))[0]


# ---------------------------------------------------------------------------
# comparison records
# ---------------------------------------------------------------------------

def record(field_name: str, python: Any, probe: Any, *, types: dict[str, str], integer: bool,
           tolerance: float = 1e-6, classification: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"field": field_name, "python": python, "probe": probe, "types": types}
    if integer:
        out["match"] = int(python) == int(probe)
    else:
        diff = abs(float(python) - float(probe))
        scale = max(abs(float(python)), abs(float(probe)), 1e-12)
        out["abs_diff"] = diff
        out["rel_diff"] = diff / scale
        out["match"] = out["rel_diff"] <= tolerance
        out["int32_python"] = int(float(python))
        out["int32_probe"] = int(float(probe))
        out["int32_agree"] = out["int32_python"] == out["int32_probe"]
    if classification:
        out["classification"] = classification
    return out


def curve_args(resolver: CharacterResolver, rating: str) -> str:
    curve_type = RATING_DIMINISHING_GLOBAL_CURVE[rating]
    curve_id = resolver.ratings.global_curve_id(curve_type)
    pts = resolver.curves.points(curve_id)
    mode = resolver.curves.mode(curve_id)
    return f"{mode} {len(pts)} " + " ".join(f"{x!r} {y!r}" for x, y in pts)


def compare_witness(resolver: CharacterResolver, table: TaggedBaseStatTable, probe: Probe,
                    witness: Witness) -> dict[str, Any]:
    resolved = resolver.resolve(witness.race_id, witness.class_id, witness.level, witness.spec_id,
                                witness.contributions(),
                                apply_armor_specialization=witness.apply_armor_specialization)
    base = table.describe(witness.class_id, witness.race_id, witness.level)
    records: list[dict[str, Any]] = []
    T_STAT = {"source_type": "player_classlevelstats int / player_racestats smallint -> PlayerLevelInfo int32",
              "intermediate_type": "float (m_createStats, m_auraFlatModifiersGroup)",
              "rounding": "SetStat(stat, int32(value)) truncation", "units": "stat points",
              "integer_conversion": "StatSystem.cpp:112 / 200 int32(float)"}

    # -- total stats: Unit::GetTotalStatValue ---------------------------------
    # Trinity routes item stats through BASE_VALUE (Player.cpp:7914-7932); charstats routes
    # them through TOTAL_VALUE.  With default percentages both give the same number; the probe
    # is fed Trinity's routing so any divergence would be the routing itself.
    for stat in range(MAX_STATS):
        item = witness.stats.get(stat, 0)
        # TOTAL_PCT: Unit::GetTotalAuraMultiplier accumulates in double and returns
        # static_cast<float> (Unit.cpp:5016-5033); the armour specialisation is the only
        # percent stage a witness can carry, so f32(1 + pct/100) is Trinity's value.
        total_pct = f32(resolved.stat_stages[stat].total_pct)
        reply = probe.ask(f"totalstat {base['stats'][STAT_NAMES[stat]]} {item} 100 1 0 {total_pct!r}")
        rec = record(f"stat.{STAT_NAMES[stat]}", resolved.stat(stat), int(reply[1]), types=T_STAT, integer=True)
        if not rec["match"] and total_pct != 1.0:
            rec["classification"] = (BINARY32_CLASS + f": total {resolved.stat_stages[stat].create_value + item} "
                                     f"x f32({resolved.stat_stages[stat].total_pct!r}) = {reply[0]} truncates to "
                                     f"{reply[1]}; double gives {resolved.stat(stat)}")
        records.append(rec)
        raw_rec = record(f"stat.{STAT_NAMES[stat]}.raw", resolved.stat_stages[stat].value(), float(reply[0]),
                         types=T_STAT, integer=False)
        if not raw_rec["int32_agree"] and total_pct != 1.0:
            raw_rec["classification"] = BINARY32_CLASS + ": values agree to rel_diff but straddle an integer"
        records.append(raw_rec)

    stamina = resolved.stat(STAT_STAMINA)
    hps = resolver.derived.hp_per_sta(witness.level)
    reply = probe.ask(f"maxhealth 0 {witness.health} 1 0 1 {stamina} {witness.level} {hps!r}")
    records.append(record("max_health", resolved.max_health, int(reply[0]), integer=True, types={
        "source_type": "HpPerSta.txt float; UnitData::Stats int32", "intermediate_type": "float",
        "rounding": "SetMaxHealth((uint32)value) truncation; SetMaxHealth floors 0 to 1 (Unit.cpp:10018)",
        "units": "health", "integer_conversion": "StatSystem.cpp:323 (uint32)"}))
    records.append(record("max_health.raw", float(resolved.max_health if resolved.health_from_stamina == 0 else
                                                  (witness.health + resolved.health_from_stamina)),
                          float(reply[1]), integer=False, types={"intermediate_type": "float"}))

    # -- base mana: GetGameTableColumnForClass + uint32 -----------------------
    bmp = resolver.tables.gametable("BaseMp").row(witness.level)
    reply = probe.ask(f"basemp {witness.class_id} " + " ".join(f"{v!r}" for v in bmp[:15]))
    records.append(record("create_mana", resolved.base.create_mana, int(reply[0]), integer=True, types={
        "source_type": "BaseMp.txt float", "intermediate_type": "float", "rounding": "uint32() truncation",
        "units": "mana", "integer_conversion": "ObjectMgr.cpp:4434"}))
    reply = probe.ask(f"maxpower {resolved.base.create_mana} 0 1 0 1")
    records.append(record("max_mana", resolved.base.create_mana, int(reply[0]), integer=True, types={
        "intermediate_type": "float", "rounding": "(int32)std::lroundf(value) -- round half away from zero, "
        "NOT truncation (StatSystem.cpp:344); Python round() is half-to-even",
        "units": "mana", "integer_conversion": "StatSystem.cpp:344"}))

    # -- armor ----------------------------------------------------------------
    reply = probe.ask(f"armor {resolved.base.base_armor} 1 {witness.armor} 1 1")
    records.append(record("armor", resolved.armor, int(reply[0]), integer=True, types={
        "source_type": "int32(createStats[AGILITY]*2) base; item armor float TOTAL_VALUE",
        "intermediate_type": "float", "rounding": "SetArmor(int32(value), int32(value - baseValue)) truncation",
        "units": "armor", "integer_conversion": "StatSystem.cpp:274"}))
    records.append(record("bonus_armor", resolved.bonus_armor, int(reply[1]), integer=True,
                          types={"intermediate_type": "float", "rounding": "int32 truncation"}))

    # -- attack power ---------------------------------------------------------
    klass = resolved.klass
    reply = probe.ask(f"ap {resolved.stat(STAT_STRENGTH)} {resolved.stat(STAT_AGILITY)} "
                      f"{klass.attack_power_per_strength!r} {klass.attack_power_per_agility!r} 1")
    records.append(record("attack_power", resolved.attack_power, int(reply[0]), integer=True, types={
        "source_type": "ChrClasses.AttackPowerPerStrength/Agility (float in DB2Structure)",
        "intermediate_type": "float", "rounding": "SetAttackPower(int32(base_attPower)) truncation",
        "units": "attack power", "integer_conversion": "StatSystem.cpp:382"}))
    reply = probe.ask(f"rap {witness.level} {resolved.stat(STAT_AGILITY)} {klass.ranged_attack_power_per_agility!r}")
    records.append(record("ranged_attack_power", resolved.ranged_attack_power, int(reply[0]), integer=True, types={
        "intermediate_type": "float", "rounding": "int32 truncation", "units": "attack power"}))

    # -- ratings --------------------------------------------------------------
    conv = {c["rating"]: c for c in resolved.rating_conversions}
    combat = resolver.ratings.gt_combat_ratings.row(witness.level)
    T_RATING = {"source_type": "CombatRatings.txt float; ActivePlayerData::CombatRatings int32",
                "intermediate_type": "float (1.0f / column; float(rating) * multiplier; curve in float)",
                "rounding": "none", "units": "percent",
                "integer_conversion": "ApplyRatingMod: int32 value added into int16 m_baseRatingValue (Player.h:3254)"}
    for rating in COMPARED_RATINGS:
        c = conv[rating]
        column = combat[CR_INDEX[rating]]
        reply = probe.ask(f"ratingbonus {CR_INDEX[rating]} {int(c['amount'])} {column!r} {curve_args(resolver, rating)}")
        records.append(record(f"rating.{rating}.linear_percent", c["linear_percent"], float(reply[0]),
                              integer=False, types=T_RATING))
        records.append(record(f"rating.{rating}.final_percent", c["final_percent"], float(reply[1]),
                              integer=False, types=T_RATING))

    # -- crit -----------------------------------------------------------------
    reply = probe.ask(f"crit 0 5 {f32(conv['CritMelee']['final_percent'])!r}")
    records.append(record("crit_percentage.melee", BASE_CRIT_PERCENT + conv["CritMelee"]["final_percent"],
                          float(reply[0]), integer=False, types={"intermediate_type": "float", "units": "percent",
                          "source_type": "literal 5.0f (StatSystem.cpp:530) + rating"}))
    reply = probe.ask(f"spellcrit 0 0 {f32(conv['CritSpell']['final_percent'])!r}")
    records.append(record("crit_percentage.spell", BASE_CRIT_PERCENT + conv["CritSpell"]["final_percent"],
                          float(reply[0]), integer=False, types={"intermediate_type": "float", "units": "percent",
                          "source_type": "literal 5.0f (StatSystem.cpp:711) + auras + rating"}))

    # -- mastery --------------------------------------------------------------
    column = combat[CR_INDEX["Mastery"]]
    aura = GENERIC_MASTERY_BASE_POINTS if witness.spec_id is not None else 0.0
    reply = probe.ask(f"mastery {aura!r} {int(conv['Mastery']['amount'])} {column!r} {curve_args(resolver, 'Mastery')}")
    T_MASTERY = {"source_type": "SPELL_AURA_MASTERY amounts (SpellEffectValue double) summed into float; rating int32",
                 "intermediate_type": "float", "rounding": "none", "units": "mastery points (percent-like)",
                 "integer_conversion": "none until an effect amount"}
    records.append(record("mastery_value.charstats", resolved.mastery_value, float(reply[0]), integer=False,
                          types=T_MASTERY,
                          classification="model-gap: charstats omits GetTotalAuraModifier(SPELL_AURA_MASTERY) "
                                         "(spell 114585, 8 base points, auto-learned via skill line 183)"
                          if witness.spec_id is not None else ""))
    records.append(record("mastery_value.with_114585", aura + resolved.mastery_value, float(reply[0]),
                          integer=False, types=T_MASTERY))
    if resolved.mastery and resolved.mastery.spells:
        spell = resolved.mastery.spells[0]
        effects = [e for e in spell.effects if e.participates_in_mastery]
        if effects:
            e = effects[0]
            mastery_f = float(reply[0])
            reply2 = probe.ask(f"masteryamount {e.base_points!r} {mastery_f!r} {e.bonus_coefficient!r}")
            records.append(record(f"mastery_amount.spell{spell.spell_id}.effect{e.effect_index}",
                                  e.amount_at(mastery_f), float(reply2[0]), integer=False, types={
                                      "source_type": "EffectBasePointsF float, EffectBonusCoefficient float, Mastery float",
                                      "intermediate_type": "SpellEffectValue double += float * float (SpellInfo.cpp:602)",
                                      "rounding": "AuraEffect::GetAmountAsInt static_cast<int32> where an int is needed",
                                      "units": "effect amount"}))
            records.append(record(f"mastery_amount.spell{spell.spell_id}.effect{e.effect_index}.int32",
                                  int(e.amount_at(mastery_f)), int(reply2[1]), integer=True,
                                  types={"integer_conversion": "SpellAuraEffects.h:59 static_cast<int32>"}))

    # -- fill rule cross-check (level-90 / gap witnesses) ---------------------
    if table.fill_rule == FILL_RULE_TRINITY and (witness.class_id, witness.level) in table.filled:
        raw = load()
        mods = raw.race_mods.get(witness.race_id, (0,) * MAX_STATS)
        rows = [(lvl, [v + mods[i] for i, v in enumerate(s)]) for lvl, s in sorted(raw.class_level[witness.class_id].items())]
        req = f"fillgaps {TRINITY_DEFAULT_MAX_PLAYER_LEVEL} {witness.level} {len(rows)} " + " ".join(
            f"{lvl} " + " ".join(str(v) for v in s) for lvl, s in rows)
        reply = probe.ask(req)
        for i in range(MAX_STATS):
            records.append(record(f"fill_rule.{STAT_NAMES[i]}", base["stats"][STAT_NAMES[i]], int(reply[1 + i]),
                                  integer=True, types={"source_type": "int32 copy of the previous cell",
                                                       "evidence_class": "trinity-probe vs trinity-consumer(fill-rule)"}))
        records.append({"field": "fill_rule.tc_log_error_count", "probe": int(reply[0]),
                        "note": "TC_LOG_ERROR lines emitted for this (race, class) at server start"})

    # -- agent E's compiled answers vs the re-resolution (same Python oracle) -----
    if witness.compiled_python is not None:
        cp = witness.compiled_python
        pairs = [(f"compiled.stat.{n}", cp.get("stats", {}).get(n), resolved.stat(i)) for i, n in STAT_NAMES.items()]
        pairs += [(f"compiled.{k}", cp.get(k), getattr(resolved, k)) for k in
                  ("max_health", "armor", "bonus_armor", "attack_power", "ranged_attack_power", "spell_power")]
        for name, stored, fresh in pairs:
            if stored is None:
                raise SourceError(f"{witness.name}: compiled stats lack {name}; unknown shape")
            records.append(record(name, fresh, stored, integer=True, types={
                "source_type": "agent E compiled derived.stats.value (charstats double)",
                "evidence_class": "consistency (not a Trinity comparison)"}))
        records.append(record("compiled.mastery_value", resolved.mastery_value, cp.get("mastery_value"),
                              integer=False, tolerance=0.0, types={"evidence_class": "consistency"}))
        if witness.apply_armor_specialization and resolved.armor_specialization_applied is None:
            for r in records:
                if r["field"].startswith("compiled.") and "match" in r and not r["match"]:
                    r["classification"] = ("compiler-vs-charstats: the compiler applies an armour specialisation "
                                           "found outside SpecializationSpells (class skill line); "
                                           "charstats.acquisition only searches SpecializationSpells")
        # Trinity arithmetic over the compiler's own stat stages (its actual prepared state)
        stages = cp.get("stat_stages")
        if not isinstance(stages, dict) or set(stages) != set(STAT_NAMES.values()):
            raise SourceError(f"{witness.name}: compiled stat_stages missing or unknown shape")
        T_CSTAGE = dict(T_STAT, evidence_class="differential over the compiler's stat stages")
        for i, n in STAT_NAMES.items():
            st = stages[n]
            if (st.get("base_flat"), st.get("base_pct")) != (0.0, 1.0):
                raise SourceError(f"{witness.name}: stat stage {n} uses base_flat/base_pct; not modelled here")
            tp = f32(float(st["total_pct"]))
            reply = probe.ask(f"totalstat {st['create_value']} {st['total_flat']!r} 100 1 0 {tp!r}")
            rec = record(f"compiled_trinity.stat.{n}", st["value"], int(reply[1]), types=T_CSTAGE, integer=True)
            if not rec["match"] and tp != 1.0:
                rec["classification"] = (BINARY32_CLASS + f": {st['create_value']} + {st['total_flat']} x "
                                         f"f32({st['total_pct']!r}) = {reply[0]}")
            records.append(rec)
        cstats = cp["stats"]
        reply = probe.ask(f"maxhealth 0 {witness.health} 1 0 1 {cstats['Stamina']} {witness.level} {hps!r}")
        records.append(record("compiled_trinity.max_health", cp["max_health"], int(reply[0]), integer=True,
                              types={"evidence_class": "differential over the compiler's stats"}))
        reply = probe.ask(f"ap {cstats['Strength']} {cstats['Agility']} "
                          f"{klass.attack_power_per_strength!r} {klass.attack_power_per_agility!r} 1")
        records.append(record("compiled_trinity.attack_power", cp["attack_power"], int(reply[0]), integer=True,
                              types={"evidence_class": "differential over the compiler's stats"}))

    if any(r.get("classification", "").startswith(BINARY32_CLASS) for r in records):
        for r in records:
            if "match" in r and not r["match"] and not r.get("classification"):
                r["classification"] = (BINARY32_CLASS + " (downstream): python side consumed the double-truncated "
                                       "stat; probe side the float-truncated one")
    mismatches = [r for r in records if "match" in r and not r["match"]]
    return {
        "witness": witness.to_dict(),
        "base_stats": base,
        "python": {"stats": {STAT_NAMES[s]: resolved.stat(s) for s in range(MAX_STATS)},
                   "max_health": resolved.max_health, "armor": resolved.armor, "attack_power": resolved.attack_power,
                   "spell_power": resolved.spell_power, "mastery_value": resolved.mastery_value,
                   "hp_per_sta": resolved.hp_per_sta, "warnings": resolved.warnings},
        "records": records,
        "mismatch_count": len(mismatches),
        "mismatch_fields": [m["field"] for m in mismatches],
    }


def type_witnesses(probe: Probe) -> list[dict[str, Any]]:
    """Discriminating cases that do not need a character."""
    out = []
    r = probe.ask("maxpower 5 0 1 0 0.5")
    out.append({"case": "lroundf half-away: 2.5 -> 3 (Python round(2.5) == 2)", "probe": int(r[0]),
                "python_round": round(2.5), "python_int": int(2.5), "reachable": "only through a percent mana aura "
                "producing an exact .5 (aura stage, not the flat model)", "coordinate": "StatSystem.cpp:344"})
    r = probe.ask("applyrating 30000 5000")
    out.append({"case": "int16 rating accumulator: 30000 + 5000", "probe": int(r[0]), "python": 35000,
                "wraps": int(r[0]) != 35000, "coordinate": "Player.cpp:5233 (m_baseRatingValue int16, Player.h:3254)",
                "reachable": "unresolved: requires a single CombatRatings slot above 32767 after CombatRatingsMultByILvl"})
    r = probe.ask("maxhealth 0 0 1 0 1 5 1 -1")
    out.append({"case": "HpPerSta row missing -> 10.0f fallback (5 stamina, no row)", "probe": int(r[0]),
                "expected": 50, "coordinate": "StatSystem.cpp:286"})
    r = probe.ask("maxhealth 0 0 1 0 1 0 80 20")
    out.append({"case": "SetMaxHealth floors 0 to 1", "probe": int(r[0]), "coordinate": "Unit.cpp:10018"})
    r = probe.ask("buildlevelinfo 1 80 90 17647 12176 86452 12000 0")
    out.append({"case": "BuildPlayerLevelInfo (only reachable with MaxPlayerLevel < level) warrior 80->90",
                "probe": [int(x) for x in r], "input_level_80": [17647, 12176, 86452, 12000, 0],
                "coordinate": "ObjectMgr.cpp:4452-4530", "evidence_class": "trinity-probe",
                "note": "not Trinity's default path; recorded to show what a MaxPlayerLevel=80 server would serve"})
    r = probe.ask("fillgaps 90 90 2 1 1 2 3 4 5 80 17647 12176 86452 12000 0")
    out.append({"case": "fill rule: rows at 1 and 80 only, query 90", "probe_errors": int(r[0]),
                "probe_stats": [int(x) for x in r[1:]], "coordinate": "ObjectMgr.cpp:4336-4356"})
    r = probe.ask("fillgaps 90 90 1 80 17647 12176 86452 12000 0")
    out.append({"case": "fill rule: level 1 missing", "probe": r[0], "coordinate": "ObjectMgr.cpp:4342-4346"})
    return out


def generic_mastery_evidence(tables: Tables) -> dict[str, Any]:
    """Re-read, from the snapshot, every DB2 fact the 114585 chain rests on (fails closed)."""
    sid = GENERIC_MASTERY_SPELL
    misc = [r for r in tables("SpellMisc") if r["SpellID"] == sid and r["DifficultyID"] == 0]
    effects = [r for r in tables("SpellEffect") if r["SpellID"] == sid]
    sla = [r for r in tables("SkillLineAbility") if r["Spell"] == sid]
    if len(misc) != 1 or not effects or len(sla) != 1:
        raise SourceError(f"spell {sid}: expected 1 SpellMisc, >=1 SpellEffect, 1 SkillLineAbility row; "
                          f"got {len(misc)}, {len(effects)}, {len(sla)}")
    skill = sla[0]["SkillLine"]
    srci = [r for r in tables("SkillRaceClassInfo") if r["SkillID"] == skill]
    levels = [r for r in tables("SpellLevels") if r["SpellID"] == sid]
    attr0 = int(misc[0]["Attributes_0"])
    return {
        "spell_id": sid,
        "spell_misc_attributes_0": attr0,
        "is_passive": bool(attr0 & 0x40),
        "effects": [{"id": r["ID"], "effect_index": r["EffectIndex"], "effect": r["Effect"],
                     "effect_aura": r["EffectAura"], "base_points": r["EffectBasePointsF"]} for r in effects],
        "skill_line_ability": {k: sla[0][k] for k in ("ID", "SkillLine", "AcquireMethod", "ClassMask",
                                                       "RaceMasks_0", "RaceMasks_1", "MinSkillLineRank")},
        "skill_race_class_info": [{k: r[k] for k in ("ID", "SkillID", "ClassMask", "RaceMasks_0", "Availability",
                                                     "MinLevel", "Flags")} for r in srci],
        "spell_levels": [{k: r[k] for k in ("DifficultyID", "BaseLevel", "SpellLevel")} for r in levels],
        "stance_rows": sum(1 for r in tables("SpellShapeshift") if r["SpellID"] == sid),
        "aura_restriction_rows": sum(1 for r in tables("SpellAuraRestrictions") if r["SpellID"] == sid),
        "equipped_item_rows": sum(1 for r in tables("SpellEquippedItems") if r["SpellID"] == sid),
        "chain": [
            "ObjectMgr.cpp:4063-4070 playercreate skills: SkillRaceClassInfo Availability == 1, class/race mask match",
            "Player.cpp:25259 LearnDefaultSkills -> LearnDefaultSkill -> SetSkill",
            "Player.cpp:25391 LearnSkillRewardedSpells (AutomaticCharLevel accepted at 25407; no class/race mask; "
            "required level max(SpellLevel, BaseLevel) = 0)",
            "Player.cpp:2919-2929 AddSpell: IsPassive -> HandlePassiveSpellLearn (3079: no Stances, "
            "EquippedItemClass < 0, no CasterAuraState -> need_cast) -> CastSpell(this, id, true)",
            "SpellAuraEffects.cpp:5571 HandleMastery -> Player::UpdateMastery",
            "StatSystem.cpp:548-549 value = GetTotalAuraModifier(SPELL_AURA_MASTERY) + GetRatingBonusValue(CR_MASTERY)",
            "StatSystem.cpp:543-547 / Player.cpp:30525 gate: Mastery is 0 unless the primary spec's "
            "MasterySpellID[0|1] is known (CanUseMastery)",
        ],
        "evidence_class": "trinity-consumer (static trace) + db2-fact; application at runtime not executed",
        "contradicts": "docs/research/character-stat-pipeline-archaeology.md \u00a79 and charstats.derived (rating term only)",
    }


ARMOR_SPEC_PERCENT = 5.0


def armor_spec_truncation_sweep(probe: Probe, upto: int = 120000, percent: float = ARMOR_SPEC_PERCENT) -> dict[str, Any]:
    """int32(float total * f32(1 + pct/100)) (Trinity) vs int(double total * (1 + pct/100)) (charstats).

    ``total`` is the pre-percent stat (create + item flat); every value 1..upto is
    exact in both binary32 and binary64, so the only difference is the multiplier's
    representation and the float product.
    """
    tp = f32(1.0 + percent / 100.0)
    divergences: list[int] = []
    deltas: set[int] = set()
    for n in range(1, upto + 1):
        reply = probe.ask(f"totalstat {n} 0 100 1 0 {tp!r}")
        py = int(n * (1.0 + percent / 100.0))
        if py != int(reply[1]):
            divergences.append(n)
            deltas.add(py - int(reply[1]))
    by_residue: dict[int, int] = {}
    for n in divergences:
        by_residue[n % 20] = by_residue.get(n % 20, 0) + 1
    return {
        "percent": percent, "total_pct_float": tp, "total_pct_double": 1.0 + percent / 100.0,
        "range": [1, upto], "divergence_count": len(divergences),
        "divergences_by_total_mod_20": {str(k): v for k, v in sorted(by_residue.items())},
        "multiples_of_20_in_range": upto // 20,
        "first": [{"total": n, "python_double": n * (1.0 + percent / 100.0),
                   "trinity_int32": int(probe.ask(f"totalstat {n} 0 100 1 0 {tp!r}")[1])} for n in divergences[:8]],
        "python_minus_trinity_values": sorted(deltas),
        "coordinates": ["Unit.cpp:5016-5033 (double multiplier returned as static_cast<float>)",
                        "Unit.cpp:9828 GetTotalStatValue (float)", "StatSystem.cpp:112 SetStat(int32(value))"],
        "evidence_class": "differential (trinity-probe vs charstats double)",
        "reachable": "yes: any armour-specialised player whose create + item primary stat is an affected total "
                     "(e.g. Human Warrior L80 create Strength 17647 + 9993 item Strength = 27640)",
        "retail_truth": "unresolved: Trinity's float path is the consumer oracle; the Retail client/server "
                        "precision is not observable from the snapshot",
    }


def rating_int32_sweep(probe: Probe, resolver: CharacterResolver, rating: str, level: int,
                       upto: int = 4000) -> dict[str, Any]:
    """Search rating amounts for a value where int32(C++ float) != int32(Python double).

    This is the reachable-divergence question of the 41,999 vs 42,000 ms cooldown
    case, asked of the rating -> percent conversion (linear and diminished).
    """
    column = resolver.ratings.gt_combat_ratings.row(level)[CR_INDEX[rating]]
    curve = curve_args(resolver, rating)
    divergences: list[dict[str, Any]] = []
    max_rel = 0.0
    for amount in range(1, upto + 1):
        c = resolver.ratings.convert(rating, float(amount), level)
        reply = probe.ask(f"ratingbonus {CR_INDEX[rating]} {amount} {column!r} {curve}")
        linear_c, final_c = float(reply[0]), float(reply[1])
        for name, py, cc in (("linear", c.linear_percent, linear_c), ("final", c.final_percent, final_c)):
            rel = abs(py - cc) / max(abs(py), 1e-12)
            max_rel = max(max_rel, rel)
            if int(py) != int(cc):
                divergences.append({"amount": amount, "stage": name, "python": py, "probe": cc,
                                    "int32_python": int(py), "int32_probe": int(cc)})
    return {"rating": rating, "level": level, "column": column, "amounts_swept": upto,
            "max_rel_diff": max_rel, "int32_divergences": divergences[:20],
            "int32_divergence_count": len(divergences),
            "note": "int32() of the percentage is not a Trinity consumer step for these ratings "
                    "(percentages stay float); the sweep bounds the float-vs-double gap and shows whether a "
                    "truncation boundary is crossed anywhere in the range"}


# ---------------------------------------------------------------------------
# fixtures (agent E) -- fail closed on unknown shape
# ---------------------------------------------------------------------------

COMPILED_BOUNDARY_STEP = "boundary"


def witness_from_e_compiled(path: Path, raw: dict[str, Any]) -> Witness:
    """Agent E's compiler output (``character_prep.compiler.Compiler.compile``).

    Shape relied on (anything else raises): ``fixture.identity`` {race_id, class_id,
    spec_id, level}; ``validation.ok`` true; ``derived.stats.provenance`` containing
    exactly one ``{"step": "boundary", "handed_over": Contributions.to_dict()}``.  The
    Python answers E stored (``derived.stats.value``) are not used as the oracle; the
    differential re-resolves from the handed-over contributions and cross-checks them.
    """
    if not (raw.get("validation") or {}).get("ok", False):
        raise SourceError(f"{path}: compiled fixture failed validation ({(raw.get('validation') or {}).get('errors')}); "
                          "not compared")
    identity = (raw.get("fixture") or {}).get("identity")
    stats_node = (raw.get("derived") or {}).get("stats")
    if not isinstance(identity, dict) or not isinstance(stats_node, dict):
        raise SourceError(f"{path}: unknown compiled fixture shape (fixture.identity / derived.stats missing)")
    boundary = [p for p in stats_node.get("provenance") or [] if isinstance(p, dict)
                and p.get("step") == COMPILED_BOUNDARY_STEP]
    if len(boundary) != 1 or not isinstance(boundary[0].get("handed_over"), dict):
        raise SourceError(f"{path}: unknown compiled fixture shape (expected one derived.stats boundary step)")
    handed = boundary[0]["handed_over"]
    expected_keys = {"stats", "ratings", "armor", "attack_power", "spell_power", "health", "source"}
    if set(handed) != expected_keys:
        raise SourceError(f"{path}: unknown contributions keys {sorted(set(handed) ^ expected_keys)}")
    fill_rule = FILL_RULE_NONE
    note = ""
    declared = (((raw.get("provenance") or {}).get("base_stats_source") or {}).get("fill_rule")) or FILL_RULE_NONE
    if declared not in (FILL_RULE_NONE, FILL_RULE_TRINITY):
        raise SourceError(f"{path}: unknown base_stats_source.fill_rule {declared!r}")
    if declared == FILL_RULE_TRINITY:
        fill_rule = FILL_RULE_TRINITY
        note = ("the fixture opted into Trinity's base-stat fill rule (server_inputs.base_stats.fill_rule); filled cells "
                "are trinity-consumer(fill-rule), not Retail truth")
    if stats_node.get("evidence_class") == "unresolved":
        steps = [p.get("result") for p in stats_node.get("provenance") or [] if p.get("step") == "base-primary-stats"]
        if steps != ["level-row-missing"]:
            raise SourceError(f"{path}: derived.stats unresolved for a reason other than a missing level row ({steps})")
        fill_rule = FILL_RULE_TRINITY
        note = ("compiler left base stats unresolved (no TDB row at this level); the differential opts into "
                "Trinity's fill rule, values tagged trinity-consumer(fill-rule), not Retail truth")
    armor_node = (raw.get("derived") or {}).get("armor_specialization")
    if not isinstance(armor_node, dict) or "applied_spell_id" not in (armor_node.get("value") or {}):
        raise SourceError(f"{path}: unknown compiled fixture shape (derived.armor_specialization.value.applied_spell_id)")
    w = _witness(path, {"race_id": identity.get("race_id"), "class_id": identity.get("class_id"),
                        "level": identity.get("level"), "spec_id": identity.get("spec_id")},
                 handed, fill_rule, note=note,
                 compiled_python=stats_node.get("value") if stats_node.get("evidence_class") != "unresolved" else None)
    w.apply_armor_specialization = armor_node["value"]["applied_spell_id"] is not None
    return w


def _witness(path: Path, identity: dict[str, Any], contributions: dict[str, Any], fill_rule: str, *,
             note: str = "", compiled_python: dict[str, Any] | None = None) -> Witness:
    try:
        race_id, class_id, level = int(identity["race_id"]), int(identity["class_id"]), int(identity["level"])
    except (KeyError, TypeError, ValueError) as error:
        raise SourceError(f"{path}: identity lacks race_id/class_id/level ({error})") from None
    spec_id = identity.get("spec_id")
    name_to_stat = {v: k for k, v in STAT_NAMES.items()}
    stats: dict[int, int] = {}
    for key, value in (contributions.get("stats") or {}).items():
        stat = name_to_stat.get(key) if not str(key).isdigit() else int(key)
        if stat is None:
            raise SourceError(f"{path}: unknown stat key {key!r}")
        stats[stat] = int(value)
    ratings = {str(k): int(v) for k, v in (contributions.get("ratings") or {}).items()}
    unknown = [r for r in ratings if r not in CR_INDEX]
    if unknown:
        raise SourceError(f"{path}: unknown rating names {unknown}")
    if fill_rule not in (FILL_RULE_NONE, FILL_RULE_TRINITY):
        raise SourceError(f"{path}: unknown fill_rule {fill_rule!r}")
    w = Witness(name=path.name.removesuffix(".json").removesuffix(".compiled"), race_id=race_id,
                class_id=class_id, spec_id=int(spec_id) if spec_id is not None else None, level=level,
                fill_rule=fill_rule, stats=stats, ratings=ratings, armor=int(contributions.get("armor") or 0),
                health=int(contributions.get("health") or 0),
                attack_power=int(contributions.get("attack_power") or 0),
                spell_power=int(contributions.get("spell_power") or 0),
                note=note, source=f"compiled fixture {path.name}")
    w.compiled_python = compiled_python
    return w


def witness_from_compiled(path: Path) -> Witness:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "derived" in raw and "fixture" in raw:
        return witness_from_e_compiled(path, raw)
    identity = raw.get("identity")
    contributions = raw.get("contributions")
    if not isinstance(identity, dict) or not isinstance(contributions, dict):
        raise SourceError(f"{path}: unknown compiled fixture shape (expected 'identity' and 'contributions' "
                          "objects); refusing to guess")
    return _witness(path, identity, contributions, str(raw.get("fill_rule", FILL_RULE_NONE)))


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run_differential(witnesses: list[Witness], tables: Tables | None = None,
                     refused: list[dict[str, str]] | None = None) -> dict[str, Any]:
    tables = tables or Tables(TABLES)
    corpus = load()
    pairs = playercreateinfo_pairs()
    tables_by_rule = {
        FILL_RULE_NONE: corpus.table(FILL_RULE_NONE),
        FILL_RULE_TRINITY: corpus.table(FILL_RULE_TRINITY, pairs=pairs) if pairs else None,
    }
    probe = Probe()
    try:
        results = []
        for w in witnesses:
            table = tables_by_rule[w.fill_rule]
            if table is None:
                raise SourceError("fill rule requested but playercreateinfo pairs unknown; run base-stat-gap --tdb first")
            resolver = CharacterResolver(tables, table)
            results.append(compare_witness(resolver, table, probe, w))
        types = type_witnesses(probe)
        mastery_evidence = generic_mastery_evidence(tables)
        armor_sweep = armor_spec_truncation_sweep(probe)
        sweep_resolver = CharacterResolver(tables, tables_by_rule[FILL_RULE_NONE])
        sweeps = [rating_int32_sweep(probe, sweep_resolver, "Mastery", 80),
                  rating_int32_sweep(probe, sweep_resolver, "CritMelee", 90),
                  rating_int32_sweep(probe, sweep_resolver, "VersatilityDamageDone", 80)]
    finally:
        probe.close()
    hps = tables.gametable("HpPerSta")
    total = sum(len([r for r in x["records"] if "match" in r]) for x in results)
    mism = sum(x["mismatch_count"] for x in results)
    return {
        "provenance": provenance("python3 character_prep.py differential --out "
                                 "../../docs/research/character-prep-corpora/differential.json"),
        "probe": {"path": str(PROBE.relative_to(ROOT)), "build": "make -C scripts/research/tools/tc_prep_probe",
                  "flags": "g++ -std=c++20 -O0 -ffp-contract=off"},
        "hp_per_sta_witness": {"level_80": hps.column(80, 0), "level_81": hps.column(81, 0),
                               "level_90": hps.column(90, 0), "evidence_class": "db2-fact",
                               "consumer": "StatSystem.cpp:283-293"},
        "summary": {"witnesses": len(results), "records": total, "mismatches": mism,
                    "fixture_witnesses": sum(1 for w in witnesses if w.source.startswith("compiled fixture")),
                    "refused_fixtures": len(refused or []),
                    "mismatch_fields": sorted({f for x in results for f in x["mismatch_fields"]})},
        "type_witnesses": types,
        "generic_mastery_114585": mastery_evidence,
        "rating_int32_sweeps": sweeps,
        "armor_spec_truncation_sweep": armor_sweep,
        "refused_fixtures": refused or [],
        "witnesses": results,
    }


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("differential", help="Python oracle vs extracted TrinityCore C++ probe")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--fixture", type=Path, action="append", default=[],
                   help="compiled fixture(s) from character-prep-corpora/fixtures/*.compiled.json")
    p.add_argument("--fixtures-dir", type=Path, default=CORPORA / "fixtures",
                   help="also load every *.compiled.json here if present")
    p.add_argument("--no-defaults", action="store_true", help="skip the built-in witness set")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    witnesses = [] if args.no_defaults else default_witnesses()
    paths = list(args.fixture)
    if args.fixtures_dir.is_dir():
        paths += sorted(args.fixtures_dir.glob("*.compiled.json"))
    refused: list[dict[str, str]] = []
    for path in paths:
        try:
            witnesses.append(witness_from_compiled(path))
        except SourceError as error:
            if path in args.fixture:
                raise                      # an explicitly named fixture must be comparable
            refused.append({"fixture": path.name, "reason": str(error).replace(str(ROOT) + "/", "")})
    if not witnesses:
        raise SystemExit("no witnesses")
    payload = run_differential(witnesses, refused=refused)
    for r in refused:
        print(f"refused (fail closed): {r['fixture']}: {r['reason']}")
    if args.out is not None:
        digest = write_corpus(args.out, payload)
        print(f"wrote {args.out} sha256 {digest}")
    print(json.dumps(payload["summary"], indent=1))
    for w in payload["witnesses"]:
        print(f"{w['witness']['name']}: {w['mismatch_count']} mismatches {w['mismatch_fields']}")
    return 0
