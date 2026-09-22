#!/usr/bin/env python3
"""Race/class skill and automatic-acquisition census (research only).

Replays, over the pinned DB2 snapshot, the static part of TrinityCore
7f3d43b's default-skill pipeline for every ``playercreateinfo`` (race, class)
pair, and reports what each pair acquires and why:

    ObjectMgr::LoadPlayerInfo skills        Globals/ObjectMgr.cpp:4059-4072
    Player::LearnDefaultSkills / Skill       Entities/Player/Player.cpp:25259-25314
    GetSkillRangeType                        Globals/ObjectMgr.cpp:9002-9025
    Player::SetSkill                         Entities/Player/Player.cpp:5659-5880
    Player::LearnSkillRewardedSpells         Entities/Player/Player.cpp:25391-25441
    Player::AddSpell cascades                Entities/Player/Player.cpp:2690-3060
    DB2Manager skill indexes                 DataStores/DB2Stores.cpp:1523-1532, 2986-3012
    SpellMgr learn-skill / learn-spell maps  Spells/SpellMgr.cpp:958-1153

This is a static reading, not an execution of Trinity. It is not Core
semantic authority. World-database inputs (skill_tiers, conditions source 35,
spell_learn_spell, playercreateinfo) come from ``--world`` (a
``tools/tdb_world_extract.py`` output).

Usage::

    python3 scripts/research/skill_acquisition.py --world docs/research/skill-acquisition-corpora/world-inputs.json \\
        --out docs/research/skill-acquisition-corpora [--level 90]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
TABLES = ROOT / "data" / "tables"
TRINITY = ROOT.parent / "TrinityCore"

SKILL_RUNEFORGING = 776
SKILL_DUAL_WIELD = 118
CAT_ARMOR, CAT_LANGUAGES = 8, 10
FLAG_ALWAYS_MAX = 0x10
CLASS_DEATH_KNIGHT = 6
SPELL_ATTR0_PASSIVE = 0x40
SPELL_ATTR1_CAST_WHEN_LEARNED = 0x80000000
EFF_LEARN_SPELL, EFF_LANGUAGE, EFF_DUAL_WIELD, EFF_SKILL_STEP = 36, 39, 40, 44
EFF_PROFICIENCY, EFF_SKILL, EFF_TITAN_GRIP = 60, 118, 155
EFF_APPLY_AURA = {6, 35, 65, 119, 128, 129, 143, 174, 202}
AURA_MASTERY = 318
TARGET_UNIT_PET = 5
METHOD = {0: "Learned", 1: "AutomaticSkillRank", 2: "AutomaticCharLevel", 3: "NeverLearned",
          4: "LearnedOrAutomaticCharLevel"}
CATEGORY = {5: "attributes", 6: "weapon", 7: "class", 8: "armor", 9: "secondary", 10: "languages",
            11: "profession", 12: "generic", 27: "other-27"}


def read(name: str) -> list[dict[str, str]]:
    with open(TABLES / f"{name}.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def i(v: str | None) -> int:
    return int(v) if v not in (None, "") else 0


def sha(name: str) -> str:
    return hashlib.sha256((TABLES / f"{name}.csv").read_bytes()).hexdigest()


def git_head(path: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class Data:
    def __init__(self, world: dict):
        self.skill = {i(r["ID"]): r for r in read("SkillLine")}
        self.srci = sorted(read("SkillRaceClassInfo"), key=lambda r: i(r["ID"]))
        self.sla = sorted(read("SkillLineAbility"), key=lambda r: i(r["ID"]))
        self.race_bit = {i(r["ID"]): i(r["PlayableRaceBit"]) for r in read("ChrRaces")}
        self.race_name = {i(r["ID"]): r["Name_lang"] for r in read("ChrRaces")}
        self.class_name = {i(r["ID"]): r["Name_lang"] for r in read("ChrClasses")}
        self.spell_name = {i(r["ID"]): r["Name_lang"] for r in read("SpellName")}
        self.misc = {i(r["SpellID"]): r for r in read("SpellMisc") if i(r["DifficultyID"]) == 0}
        self.levels = {i(r["SpellID"]): r for r in read("SpellLevels") if i(r["DifficultyID"]) == 0}
        self.effects: dict[int, list[dict]] = defaultdict(list)
        for r in read("SpellEffect"):
            if i(r["DifficultyID"]) == 0:
                self.effects[i(r["SpellID"])].append(r)
        self.equipped = {i(r["SpellID"]): r for r in read("SpellEquippedItems")}
        self.specs = [r for r in read("ChrSpecialization")]
        self.spec_spells: dict[int, list[dict]] = defaultdict(list)
        for r in sorted(read("SpecializationSpells"), key=lambda r: i(r["ID"])):
            self.spec_spells[i(r["SpecID"])].append(r)
        self.learn_spell_db2 = read("SpellLearnSpell")

        # DB2Manager indexes (DB2Stores.cpp:1527-1532)
        self.sla_by_key: dict[int, list[dict]] = defaultdict(list)
        self.sla_by_spell: dict[int, list[dict]] = defaultdict(list)
        for r in self.sla:
            key = i(r["SkillupSkillLineID"]) or i(r["SkillLine"])
            self.sla_by_key[key].append(r)
            self.sla_by_spell[i(r["Spell"])].append(r)
        self.srci_by_skill: dict[int, list[dict]] = defaultdict(list)
        for r in self.srci:
            if i(r["SkillID"]) in self.skill:
                self.srci_by_skill[i(r["SkillID"])].append(r)

        t = world["tables"]
        self.tiers = {row[0]: row[1:] for row in t["skill_tiers"]["rows"]}
        cols = t["conditions"]["columns"]
        self.conditions: dict[int, list[dict]] = defaultdict(list)
        for row in t["conditions"]["rows"]:
            c = dict(zip(cols, row))
            self.conditions[c["SourceEntry"]].append(c)
        self.pairs = sorted({(p[0], p[1]) for p in t["playercreateinfo"]["rows"]})
        self.learn_spell_world = t["spell_learn_spell"]["rows"]

        # SpellMgr::LoadSpellLearnSkills (SpellMgr.cpp:958-999)
        self.learn_skill: dict[int, dict] = {}
        for sid, effs in self.effects.items():
            for e in sorted(effs, key=lambda e: i(e["EffectIndex"])):
                eff = i(e["Effect"])
                if eff == EFF_SKILL:
                    self.learn_skill[sid] = {"skill": i(e["EffectMiscValue_0"]),
                                             "step": int(float(e["EffectBasePointsF"] or 0)), "value": 0, "max": 0}
                    break
                if eff == EFF_DUAL_WIELD:
                    self.learn_skill[sid] = {"skill": SKILL_DUAL_WIELD, "step": 1, "value": 1, "max": 1}
                    break

        # SpellMgr::LoadSpellLearnSpells (SpellMgr.cpp:1001-1153)
        self.learn_edges: dict[int, list[dict]] = defaultdict(list)
        for src, dst, active in self.learn_spell_world:
            self.learn_edges[src].append({"spell": dst, "auto": False, "via": "spell_learn_spell", "active": bool(active)})
        for sid, effs in self.effects.items():
            for e in effs:
                if i(e["Effect"]) != EFF_LEARN_SPELL:
                    continue
                dst = i(e["EffectTriggerSpell"])
                if dst not in self.spell_name:
                    continue
                auto = (i(e["ImplicitTarget_0"]) == TARGET_UNIT_PET or self.passive(sid)
                        or any(i(x["Effect"]) == EFF_SKILL_STEP for x in effs))
                self.learn_edges[sid].append({"spell": dst, "auto": auto, "via": "raw-36", "active": True})
        for r in self.learn_spell_db2:
            src, dst = i(r["SpellID"]), i(r["LearnSpellID"])
            if src in self.spell_name and dst in self.spell_name and \
                    not any(x["spell"] == dst for x in self.learn_edges[src]):
                self.learn_edges[src].append({"spell": dst, "auto": False, "via": "SpellLearnSpell.db2", "active": True})

    # ---- predicates -------------------------------------------------------
    def race_in(self, m0: int, m1: int, race: int) -> bool:
        bit = self.race_bit.get(race, -1)
        if bit < 0:
            return False
        word = m0 if bit < 32 else m1
        return bool((word & 0xFFFFFFFF) >> (bit % 32) & 1)

    def srci_matches(self, r: dict, race: int, cls: int) -> bool:
        m0, m1, cm = i(r["RaceMasks_0"]), i(r["RaceMasks_1"]), i(r["ClassMask"])
        if (m0 or m1) and not self.race_in(m0, m1, race):
            return False
        return not (cm and not (cm & (1 << (cls - 1))))

    def get_srci(self, skill: int, race: int, cls: int) -> dict | None:
        # DB2Manager::GetSkillRaceClassInfo returns the first match (DB2Stores.cpp:2991-3004)
        for r in self.srci_by_skill.get(skill, []):
            if self.srci_matches(r, race, cls):
                return r
        return None

    def passive(self, sid: int) -> bool:
        m = self.misc.get(sid)
        return bool(m and i(m["Attributes_0"]) & SPELL_ATTR0_PASSIVE)

    def spell_level(self, sid: int) -> int:
        lv = self.levels.get(sid)
        return max(i(lv["SpellLevel"]), i(lv["BaseLevel"])) if lv else 0

    def range_type(self, r: dict) -> str:
        skill = self.skill.get(i(r["SkillID"]))
        if not skill:
            return "NONE"
        if i(r["SkillTierID"]) in self.tiers:
            return "RANK"
        if i(r["SkillID"]) == SKILL_RUNEFORGING:
            return "MONO"
        cat = i(skill["CategoryID"])
        if cat == CAT_ARMOR:
            return "MONO"
        if cat == CAT_LANGUAGES:
            return "LANGUAGE"
        return "LEVEL"


class Character:
    """Static replay of one (race, class, level[, spec]) preparation."""

    def __init__(self, d: Data, race: int, cls: int, level: int):
        self.d, self.race, self.cls, self.level = d, race, cls, level
        self.skills: dict[int, dict] = {}
        self.spells: dict[int, dict] = {}
        self.gated: list[dict] = []
        self.conditional: list[dict] = []
        self.runtime_learn: list[dict] = []
        self.none_range: list[int] = []

    # Player::LearnDefaultSkills (Player.cpp:25259-25275)
    def learn_default_skills(self) -> None:
        for r in self.d.srci:
            if i(r["Availability"]) != 1:
                continue
            m0, m1, cm = i(r["RaceMasks_0"]), i(r["RaceMasks_1"]), i(r["ClassMask"])
            if (m0 or m1) and not self.d.race_in(m0, m1, self.race):
                continue
            if not (cm in (-1, 0) or (1 << (self.cls - 1)) & cm):
                continue
            skill = i(r["SkillID"])
            if skill in self.skills:
                continue
            if i(r["MinLevel"]) > self.level:
                self.gated.append({"kind": "skill-min-level", "skill": skill, "srci": i(r["ID"]),
                                   "min_level": i(r["MinLevel"])})
                continue
            self.learn_default_skill(r, via=f"default:SRCI {i(r['ID'])}")

    # Player::LearnDefaultSkill (Player.cpp:25277-25314)
    def learn_default_skill(self, r: dict, via: str) -> None:
        rt = self.d.range_type(r)
        maxv = self.level * 5
        flags = i(r["Flags"])
        if rt == "LANGUAGE":
            value, mx = 300, 300
        elif rt == "LEVEL":
            value = maxv if flags & FLAG_ALWAYS_MAX else (
                min(max(1, (self.level - 1) * 5), maxv) if self.cls == CLASS_DEATH_KNIGHT else 1)
            mx = maxv
        elif rt == "MONO":
            value, mx = 1, 1
        elif rt == "RANK":
            tier = self.d.tiers[i(r["SkillTierID"])]
            mx = tier[0]
            value = mx if flags & FLAG_ALWAYS_MAX else (
                min(max(1, (self.level - 1) * 5), mx) if self.cls == CLASS_DEATH_KNIGHT else 1)
        else:
            self.none_range.append(i(r["SkillID"]))
            return
        self.set_skill(i(r["SkillID"]), value, mx, i(r["ID"]), rt, via)

    def set_skill(self, skill: int, value: int, mx: int, srci: int | None, rt: str, via: str) -> None:
        if value == 0:
            return
        self.skills[skill] = {"value": value, "max": mx, "srci": srci, "range": rt, "via": via}
        self.learn_skill_rewarded(skill, value)

    # Player::LearnSkillRewardedSpells (Player.cpp:25391-25441)
    def learn_skill_rewarded(self, skill: int, value: int) -> None:
        d = self.d
        for a in d.sla_by_key.get(skill, []):
            sid = i(a["Spell"])
            if sid not in d.spell_name:
                continue
            method = i(a["AcquireMethod"])
            if method not in (1, 2, 4):
                continue
            m0, m1 = i(a["RaceMasks_0"]), i(a["RaceMasks_1"])
            if (m0 or m1) and not d.race_in(m0, m1, self.race):
                continue
            cm = i(a["ClassMask"])
            if cm and not (cm & (1 << (self.cls - 1))):
                continue
            if method == 4:
                pc = i(d.misc.get(sid, {}).get("ShowFutureSpellPlayerConditionID"))
                conds = d.conditions.get(i(a["ID"]), [])
                if pc or conds:
                    self.conditional.append({"sla": i(a["ID"]), "spell": sid, "skill": skill,
                                             "player_condition": pc,
                                             "conditions": [c.get("Comment") for c in conds]})
                    continue
            req = d.spell_level(sid)
            if req > self.level:
                self.gated.append({"kind": "spell-level", "sla": i(a["ID"]), "spell": sid, "skill": skill,
                                   "level": req})
                continue
            if value < i(a["MinSkillLineRank"]) and method == 1:
                self.gated.append({"kind": "skill-rank", "sla": i(a["ID"]), "spell": sid, "skill": skill,
                                   "min_rank": i(a["MinSkillLineRank"]), "value": value})
                continue
            self.add_spell(sid, from_skill=i(a["SkillLine"]), via=f"skill {skill} / SLA {i(a['ID'])}")

    # Player::AddSpell (Player.cpp:2690-3060), static subset
    def add_spell(self, sid: int, from_skill: int = 0, via: str = "") -> None:
        d = self.d
        if sid in self.spells or sid not in d.spell_name:
            return
        self.spells[sid] = {"via": via}
        for prev in d.sla_by_spell.get(sid, []):
            sup = i(prev["SupercedesSpell"])
            if sup and sup in d.spell_name:  # lower rank learned first (Player.cpp:2845-2851)
                self.add_spell(sup, from_skill, via=f"rank-chain of {sid}")
        ls = d.learn_skill.get(sid)
        if ls:
            if ls["skill"] != from_skill:
                self.spell_learn_skill(ls, sid)
        else:
            for a in d.sla_by_spell.get(sid, []):
                skill = i(a["SkillLine"])
                if skill not in d.skill or skill == from_skill:
                    continue
                if (i(a["AcquireMethod"]) == 2 and skill not in self.skills) or \
                        (skill == SKILL_RUNEFORGING and i(a["TrivialSkillLineRankHigh"]) == 0):
                    rc = d.get_srci(skill, self.race, self.cls)
                    if rc and skill not in self.skills:
                        self.learn_default_skill(rc, via=f"spell {sid} (SLA {i(a['ID'])}, AutomaticCharLevel)")
        for e in d.learn_edges.get(sid, []):
            if e["auto"]:
                self.runtime_learn.append({"source": sid, "spell": e["spell"], "via": e["via"]})
            else:
                self.add_spell(e["spell"], via=f"{e['via']} from {sid}")

    def spell_learn_skill(self, ls: dict, sid: int) -> None:
        d = self.d
        skill = ls["skill"]
        cur = self.skills.get(skill, {}).get("value", 0)
        value = max(cur, ls["value"])
        mx = ls["max"]
        rc = None
        rt = "fixed"
        if mx == 0:
            rc = d.get_srci(skill, self.race, self.cls)
            if rc:
                rt = d.range_type(rc)
                if rt == "LANGUAGE":
                    value, mx = 300, 300
                elif rt == "LEVEL":
                    mx = self.level * 5
                elif rt == "MONO":
                    mx = 1
                elif rt == "RANK":
                    tier = d.tiers[i(rc["SkillTierID"])]
                    mx = tier[max(ls["step"] - 1, 0)]
                if i(rc["Flags"]) & FLAG_ALWAYS_MAX:
                    value = mx
        if skill in d.skill:
            self.set_skill(skill, value, mx, i(rc["ID"]) if rc else None, rt, f"spell {sid} learn-skill")

    def learn_spec(self, spec: int) -> None:
        for r in self.d.spec_spells.get(spec, []):
            sid = i(r["SpellID"])
            if sid in self.d.spell_name and i(self.d.levels.get(sid, {}).get("SpellLevel")) <= self.level:
                self.add_spell(sid, via=f"spec {spec} (SpecializationSpells {i(r['ID'])})")


# ---- classification ---------------------------------------------------------
def classify_spell(d: Data, sid: int) -> list[str]:
    tags = []
    effs = d.effects.get(sid, [])
    eq = d.equipped.get(sid)
    for e in effs:
        eff, aura = i(e["Effect"]), i(e["EffectAura"])
        if eff == EFF_PROFICIENCY:
            ic = i(eq["EquippedItemClass"]) if eq else -1
            tags.append({2: "weapon-proficiency", 4: "armor-proficiency"}.get(ic, "proficiency-other"))
        elif eff == EFF_LANGUAGE:
            tags.append("language")
        elif eff == EFF_DUAL_WIELD:
            tags.append("dual-wield")
        elif eff == EFF_TITAN_GRIP:
            tags.append("titan-grip")
        elif eff == EFF_SKILL:
            tags.append("learn-skill")
        elif eff == EFF_LEARN_SPELL:
            tags.append("raw-36-learn-spell")
        elif eff in EFF_APPLY_AURA and aura == AURA_MASTERY:
            tags.append("mastery-aura")
    tags.append("passive" if d.passive(sid) else "active")
    return sorted(set(tags))


def pair_key(race: int, cls: int) -> str:
    return f"{race}:{cls}"


def run(d: Data, level: int) -> dict:
    per_pair: dict[str, dict] = {}
    sla_hits: dict[int, set[str]] = defaultdict(set)
    skill_hits: dict[int, set[str]] = defaultdict(set)
    gated_all: list[dict] = []
    cond_all: list[dict] = []
    runtime_all: Counter = Counter()
    spec_delta: dict[str, dict] = {}
    for race, cls in d.pairs:
        ch = Character(d, race, cls, level)
        ch.learn_default_skills()
        k = pair_key(race, cls)
        per_pair[k] = {"race": race, "class": cls,
                       "skills": {str(s): v for s, v in sorted(ch.skills.items())},
                       "spells": {str(s): v for s, v in sorted(ch.spells.items())},
                       "runtime_raw36": ch.runtime_learn, "none_range": ch.none_range}
        for s in ch.skills:
            skill_hits[s].add(k)
        for sid, v in ch.spells.items():
            for a in d.sla_by_spell.get(sid, []):
                if f"SLA {i(a['ID'])}" in v["via"]:
                    sla_hits[i(a["ID"])].add(k)
        for g in ch.gated:
            gated_all.append({**g, "pair": k})
        for c in ch.conditional:
            cond_all.append({**c, "pair": k})
        for r in ch.runtime_learn:
            runtime_all[(r["source"], r["spell"])] += 1
        # specialization delta: what SpecializationSpells add to the skill system
        for spec in d.specs:
            if i(spec["ClassID"]) != cls:
                continue
            ch2 = Character(d, race, cls, level)
            ch2.learn_default_skills()
            base_sk, base_sp = set(ch2.skills), set(ch2.spells)
            ch2.learn_spec(i(spec["ID"]))
            new_sk = {s: ch2.skills[s]["via"] for s in set(ch2.skills) - base_sk}
            if new_sk:
                spec_delta[f"{k}:{i(spec['ID'])}"] = {
                    "skills_added_by_spec_spells": new_sk,
                    "skill_spells_added": sorted(s for s in set(ch2.spells) - base_sp
                                                 if "skill" in ch2.spells[s]["via"])}
    return {"per_pair": per_pair, "sla_hits": sla_hits, "skill_hits": skill_hits, "gated": gated_all,
            "conditional": cond_all, "runtime": runtime_all, "spec_delta": spec_delta}


def build(d: Data, level: int, world_prov: dict) -> dict[str, object]:
    res = run(d, level)
    npairs = len(d.pairs)
    races = sorted({r for r, _ in d.pairs})
    classes = sorted({c for _, c in d.pairs})

    def scope(keys: set[str]) -> dict:
        rs = {int(k.split(":")[0]) for k in keys}
        cs = {int(k.split(":")[1]) for k in keys}
        all_r = all(any(pair_key(r, c) in keys for c in classes if (r, c) in d.pairs) for r in rs)
        if len(keys) == npairs:
            kind = "every-pair"
        elif all(all(pair_key(r, c) in keys for (r, c) in d.pairs if c == cls) for cls in cs) and \
                not any(pair_key(r, c) in keys for (r, c) in d.pairs if c not in cs) and len(cs) < len(classes):
            kind = "class-restricted"
        elif all(all(pair_key(r, c) in keys for (r, c) in d.pairs if r == race) for race in rs) and len(rs) < len(races):
            kind = "race-restricted"
        else:
            kind = "race+class-restricted"
        del all_r
        return {"pairs": len(keys), "races": sorted(rs), "classes": sorted(cs), "scope": kind}

    # SkillRaceClassInfo table census
    srci_rows = []
    for r in d.srci:
        sk = d.skill.get(i(r["SkillID"]))
        matched = [pair_key(ra, c) for ra, c in d.pairs if d.srci_matches(r, ra, c)]
        srci_rows.append({
            "id": i(r["ID"]), "skill": i(r["SkillID"]), "skill_name": sk["DisplayName_lang"] if sk else None,
            "category": i(sk["CategoryID"]) if sk else None, "availability": i(r["Availability"]),
            "class_mask": i(r["ClassMask"]), "race_masks": [i(r["RaceMasks_0"]), i(r["RaceMasks_1"])],
            "flags": i(r["Flags"]), "min_level": i(r["MinLevel"]), "skill_tier": i(r["SkillTierID"]),
            "range_type": d.range_type(r), "pairs_matched": len(matched)})

    # SkillLineAbility census
    sla_rows = []
    for a in d.sla:
        sid = i(a["Spell"])
        sk = d.skill.get(i(a["SkillLine"]))
        hits = res["sla_hits"].get(i(a["ID"]), set())
        sla_rows.append({
            "id": i(a["ID"]), "skill": i(a["SkillLine"]), "skillup_skill": i(a["SkillupSkillLineID"]),
            "category": i(sk["CategoryID"]) if sk else None, "spell": sid,
            "spell_name": d.spell_name.get(sid), "method": i(a["AcquireMethod"]),
            "class_mask": i(a["ClassMask"]), "race_masks": [i(a["RaceMasks_0"]), i(a["RaceMasks_1"])],
            "min_rank": i(a["MinSkillLineRank"]), "flags": i(a["Flags"]), "supercedes": i(a["SupercedesSpell"]),
            "spell_level": d.spell_level(sid), "tags": classify_spell(d, sid) if sid in d.spell_name else ["missing-spell"],
            "auto_pairs": len(hits), **({"auto_scope": scope(hits)} if hits else {})})

    auto = [s for s in sla_rows if s["auto_pairs"]]
    summary = {
        "level": level, "pairs": npairs, "races": len(races), "classes": len(classes),
        "srci_rows": len(d.srci), "srci_distinct_skills": len({r["skill"] for r in srci_rows}),
        "srci_rows_skill_missing_from_SkillLine": [r["id"] for r in srci_rows if r["skill_name"] is None],
        "srci_by_availability": Counter(r["availability"] for r in srci_rows),
        "srci_by_availability_category": Counter(f"{r['availability']}/{CATEGORY.get(r['category'], r['category'])}"
                                                 for r in srci_rows),
        "srci_class_masks": Counter(r["class_mask"] for r in srci_rows),
        "srci_race_mask_kinds": Counter("all" if r["race_masks"] in ([-1, -1], [0, 0]) else "restricted"
                                        for r in srci_rows),
        "srci_min_levels": Counter(r["min_level"] for r in srci_rows),
        "srci_flags": Counter(r["flags"] for r in srci_rows),
        "srci_range_types_availability1": Counter(r["range_type"] for r in srci_rows if r["availability"] == 1),
        "sla_rows": len(sla_rows), "sla_distinct_spells": len({s["spell"] for s in sla_rows}),
        "sla_by_method": Counter(METHOD.get(s["method"], s["method"]) for s in sla_rows),
        "sla_by_method_category": Counter(f"{METHOD.get(s['method'], s['method'])}/{CATEGORY.get(s['category'], s['category'])}"
                                          for s in sla_rows),
        "sla_with_skillup_redirect": sum(1 for s in sla_rows if s["skillup_skill"]),
        "sla_with_race_mask": sum(1 for s in sla_rows if s["race_masks"] != [0, 0]),
        "auto_sla_rows": len(auto), "auto_distinct_spells": len({s["spell"] for s in auto}),
        "auto_by_category": Counter(CATEGORY.get(s["category"], s["category"]) for s in auto),
        "auto_by_method": Counter(METHOD[s["method"]] for s in auto),
        "auto_by_scope": Counter(s["auto_scope"]["scope"] for s in auto),
        "auto_by_tag": Counter(t for s in auto for t in s["tags"]),
        "default_skills_per_pair": Counter(len(p["skills"]) for p in res["per_pair"].values()),
        "auto_spells_per_pair_minmax": [min(len(p["spells"]) for p in res["per_pair"].values()),
                                        max(len(p["spells"]) for p in res["per_pair"].values())],
        "gated_by_kind": Counter(g["kind"] for g in res["gated"]),
        "conditional_rows": len({c["sla"] for c in res["conditional"]}),
        "runtime_raw36_edges": len(res["runtime"]),
        "spec_delta_rows": len(res["spec_delta"]),
    }
    summary = json.loads(json.dumps(summary, default=lambda o: dict(sorted(o.items(), key=lambda kv: str(kv[0])))))

    prov = {
        "tool": "scripts/research/skill_acquisition.py",
        "command": f"python3 scripts/research/skill_acquisition.py --world <world-inputs.json> --out <dir> --level {level}",
        "wowlab_data_commit": git_head(ROOT), "trinitycore_commit": git_head(TRINITY),
        "db2_build": "12.1.0.69497",
        "db2_sha256": {t: sha(t) for t in ("SkillRaceClassInfo", "SkillLine", "SkillLineAbility", "ChrRaces",
                                           "ChrClasses", "SpellMisc", "SpellLevels", "SpellEffect",
                                           "SpellEquippedItems", "SpecializationSpells", "ChrSpecialization",
                                           "SpellLearnSpell")},
        "world": world_prov,
        "status": "static replay of Trinity consumers; not an execution; not Core authority",
    }
    by_pair_compact = {k: {"race": v["race"], "class": v["class"],
                           "skills": {s: [x["value"], x["max"], x["range"], x["via"]] for s, x in v["skills"].items()},
                           "spells": {s: x["via"] for s, x in v["spells"].items()},
                           "runtime_raw36": v["runtime_raw36"], "none_range": v["none_range"]}
                       for k, v in res["per_pair"].items()}
    return {
        "summary.json": {"provenance": prov, "summary": summary},
        "srci-rows.json": {"provenance": prov, "rows": srci_rows},
        "sla-rows.json": {"provenance": prov,
                          "note": "rows with AcquireMethod 1, 2 or 4 only (the automatic methods); counts for every row are in summary.json",
                          "rows": [s for s in sla_rows if s["method"] in (1, 2, 4)]},
        "pairs.json": {"provenance": prov, "pairs": by_pair_compact},
        "gates.json": {"provenance": prov, "gated": res["gated"], "conditional": res["conditional"],
                       "runtime_raw36": [{"source": s, "spell": t, "pairs": n} for (s, t), n in
                                         sorted(res["runtime"].items())],
                       "spec_delta": res["spec_delta"]},
    }


def dumps(obj: dict) -> str:
    """One JSON value per line for the large lists, so diffs stay row-sized."""
    parts = []
    for k in sorted(obj):
        v = obj[k]
        if isinstance(v, list):
            body = ",\n".join("  " + json.dumps(x, sort_keys=True, ensure_ascii=False) for x in v)
            parts.append(f" {json.dumps(k)}: [\n{body}\n ]" if v else f" {json.dumps(k)}: []")
        else:
            parts.append(f" {json.dumps(k)}: " + json.dumps(v, indent=1, sort_keys=True, ensure_ascii=False).replace("\n", "\n "))
    return "{\n" + ",\n".join(parts) + "\n}\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--world", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--level", type=int, default=90)
    ap.add_argument("--check", action="store_true", help="fail if --out differs from a fresh build")
    args = ap.parse_args()
    world = json.loads(args.world.read_text())
    d = Data(world)
    outs = build(d, args.level, {k: world["provenance"].get(k) for k in
                                 ("tdb_release", "base_world_database_sha256", "trinitycore_commit",
                                  "update_files_total")})
    args.out.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, obj in outs.items():
        text = dumps(obj)
        path = args.out / name
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(name)
        else:
            path.write_text(text)
    if args.check and stale:
        raise SystemExit(f"stale: {stale}")
    print(json.dumps(outs["summary.json"]["summary"], indent=1))


if __name__ == "__main__":
    main()
