"""Server-authored preparation inputs: what a Trinity Player needs beyond DB2.

Every input that is *not* client data (DB2 / GameTable) yet touches the
initial Player state is listed here with its loader, its consumer, whether the
pinned TDB (plus ``sql/updates/world/master``) supplies it, and what would
recover it if the world database were unavailable.  Rows are counted by the
generic extractor; nothing is interpreted, nothing is invented.

Subcommands:

    character_prep.py server-inputs [--class ID --race ID --level N] [--out FILE] [--tdb PATH]
    character_prep.py base-stat-gap [--out FILE] [--tdb PATH] [--probe]

``server-inputs`` without an identity prints the registry; with an identity it
prints which inputs that identity needs and which the pinned corpus satisfies.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import (CORPORA, ROOT, SNAPSHOT_BUILD, SourceError, TABLES, TDB_RELEASE,
               TRINITY_COMMIT, WORLD_DB_CORPORA)
from .basestats_tdb import (FILL_RULE_COORDINATES, FILL_RULE_TRINITY,
                            TRINITY_DEFAULT_MAX_PLAYER_LEVEL, TdbBaseStats, load)
from charstats.identity import CLASS_NAMES, MAX_STATS, STAT_NAMES

RESEARCH = Path(__file__).resolve().parents[1]
WORKSPACE_PARENT = ROOT.parent
TC_ROOT = WORKSPACE_PARENT / "TrinityCore"
DEFAULT_TDB = WORKSPACE_PARENT.parent / "bag" / "tdb" / TDB_RELEASE
CONF_DIST = TC_ROOT / "src/server/worldserver/worldserver.conf.dist"
EXTRACTOR = RESEARCH / "tools" / "tdb_world_extract.py"
PROBE = RESEARCH / "tools" / "tc_prep_probe" / "probe"


def wowlab_data_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def provenance(generator: str, world_database: bool = True) -> dict[str, Any]:
    out = {
        "snapshot_build": SNAPSHOT_BUILD,
        "trinitycore_commit": TRINITY_COMMIT,
        "generator": generator,
        "wowlab_data_commit": wowlab_data_commit(),
    }
    if world_database:
        out["world_database"] = TDB_RELEASE
    return out


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

PHASES = ("prepared", "identity-gate", "creation-only", "acquisition", "runtime",
          "irrelevant", "reference-other-track")


@dataclass
class ServerInput:
    id: str
    kind: str                       # world-db-table | worldserver.conf | session-state
    phase: str                      # one of PHASES
    loader: str                     # file:line at TRINITY_COMMIT
    consumers: list[str]            # file:line at TRINITY_COMMIT
    touches: str                    # which prepared value(s) it reaches
    evidence_class: str             # for the value it supplies
    reconstruct_from: str | None    # stronger source / observable state, or None
    fallback: str                   # what happens without it
    notes: str = ""
    tdb: dict[str, Any] = field(default_factory=dict)
    conf: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def registry() -> list[ServerInput]:
    om = "src/server/game/Globals/ObjectMgr.cpp"
    pl = "src/server/game/Entities/Player/Player.cpp"
    ss = "src/server/game/Entities/Unit/StatSystem.cpp"
    sm = "src/server/game/Spells/SpellMgr.cpp"
    wc = "src/server/game/World/World.cpp"
    return [
        ServerInput(
            id="player_classlevelstats", kind="world-db-table", phase="prepared",
            loader=f"{om}:4287 (ObjectMgr::LoadPlayerInfo)",
            consumers=[f"{om}:4437-4450 (ObjectMgr::GetPlayerLevelInfo)",
                       f"{pl}:2332,2362-2366 (Player::InitStatsForLevel -> SetCreateStat/SetStat)",
                       f"{pl}:2373 (SetArmor(int32(createStats[AGILITY]*2), 0))"],
            touches="create stats (Strength, Agility, Stamina, Intellect, Spirit) per (class, level); "
                    "therefore base armor, base health-from-stamina, base AP/SP",
            evidence_class="world-db-fact",
            reconstruct_from="a Retail character-sheet observation of a naked character (no items, no auras) "
                             "of that class at that level: UnitData::Stats[] minus the race modifier; one "
                             "observation per (class, level) reached",
            fallback="caller-supplied (charstats.MissingBaseStats); Trinity itself fills level gaps from the "
                     "previous level (opt-in reproduction: basestats_tdb --fill-rule trinity)",
            notes="rows above CONFIG_MAX_PLAYER_LEVEL ignored (ObjectMgr.cpp:4309-4317); gap fill 4336-4356 "
                  "keyed on race-modified strength == 0; VerifiedBuild column not read by the loader"),
        ServerInput(
            id="player_racestats", kind="world-db-table", phase="prepared",
            loader=f"{om}:4263 (ObjectMgr::LoadPlayerInfo)",
            consumers=[f"{om}:4328 (levelInfo.stats[i] = row + raceStats.StatModifier[i])"],
            touches="flat per-race delta added to every class row at every level",
            evidence_class="world-db-fact",
            reconstruct_from="two naked Retail observations of the same class/level differing only in race",
            fallback="a race with no row: every (race, class) pair playercreateinfo created for it has a null "
                     "levelInfo -> ABORT() at ObjectMgr.cpp:4342-4346 (server does not start)",
            notes="int16 columns; 25 legacy races carry Classic-era +/-3 deltas, the 6 newest (52, 70, 84, 85, "
                  "86, 91) are authored as zeros -- internally inconsistent authoring, Retail truth unresolved"),
        ServerInput(
            id="playercreateinfo", kind="world-db-table", phase="identity-gate",
            loader=f"{om}:3813 (ObjectMgr::LoadPlayerInfo)",
            consumers=[f"{om}:4323 (only these (race, class) pairs receive levelInfo)",
                       f"{pl}:396-402 (Player::Create refuses an unknown pair)",
                       f"{om}:10517-10520 (ObjectMgr::GetPlayerInfo)"],
            touches="existence of the (race, class) identity; create/home position (non-combat)",
            evidence_class="world-db-fact",
            reconstruct_from="ChrClassRaceSex / ChrClasses + ChrRaces playable flags in DB2 (the pair set), "
                             "but Trinity trusts the table, not DB2",
            fallback="pair absent: no PlayerInfo -> creation refused; an existing character of that pair "
                     "would get GetPlayerLevelInfo() returning without writing info (all-zero stats)",
            notes="also requires a male and female ChrModel for the race (ObjectMgr.cpp:3861-3871)"),
        ServerInput(
            id="playercreateinfo_item / _spell_custom / _cast_spell / _action", kind="world-db-table",
            phase="creation-only",
            loader=f"{om}:3993, 4080, 4142, 4205",
            consumers=[f"{pl}:509 (info->action), {pl}:521-522 (info->item), Player::Create only"],
            touches="starting items / spells / action bars at Player::Create; nothing after creation",
            evidence_class="world-db-fact",
            reconstruct_from=None,
            fallback="irrelevant post-creation: a prepared character's spells and gear are inputs, not "
                     "recomputed from these tables",
            notes="playercreateinfo_spell_custom has 0 rows and playercreateinfo_item 1 row in the pinned TDB"),
        ServerInput(
            id="player_xp_for_level", kind="world-db-table", phase="irrelevant",
            loader=f"{om}:4370 (ObjectMgr::LoadPlayerInfo)",
            consumers=[f"{om}:7916-7921 (GetXPForLevel)", f"{pl}:2340 (ActivePlayerData::NextLevelXP)"],
            touches="NextLevelXP only (level identity, not combat)",
            evidence_class="world-db-fact",
            reconstruct_from="xp.txt GameTable (client) -- Trinity pre-fills from sXpGameTable at "
                             "ObjectMgr.cpp:4365-4367 and the table only overrides",
            fallback="0 rows in the pinned TDB: the GameTable values are used unchanged",
            notes=""),
        ServerInput(
            id="race_unlock_requirement / class_expansion_requirement", kind="world-db-table",
            phase="creation-only",
            loader=f"{om}:10522, 10528, 10573 (LoadRaceAndClassExpansionRequirements)",
            consumers=["src/server/game/Handlers/CharacterHandler.cpp:700-760 (character creation checks)"],
            touches="whether an account may create the (race, class); nothing after creation",
            evidence_class="world-db-fact", reconstruct_from=None,
            fallback="irrelevant post-creation", notes=""),
        ServerInput(
            id="spell_learn_spell", kind="world-db-table", phase="acquisition",
            loader=f"{sm}:1009 (SpellMgr::LoadSpellLearnSpells)",
            consumers=[f"{pl} Player::AddSpell/LearnSpell chains"],
            touches="which spells a character owns (acquisition), hence CanUseMastery and passive auras",
            evidence_class="world-db-fact",
            reconstruct_from="already in dummy-corpora/trinity-server-overlay.json (Track: dummy_semantics)",
            fallback="reference: charstats.acquisition + dummy_semantics overlay", notes="5 rows"),
        ServerInput(
            id="spell_proc / spell_custom_attr / spell_script_names / spell_pet_auras / serverside_spell_effect / "
               "spell_linked_spell / spell_area / spell_group(+stack_rules) / spell_required / spell_threat / "
               "spell_enchant_proc_data",
            kind="world-db-table", phase="reference-other-track",
            loader=f"{sm}:1506, 2986; {om}:5971; {sm}:1972, 2752, 2081, 2307, 1274, 1355; {sm}:1920, 2038",
            consumers=["spell/aura/proc semantics (procs/, dummy_semantics/ passes)"],
            touches="runtime spell semantics, not prepared stat values",
            evidence_class="world-db-fact",
            reconstruct_from="already extracted by earlier passes (proc overlay, server overlay)",
            fallback="reference only", notes=""),
        ServerInput(
            id="item_template_addon / item_random_bonus_list_template", kind="world-db-table",
            phase="irrelevant",
            loader=f"{om}:3381 (LoadItemTemplateAddon); src/server/game/Entities/Item/ItemEnchantmentMgr.cpp:48",
            consumers=["Item::GenerateItemRandomBonusListId (random bonus lists at item creation)"],
            touches="server-side random bonus lists for newly created items; nothing for a supplied loadout",
            evidence_class="world-db-fact",
            reconstruct_from="the fixture's explicit bonus list ids (gearing-pipeline-archaeology.md)",
            fallback="RandomBonusListTemplateId is 0 on all 625 item_template_addon rows and "
                     "item_random_bonus_list_template has 0 rows: nothing to reconstruct",
            notes=""),
        ServerInput(
            id="skill_tiers / skill_fishing_base_level / skill_* ", kind="world-db-table", phase="irrelevant",
            loader=f"{om}:8936, 8900",
            consumers=["profession skill values"],
            touches="profession/skill max values; no prepared combat stat",
            evidence_class="world-db-fact", reconstruct_from=None,
            fallback="irrelevant to preparation", notes="weapon skill is legacy-only (Track B/C)"),
        ServerInput(
            id="creature_template / creature_template_spell / pet_levelstats", kind="world-db-table", phase="reference-other-track",
            loader=f"{om}:347, 370 (LoadCreatureTemplates, WORLD_SEL_CREATURE_TEMPLATE), 3686 (LoadPetLevelInfo)",
            consumers=["Guardian::InitStatsForLevel (Track A/B)"],
            touches="controlled-unit preparation, not the Player", evidence_class="world-db-fact",
            reconstruct_from="world-db-corpora/player-base-stats.json (pet_levelstats) + creature-templates.json",
            fallback="reference only", notes=""),
        ServerInput(
            id="spell_totem_model / player_factionchange_* / pet_name_generation / battle_pet_* / game_tele / "
               "exploration_basexp", kind="world-db-table", phase="irrelevant",
            loader="ObjectMgr / SpellMgr loaders", consumers=["cosmetic, faction change, battle pets, GM teleports"],
            touches="nothing in prepared combat state", evidence_class="world-db-fact",
            reconstruct_from=None, fallback="irrelevant", notes=""),
        # -- worldserver.conf -------------------------------------------------
        ServerInput(
            id="MaxPlayerLevel", kind="worldserver.conf", phase="prepared",
            loader=f"{wc}:750 (default GetMaxLevelForExpansion(CURRENT_EXPANSION) = 90, Min 1, Max MAX_LEVEL 123)",
            consumers=[f"{om}:4309-4317 (rows above are ignored)", f"{om}:4324 (levelInfo array size)",
                       f"{om}:4349 (fill loop bound)", f"{om}:4446-4449 (BuildPlayerLevelInfo above it)",
                       f"{om}:4424-4425 (GetPlayerClassLevelInfo clamps base mana level)",
                       f"{pl}:2333-2338 (ActivePlayerData::MaxLevel)"],
            touches="which levels have base-stat cells, and how levels above the last row are served",
            evidence_class="trinity-consumer",
            reconstruct_from="worldserver.conf.dist (value below); a deployed server may differ",
            fallback="default 90", notes="a server configured with MaxPlayerLevel=80 would serve 81-90 through "
                                         "BuildPlayerLevelInfo's Classic switch instead of the copy"),
        ServerInput(
            id="Expansion", kind="worldserver.conf", phase="identity-gate",
            loader=f"{wc}:795", consumers=[f"{pl}:2334-2339 (MaxLevel by session expansion)"],
            touches="ActivePlayerData::MaxLevel only", evidence_class="trinity-consumer",
            reconstruct_from="worldserver.conf.dist", fallback="CURRENT_EXPANSION (11 = Midnight)", notes=""),
        ServerInput(
            id="StartPlayerLevel / StartDeathKnightPlayerLevel / StartDemonHunterPlayerLevel / StartEvokerPlayerLevel",
            kind="worldserver.conf", phase="creation-only",
            loader=f"{wc}:752-755", consumers=["Player::Create (start level)"],
            touches="which levels are reachable from creation; explains why Evoker rows 2-9 are absent",
            evidence_class="trinity-consumer", reconstruct_from="worldserver.conf.dist",
            fallback="1 / 8 / 8 / 10", notes=""),
        ServerInput(
            id="Rate.Health / Rate.Mana / Rate.Rage.* / Rate.Focus / Rate.Energy / Rate.* (power rates)",
            kind="worldserver.conf", phase="runtime",
            loader=f"{wc}:941-942 (RATE_HEALTH, RATE_POWER_MANA) and following",
            consumers=[f"{pl}:1748 (Player::RegenerateHealth only)", f"{ss}:809-835 (Player::UpdatePowerRegen, PowerRegenInfo)"],
            touches="regeneration per tick; NOT max health / max power / any prepared value",
            evidence_class="trinity-consumer",
            reconstruct_from="worldserver.conf.dist", fallback="1.0",
            notes="grep of getRate(RATE_HEALTH|RATE_POWER_*) in Player.cpp/StatSystem.cpp: no multiplier in "
                  "UpdateMaxHealth/UpdateMaxPower/InitStatsForLevel"),
        ServerInput(
            id="Stats.Limits.Enable / Stats.Limits.Dodge / Parry / Block / Crit",
            kind="worldserver.conf", phase="prepared",
            loader=f"{wc} (CONFIG_STATS_LIMITS_ENABLE, CONFIG_STATS_LIMITS_*)",
            consumers=[f"{ss}:495-496 (UpdateBlockPercentage)", f"{ss}:505-507 (UpdateCritPercentage)",
                       f"{ss}:665-666 (UpdateParryPercentage)", f"{ss}:703-704 (UpdateDodgePercentage)"],
            touches="caps the prepared crit/dodge/parry/block percentages when enabled",
            evidence_class="trinity-consumer", reconstruct_from="worldserver.conf.dist",
            fallback="disabled (0) -> no cap", notes="a configuration that clamps prepared values; off by default"),
        ServerInput(
            id="session expansion (account state)", kind="session-state", phase="identity-gate",
            loader="WorldSession::GetExpansion()", consumers=[f"{pl}:2334-2339"],
            touches="ActivePlayerData::MaxLevel; no stat", evidence_class="trinity-consumer",
            reconstruct_from=None, fallback="caller-supplied if the field matters", notes=""),
    ]


# ---------------------------------------------------------------------------
# worldserver.conf.dist values
# ---------------------------------------------------------------------------

CONF_KEYS = ("MaxPlayerLevel", "Expansion", "StartPlayerLevel", "StartDeathKnightPlayerLevel",
             "StartDemonHunterPlayerLevel", "StartEvokerPlayerLevel", "Rate.Health", "Rate.Mana",
             "Stats.Limits.Enable", "Stats.Limits.Dodge", "Stats.Limits.Parry", "Stats.Limits.Block",
             "Stats.Limits.Crit", "CharacterCreating.Disabled.RaceMask", "CharacterCreating.Disabled.ClassMask")


def conf_dist_values(path: Path = CONF_DIST) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {k: {"value": None, "line": None, "status": "worldserver.conf.dist not found"} for k in CONF_KEYS}
    out: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.]+)\s*=\s*(\S+)")
    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        m = pattern.match(line)
        if m and m.group(1) in CONF_KEYS and m.group(1) not in out:
            out[m.group(1)] = {"value": m.group(2), "line": number,
                               "coordinate": f"src/server/worldserver/worldserver.conf.dist:{number}"}
    for key in CONF_KEYS:
        out.setdefault(key, {"value": None, "line": None, "status": "not found"})
    return out


# ---------------------------------------------------------------------------
# TDB row counts / pairs via the generic extractor
# ---------------------------------------------------------------------------

COUNT_TABLES = {
    # table: projection column (keeps the intermediate file small)
    "playercreateinfo": "race,class", "playercreateinfo_item": "race", "playercreateinfo_spell_custom": "racemask",
    "playercreateinfo_cast_spell": "raceMask", "playercreateinfo_action": "race", "player_xp_for_level": "Level",
    "race_unlock_requirement": "raceID,expansion", "class_expansion_requirement": "ClassID,RaceID",
    "spell_learn_spell": "entry", "spell_proc": "SpellId", "spell_custom_attr": "entry",
    "spell_script_names": "spell_id", "spell_pet_auras": "spell", "serverside_spell_effect": "SpellID",
    "spell_linked_spell": "spell_trigger", "spell_area": "spell", "spell_group": "id",
    "spell_group_stack_rules": "group_id", "spell_required": "spell_id", "spell_threat": "entry",
    "spell_enchant_proc_data": "EnchantID", "item_template_addon": "Id,RandomBonusListTemplateId",
    "item_random_bonus_list_template": "Id", "skill_tiers": "ID", "skill_fishing_base_level": "entry",
    "spell_totem_model": "SpellID", "exploration_basexp": "level", "player_factionchange_spells": "alliance_id",
    "creature_template_spell": "CreatureID",
}


def extract_counts(tdb: Path, tables: dict[str, str] = COUNT_TABLES) -> dict[str, Any]:
    """Run tools/tdb_world_extract.py; return its provenance + projected rows."""
    if not tdb.exists():
        raise SourceError(f"TDB dump not found: {tdb}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "counts.json"
        cmd = [sys.executable, str(EXTRACTOR), "--tdb", str(tdb), "--tables", ",".join(tables),
               "--out", str(out)]
        for table, cols in tables.items():
            cmd += ["--project", f"{table}={cols}"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SourceError(f"extractor failed: {proc.stderr.strip()[-500:]}")
        return json.loads(out.read_text(encoding="utf-8"))


def pairs_from_extract(extract: dict[str, Any]) -> list[list[int]]:
    t = extract["tables"]["playercreateinfo"]
    cols = t["columns"]
    ri, ci = cols.index("race"), cols.index("class")
    return sorted({(int(r[ri]), int(r[ci])) for r in t["rows"]})


# ---------------------------------------------------------------------------
# ChrRaces / ChrClasses from the snapshot
# ---------------------------------------------------------------------------

CHRRACES_FLAG_NPC_ONLY = 0x1   # DBCEnums.h:349 ChrRacesFlag::NPCOnly


def chr_races() -> dict[int, dict[str, Any]]:
    out = {}
    with (TABLES / "ChrRaces.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            race_id = int(row["ID"])
            flags = int(row["Flags"])
            out[race_id] = {
                "name": row["Name_lang"], "flags": flags,
                "playable_race_bit": int(row["PlayableRaceBit"]),
                "npc_only": bool(flags & CHRRACES_FLAG_NPC_ONLY),
                "alliance": int(row["Alliance"]),
            }
    return out


def chr_classes() -> dict[int, str]:
    with (TABLES / "ChrClasses.csv").open(encoding="utf-8", newline="") as handle:
        return {int(r["ID"]): r["Name_lang"] for r in csv.DictReader(handle)}


# ---------------------------------------------------------------------------
# identity query
# ---------------------------------------------------------------------------

def identity_needs(corpus: TdbBaseStats, class_id: int, race_id: int, level: int,
                   pairs: set[tuple[int, int]] | None,
                   max_player_level: int = TRINITY_DEFAULT_MAX_PLAYER_LEVEL) -> dict[str, Any]:
    races = chr_races()
    race = races.get(race_id)
    needs: list[dict[str, Any]] = []

    levels = corpus.class_level.get(class_id, {})
    if level in levels:
        status, evidence = "satisfied", "world-db-fact"
        detail = f"row (class {class_id}, level {level}) present"
    elif not levels:
        status, evidence = "unsatisfied", "unresolved"
        detail = f"class {class_id} has no player_classlevelstats rows (Trinity: ABORT at load if any playercreateinfo pair exists)"
    elif level > max_player_level:
        status, evidence = "unsatisfied", "unresolved"
        detail = (f"level {level} > MaxPlayerLevel {max_player_level}: Trinity would call "
                  f"BuildPlayerLevelInfo ({FILL_RULE_COORDINATES['build_player_level_info']}), a Classic-era "
                  "extrapolation; not reproduced as truth")
    else:
        below = [l for l in levels if l < level]
        if 1 not in levels:
            status, evidence = "unsatisfied", "unresolved"
            detail = "level 1 row missing: Trinity ABORT()s"
        else:
            src = max(below)
            status, evidence = "fill-rule", "trinity-consumer(fill-rule)"
            detail = (f"no row for level {level}; Trinity copies level {src} forward through "
                      f"{src + 1}..{level} ({FILL_RULE_COORDINATES['fill_gaps']}); Retail truth unresolved; "
                      "opt in with --fill-rule trinity")
    needs.append({"input": "player_classlevelstats", "status": status, "evidence_class": evidence, "detail": detail})

    if race_id in corpus.race_mods:
        needs.append({"input": "player_racestats", "status": "satisfied", "evidence_class": "world-db-fact",
                      "detail": {STAT_NAMES[i]: corpus.race_mods[race_id][i] for i in range(MAX_STATS)}})
    else:
        needs.append({"input": "player_racestats", "status": "unsatisfied", "evidence_class": "unresolved",
                      "detail": f"race {race_id} has no player_racestats row (Trinity: ABORT at load if the "
                                "race has any playercreateinfo pair; otherwise the pair does not exist)"})

    if pairs is None:
        needs.append({"input": "playercreateinfo", "status": "unknown", "evidence_class": "unresolved",
                      "detail": "playercreateinfo pairs not extracted (run base-stat-gap --tdb)"})
    elif (race_id, class_id) in pairs:
        needs.append({"input": "playercreateinfo", "status": "satisfied", "evidence_class": "world-db-fact",
                      "detail": f"pair (race {race_id}, class {class_id}) present"})
    else:
        needs.append({"input": "playercreateinfo", "status": "unsatisfied", "evidence_class": "world-db-fact",
                      "detail": f"pair (race {race_id}, class {class_id}) absent: Player::Create refuses it "
                                "(Player.cpp:396-402); no levelInfo is built for it"})

    if race is None:
        needs.append({"input": "ChrRaces (client)", "status": "unsatisfied", "evidence_class": "db2-fact",
                      "detail": f"race {race_id} not in ChrRaces.csv"})
    else:
        needs.append({"input": "ChrRaces (client)", "status": "satisfied" if not race["npc_only"] and race["playable_race_bit"] >= 0 else "unsatisfied",
                      "evidence_class": "db2-fact",
                      "detail": {"name": race["name"], "playable_race_bit": race["playable_race_bit"],
                                 "npc_only": race["npc_only"],
                                 "consumer": "CharacterHandler.cpp:743 (ChrRacesFlag::NPCOnly refuses creation)"}})

    needs.append({"input": "MaxPlayerLevel", "status": "satisfied" if level <= max_player_level else "unsatisfied",
                  "evidence_class": "trinity-consumer",
                  "detail": f"level {level} vs MaxPlayerLevel {max_player_level} (worldserver.conf.dist:912)"})
    return {"identity": {"class_id": class_id, "class": CLASS_NAMES.get(class_id), "race_id": race_id,
                         "race": race["name"] if race else None, "level": level},
            "needs": needs,
            "unsatisfied": [n["input"] for n in needs if n["status"] != "satisfied"]}


# ---------------------------------------------------------------------------
# corpora
# ---------------------------------------------------------------------------

def id_tables(item_id: str, known: dict[str, Any]) -> list[str]:
    """Expand a registry id to the table names it abbreviates.

    ``a_b / _c`` -> ``a_b``, ``a_c`` (suffix joined to the first token's first word);
    ``x(+y)`` -> ``x``, ``x_y``; ``p_*`` -> every known table with prefix ``p_``.
    """
    tokens = [t for t in re.split(r"\s*/\s*|\s+", item_id) if t]
    head = tokens[0].split("_")[0] if tokens else ""
    out: list[str] = []
    for token in tokens:
        extra = re.match(r"^([a-z_]+)\(\+([a-z_]+)\)$", token)
        names = [extra.group(1), f"{extra.group(1)}_{extra.group(2)}"] if extra else [token.strip("()")]
        for name in names:
            if name.startswith("_"):
                name = head + name
            if name.endswith("*"):
                out.extend(sorted(k for k in known if k.startswith(name[:-1])))
            elif name in known:
                out.append(name)
    return list(dict.fromkeys(out))


def build_server_inputs(tdb: Path | None) -> dict[str, Any]:
    corpus = load()
    conf = conf_dist_values()
    extract = extract_counts(tdb) if tdb is not None else None
    counts: dict[str, Any] = {}
    if extract is not None:
        counts = {k: v for k, v in sorted(extract["provenance"]["final_row_counts"].items())}
    counts.update({k: v for k, v in corpus.provenance.get("final_row_counts", {}).items()})
    inputs = registry()
    for item in inputs:
        for table in id_tables(item.id, counts):
            item.tdb[table] = {"rows": counts[table], "source": "pinned TDB + sql/updates/world/master"}
        if item.kind == "worldserver.conf":
            for key in CONF_KEYS:
                if key.split(".")[0] in item.id or key in item.id:
                    item.conf[key] = conf[key]
    out: dict[str, Any] = {
        "provenance": provenance("python3 character_prep.py server-inputs --tdb <TDB> --out "
                                 "../../docs/research/character-prep-corpora/server-inputs.json"),
        "invariants": ["absent from pinned Trinity != absent from Retail", "unsupported by research != inert",
                       "structural similarity != semantic equivalence",
                       "Trinity is the best available consumer oracle, not unquestionable Retail truth"],
        "phases": list(PHASES),
        "inputs": [i.to_dict() for i in inputs],
        "worldserver_conf_dist": conf,
        "tdb_row_counts": counts,
    }
    if extract is not None:
        p = extract["provenance"]
        out["tdb_extraction"] = {
            "base_world_database": p["base_world_database"],
            "base_world_database_sha256": p["base_world_database_sha256"],
            "update_files_total": p["update_files_total"],
            "update_files_touching_tracked_tables": p["update_files_touching_tracked_tables"],
            "unparsed_statement_count": p["unparsed_statement_count"],
            "tables_missing_from_dump": p["tables_missing_from_dump"],
        }
        addon = extract["tables"].get("item_template_addon")
        if addon:
            idx = addon["columns"].index("RandomBonusListTemplateId")
            out["item_template_addon_random_bonus_nonzero"] = sum(1 for r in addon["rows"] if r[idx])
        out["playercreateinfo_pairs"] = {"count": len(pairs_from_extract(extract)),
                                         "pairs": [list(p) for p in pairs_from_extract(extract)]}
    else:
        out["tdb_extraction"] = {"status": "not-extracted (no --tdb); row counts limited to player-base-stats.json"}
    out["conf_multiplies_prepared_values"] = {
        "verdict": "none of Rate.* multiplies a prepared maximum; Stats.Limits.* (disabled by default) caps four "
                   "derived percentages; MaxPlayerLevel shapes the base-stat array",
        "evidence_class": "trinity-consumer",
        "coordinates": ["src/server/game/Entities/Player/Player.cpp:1748", "src/server/game/Entities/Unit/StatSystem.cpp:809-835",
                        "src/server/game/Entities/Unit/StatSystem.cpp:495-507,665-666,703-704"],
    }
    return out


def probe_fill_rule(corpus: TdbBaseStats, class_id: int, race_id: int, level: int,
                    max_player_level: int) -> dict[str, Any] | None:
    """Run the extracted LoadPlayerInfo gap-fill in C++ for one (race, class)."""
    if not PROBE.exists():
        return None
    mods = corpus.race_mods.get(race_id, (0,) * MAX_STATS)
    rows = [(lvl, [v + mods[i] for i, v in enumerate(stats)])
            for lvl, stats in sorted(corpus.class_level[class_id].items())]
    req = f"fillgaps {max_player_level} {level} {len(rows)} " + " ".join(
        f"{lvl} " + " ".join(str(v) for v in stats) for lvl, stats in rows)
    proc = subprocess.run([str(PROBE)], input=req + "\n", capture_output=True, text=True, timeout=30)
    reply = proc.stdout.strip().split()
    if reply and reply[0] == "ABORT":
        return {"abort": True}
    return {"tc_log_error_count": int(reply[0]),
            "stats": {STAT_NAMES[i]: int(reply[1 + i]) for i in range(MAX_STATS)},
            "evidence_class": "trinity-probe"}


def build_base_stat_gap(tdb: Path | None, use_probe: bool) -> dict[str, Any]:
    corpus = load()
    max_level = TRINITY_DEFAULT_MAX_PLAYER_LEVEL
    extract = extract_counts(tdb, {"playercreateinfo": "race,class", "race_unlock_requirement": "raceID,expansion",
                                   "class_expansion_requirement": "ClassID,RaceID"}) if tdb else None
    pairs_list = pairs_from_extract(extract) if extract else None
    pairs = {tuple(p) for p in pairs_list} if pairs_list else None
    races = chr_races()
    classes = chr_classes()
    coverage = corpus.coverage(max_level)
    table = corpus.table(FILL_RULE_TRINITY, max_level, pairs=pairs) if pairs else None

    playable = sorted(r for r, info in races.items() if info["playable_race_bit"] >= 0 and not info["npc_only"])
    npc_flagged_with_bit = sorted(r for r, info in races.items() if info["playable_race_bit"] >= 0 and info["npc_only"])
    race_rows = set(corpus.races())
    race_coverage = {
        "chrraces_total": len(races),
        "playable_by_db2": playable,
        "playable_race_bit_but_npc_only": npc_flagged_with_bit,
        "player_racestats_rows": sorted(race_rows),
        "playable_without_row": sorted(r for r in playable if r not in race_rows),
        "rows_without_playable_flag": sorted(r for r in race_rows if r not in playable),
        "trinity_missing_row_behaviour": "ABORT() at ObjectMgr.cpp:4342-4346 if the race has any playercreateinfo pair; "
                                         "otherwise the race simply has no PlayerInfo (Player::Create refuses)",
        "race_modifier_profile": {
            str(r): {"nonzero": any(corpus.race_mods[r]), "mods": {STAT_NAMES[i]: corpus.race_mods[r][i] for i in range(MAX_STATS)},
                     "name": races.get(r, {}).get("name")} for r in corpus.races()},
    }
    if pairs:
        pair_races = sorted({r for r, _ in pairs})
        race_coverage["playercreateinfo_races"] = pair_races
        race_coverage["playercreateinfo_races_without_racestats_row"] = sorted(r for r in pair_races if r not in race_rows)
        race_coverage["racestats_rows_without_playercreateinfo"] = sorted(r for r in race_rows if r not in pair_races)
        race_coverage["playercreateinfo_races_not_playable_by_db2"] = sorted(r for r in pair_races if r not in playable)

    class_coverage = {}
    for class_id in corpus.classes():
        c = coverage["per_class"][str(class_id)]
        c = dict(c)
        c["name"] = classes.get(class_id)
        if pairs:
            c["playercreateinfo_races"] = sorted(r for r, k in pairs if k == class_id)
        c["filled_cells_trinity_rule"] = sorted(l for (k, l) in (table.filled if table else {}) if k == class_id)
        class_coverage[str(class_id)] = c
    classes_without_rows = sorted(k for k in classes if k not in corpus.class_level)

    level90 = {}
    for class_id in corpus.classes():
        row80 = corpus.class_level[class_id].get(80)
        entry: dict[str, Any] = {
            "name": classes.get(class_id),
            "level_80_row": {STAT_NAMES[i]: row80[i] for i in range(MAX_STATS)} if row80 else None,
            "level_90_trinity": ({STAT_NAMES[i]: row80[i] for i in range(MAX_STATS)} if row80 else None),
            "evidence_class": "trinity-consumer(fill-rule)",
            "retail_truth": "unresolved",
            "arithmetic": "levelInfo[89] = levelInfo[88] = ... = levelInfo[79] (copy, no extrapolation); "
                          "10 TC_LOG_ERROR lines per (race, class)",
        }
        if use_probe and pairs:
            race_id = next((r for r, k in sorted(pairs) if k == class_id), None)
            if race_id is not None:
                entry["probe"] = probe_fill_rule(corpus, class_id, race_id, 90, max_level)
                entry["probe_race_id"] = race_id
                if entry["probe"] and not entry["probe"].get("abort"):
                    mods = corpus.race_mods.get(race_id, (0,) * MAX_STATS)
                    expected = {STAT_NAMES[i]: row80[i] + mods[i] for i in range(MAX_STATS)}
                    entry["probe_matches_adapter"] = entry["probe"]["stats"] == expected
        level90[str(class_id)] = entry

    out = {
        "provenance": provenance("python3 character_prep.py base-stat-gap --tdb <TDB> --probe --out "
                                 "../../docs/research/character-prep-corpora/base-stat-gap.json"),
        "corpus": {"path": str(corpus.path.relative_to(ROOT)), "provenance": corpus.provenance,
                   "schemas": corpus.schemas},
        "trinity_fill_rule": {
            "coordinates": FILL_RULE_COORDINATES,
            "config_max_player_level": max_level,
            "rule": ["rows with level > MaxPlayerLevel are ignored at load (TC_LOG_INFO)",
                     "levelInfo = array of MaxPlayerLevel zero-initialised PlayerLevelInfo per (race, class) from playercreateinfo",
                     "levelInfo[level-1].stats[i] = row.stat[i] + player_racestats[race].StatModifier[i]",
                     "level 1 missing (levelInfo null or stats[0] == 0) -> ABORT()",
                     "for index 1..MaxPlayerLevel-1: if stats[0] (race-modified Strength) == 0: copy index-1, TC_LOG_ERROR",
                     "GetPlayerLevelInfo(level <= MaxPlayerLevel) reads levelInfo[level-1]; only level > MaxPlayerLevel reaches BuildPlayerLevelInfo"],
            "sentinel": "stats[0] == 0 on the race-modified Strength; a row is not 'present' to Trinity if its "
                        "race-modified Strength is 0",
            "evidence_class": "trinity-consumer",
        },
        "class_coverage": class_coverage,
        "classes_in_chrclasses_without_rows": classes_without_rows,
        "verified_build_values": sorted({v for v in corpus.verified_build.values()}),
        "row_count": coverage["row_count"],
        "race_coverage": race_coverage,
        "playercreateinfo_pairs": ({"count": len(pairs_list), "pairs": [list(p) for p in pairs_list],
                                    "source": "pinned TDB + sql/updates/world/master (2026_03_07_00_world.sql added Haranir 86/91)"}
                                   if pairs_list else {"status": "not-extracted (no --tdb)"}),
        "zero_strength_crossings_all_races": corpus.zero_strength_crossings(max_level),
        "zero_strength_crossings_created_pairs": corpus.zero_strength_crossings(max_level, pairs) if pairs else None,
        "monotonicity_anomalies": corpus.monotonicity(),
        "filled_cells_total": len(table.filled) if table else None,
        "level_90_verdict": level90,
    }
    if extract:
        t = extract["tables"]["race_unlock_requirement"]
        out["race_unlock_requirement"] = sorted({int(r[0]) for r in t["rows"]})
        t = extract["tables"]["class_expansion_requirement"]
        out["class_expansion_requirement"] = {"rows": t["row_count_total"],
                                              "pairs": len({(int(r[1]), int(r[0])) for r in t["rows"]})}
    return out


def write_corpus(path: Path, payload: dict[str, Any]) -> str:
    text = json.dumps(payload, indent=1, sort_keys=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def register(subparsers: Any) -> None:
    p = subparsers.add_parser("server-inputs",
                              help="server-authored preparation inputs; identity query or corpus generation")
    p.add_argument("--class", dest="class_id", type=int)
    p.add_argument("--race", dest="race_id", type=int)
    p.add_argument("--level", type=int)
    p.add_argument("--tdb", type=Path, default=None, help="TDB dump; enables row counts / pairs extraction")
    p.add_argument("--out", type=Path, default=None, help="write server-inputs.json")
    p.set_defaults(func=run_server_inputs)

    g = subparsers.add_parser("base-stat-gap", help="class x level / race coverage of the TDB base stats")
    g.add_argument("--tdb", type=Path, default=None)
    g.add_argument("--probe", action="store_true", help="cross-check the fill rule with tools/tc_prep_probe")
    g.add_argument("--out", type=Path, default=None)
    g.set_defaults(func=run_base_stat_gap)


def _pairs_for_query(tdb: Path | None) -> set[tuple[int, int]] | None:
    if tdb is not None:
        return {tuple(p) for p in pairs_from_extract(extract_counts(tdb, {"playercreateinfo": "race,class"}))}
    for name in ("base-stat-gap.json", "server-inputs.json"):
        path = CORPORA / name
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            block = raw.get("playercreateinfo_pairs", {})
            if "pairs" in block:
                return {(int(r), int(c)) for r, c in block["pairs"]}
    return None


def run_server_inputs(args: argparse.Namespace) -> int:
    if args.class_id is not None or args.race_id is not None or args.level is not None:
        if None in (args.class_id, args.race_id, args.level):
            raise SystemExit("--class, --race and --level are all required for an identity query")
        corpus = load()
        result = identity_needs(corpus, args.class_id, args.race_id, args.level, _pairs_for_query(args.tdb))
        print(json.dumps(result, indent=1))
        return 0 if not result["unsatisfied"] else 1
    payload = build_server_inputs(args.tdb)
    if args.out is not None:
        digest = write_corpus(args.out, payload)
        print(f"wrote {args.out} sha256 {digest}")
    else:
        print(json.dumps({"inputs": [{"id": i["id"], "phase": i["phase"], "tdb": i["tdb"]} for i in payload["inputs"]],
                          "worldserver_conf_dist": payload["worldserver_conf_dist"]}, indent=1))
    return 0


def run_base_stat_gap(args: argparse.Namespace) -> int:
    payload = build_base_stat_gap(args.tdb, args.probe)
    if args.out is not None:
        digest = write_corpus(args.out, payload)
        print(f"wrote {args.out} sha256 {digest}")
    else:
        print(json.dumps({k: payload[k] for k in ("class_coverage", "race_coverage", "level_90_verdict")}, indent=1)[:4000])
    return 0
