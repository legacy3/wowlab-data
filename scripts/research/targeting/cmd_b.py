"""Track B commands: relation predicates and explicit-target validation.

    targeting.py relations --dummies X.json --dummy-difficulty Y.json [--out relations.json]
    targeting.py explicit-census [--out explicit-validation.json]
    targeting.py validate <fixture.json>            # staged verdicts + trace
    targeting.py relation <fixture.json> <from> <to> [--spell-json '{...}']

The ``--dummies`` / ``--dummy-difficulty`` inputs (``targeting-corpora/inputs/``) are produced by
``tools/regen_targeting.py --stage tdb`` (see :data:`DUMMY_EXTRACT_COMMANDS`): a full projected
creature_template extract, filtered by ``targeting.py training-dummy-select`` (selector:
``relations.select_dummies``), then the creature_template_difficulty rows of the selected entries.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import CORPORA, PINS
from .cli import emit

DUMMY_EXTRACT_COMMANDS = [
    "python3 tools/tdb_world_extract.py --tdb <tdb.sql> --tables creature_template "
    "--project creature_template=entry,name,subname,faction,unit_flags,unit_flags2,unit_flags3,type,flags_extra,ScriptName "
    "--out targeting-corpora/inputs/.creature-template-full.json",
    "python3 targeting.py training-dummy-select --source targeting-corpora/inputs/.creature-template-full.json "
    "--remove-source --out targeting-corpora/inputs/training-dummies.json",
    "python3 targeting.py training-dummy-ids --out targeting-corpora/inputs/training-dummies.ids.json",
    "python3 tools/tdb_world_extract.py --tdb <tdb.sql> --tables creature_template_difficulty "
    "--project creature_template_difficulty=Entry,DifficultyID,TypeFlags,StaticFlags1,StaticFlags4 "
    "--keep creature_template_difficulty.Entry=@targeting-corpora/inputs/training-dummies.ids.json "
    "--out targeting-corpora/inputs/training-dummy-difficulty.json",
]


def _dummy_select(args) -> int:
    """Filter a full creature_template extract to the training-dummy selection (keeps the extract layout)."""
    from . import relations
    src = Path(args.source)
    doc = json.loads(src.read_text(encoding="utf-8"))
    t = doc["tables"]["creature_template"]
    rows = [dict(zip(t["columns"], r)) for r in t["rows"]]
    keep = {r["entry"] for r in relations.select_dummies(rows)}
    t["rows"] = sorted((r for r in t["rows"] if r[t["columns"].index("entry")] in keep), key=lambda r: r[0])
    t["row_count_emitted"] = len(t["rows"])
    t["row_filters"] = {"selector": "relations.select_dummies"}
    doc["selector"] = {"function": "targeting.relations.select_dummies",
                       "rule": f"ScriptName == 'npc_training_dummy' OR ((name|subname) ~* /{relations.DUMMY_NAME_RE}/ "
                               f"AND NOT name ~* /{relations.DUMMY_EXCLUDE_RE}/)",
                       "selected": len(t["rows"]), "from_rows": len(rows)}
    doc["unparsed"] = [u for u in doc.get("unparsed", []) if any(f"({k}," in u.get("statement", "") for k in keep)]
    emit(doc, args.out)
    if args.remove_source:
        src.unlink()
    return 0


def _add_select(p) -> None:
    p.add_argument("--source", required=True)
    p.add_argument("--remove-source", action="store_true")
    p.add_argument("--out", required=True)


UNKNOWNS = [
    {"id": "TG-B-01", "subject": "SpellInfo::IsPositive (NegativeEffects)", "evidence": "unresolved",
     "known": "IsValidAttack/AssistTarget, SpellHitResult, PreprocessSpellHit and CheckTarget read IsPositive()",
     "unknown": "load-time positivity (SpellInfo.cpp:4609+ _isPositiveTarget/_isPositiveEffectImpl) is not ported; views must state it",
     "why_unresolved": "outside Track B (payload/positivity pass)", "reopen_condition": "positivity oracle lands (G SpellView.positive from data)",
     "build_skew": "n/a"},
    {"id": "TG-B-02", "subject": "CanSeeOrDetect", "evidence": "unresolved",
     "known": "explicit checks use implicit=false (stealth/invisibility enforced), area checks implicit=true (SpellInfo.cpp:2335)",
     "unknown": "stealth/invisibility detection arithmetic", "why_unresolved": "world visibility machinery; fixture fact `can_see`",
     "reopen_condition": "simulated targets can be stealthed/invisible", "build_skew": "n/a"},
    {"id": "TG-B-03", "subject": "HasAuraState(state, spell, caster)", "evidence": "structural-inference",
     "known": "TargetAuraState gates (SpellInfo.cpp:2496) read Unit::HasAuraState (Unit.cpp:6145) incl. caster-specific ABILITY_IGNORE_AURASTATE",
     "unknown": "per-caster aura-state derivation is modelled as a flat fixture list `aura_states`",
     "why_unresolved": "aura-state producers belong to the aura lifecycle pass", "reopen_condition": "aura-state producer model exists",
     "build_skew": "n/a"},
    {"id": "TG-B-04", "subject": "CONDITION_SOURCE_TYPE_SPELL conditions", "evidence": "world-db-fact",
     "known": "CheckCast evaluates spell conditions with the explicit object as target (Spell.cpp:5919)",
     "unknown": "condition evaluation (generic ConditionMgr not built)", "why_unresolved": "brief scope guard",
     "reopen_condition": "Track E / conditions adapter covers the spell's rows", "build_skew": False},
    {"id": "TG-B-05", "subject": "ATTR8_ENFORCE_IN_COMBAT_RESSURECTION_LIMIT", "evidence": "trinity-consumer",
     "known": "CheckTarget fails with TARGET_CANNOT_BE_RESURRECTED when the instance has 0 combat-res charges during an encounter (SpellInfo.cpp:2520)",
     "unknown": "InstanceScript charge state", "why_unresolved": "instance runtime state",
     "reopen_condition": "encounter simulation models combat-res charges", "build_skew": False},
    {"id": "TG-B-06", "subject": "vehicle / minipet explicit targets", "evidence": "trinity-consumer",
     "known": "TARGET_FLAG_UNIT_PASSENGER/MINIPET accepted via IsOnVehicle / CritterGUID (SpellInfo.cpp:2554-2559)",
     "unknown": "vehicle seat state", "why_unresolved": "out of combat-sim scope", "reopen_condition": "vehicle encounters",
     "build_skew": "n/a"},
    {"id": "TG-B-07", "subject": "HasInArc binary32 atan2", "evidence": "structural-inference",
     "known": "facing uses std::atan2 on float operands (Position.h:140)",
     "unknown": "local helper evaluates atan2 in double then narrows; exact float atan2 lives in Track C geometry",
     "why_unresolved": "pending targeting.geometry actor-level helper", "reopen_condition": "geometry.has_in_arc_actors available",
     "build_skew": "n/a"},
    {"id": "TG-B-08", "subject": "Unit::IsControlledByPlayer for ATTR5_NOT_ON_PLAYER_CONTROLLED_NPC", "evidence": "structural-inference",
     "known": "m_ControlledByPlayer is set when a player-controlled owner/charmer exists",
     "unknown": "exact set/reset points (charm/possess edge cases)", "why_unresolved": "approximated by `has an affecting player`",
     "reopen_condition": "a witness depends on a charmed/possessed NPC", "build_skew": "n/a"},
]

RETAIL_EXPERIMENTS = [
    {"id": "TG-B-R1", "question": "Does a neutral training dummy use the friendly (index 1) range column?",
     "model_a": "Trinity: GetSpellMinMaxRangeForTarget uses !IsHostileTo -> neutral => friendly column (Object.cpp:1674)",
     "model_b": "harmful spells always use the hostile column",
     "setup": "cast a spell with SpellRange 160/161/162 at a neutral dummy at a distance between the two maxima",
     "observable": "SPELL_FAILED_OUT_OF_RANGE vs cast start", "fidelity": "exact",
     "note": "no current-player explicit-unit spell uses a split range row (census); relevant only for the 10 split rows"},
    {"id": "TG-B-R2", "question": "Is the explicit target's relation re-validated when the cast completes/lands?",
     "model_a": "Trinity: only CheckCast (prepare + completion); recipient stage does not re-check relation; hit stage only evades delayed negative spells on friendly players",
     "model_b": "relation re-checked at hit (target becoming friendly => miss)",
     "setup": "cast a harmful projectile at a player whose duel ends in flight; or at an NPC that turns friendly",
     "observable": "combat log SPELL_MISSED EVADE vs damage vs nothing", "fidelity": "approximate"},
    {"id": "TG-B-R3", "question": "Are dead-target errors reported as 'Invalid target' or 'Target is dead' for harmful spells?",
     "model_a": "Trinity: ENEMY-flag spells fail in CheckExplicitTarget -> SPELL_FAILED_BAD_TARGETS",
     "model_b": "SPELL_FAILED_TARGETS_DEAD (CheckTarget)", "setup": "cast a harmful spell on a dead enemy",
     "observable": "UI_ERROR_MESSAGE / SPELL_FAILED text", "fidelity": "exact"},
]

TRINITY_DEFECTS = [
    {"id": "TG-B-D1", "file_line": "Entities/Object/Object.cpp:2469 and :2584",
     "description": "sanctuary guard `(!bySpell || bySpell->HasAttribute(SPELL_ATTR8_IGNORE_SANCTUARY))` applies the sanctuary block only "
                    "to spells that carry the IGNORE attribute (inverted vs Spell.cpp:2814 `!HasAttribute(...)`)",
     "effect_on_recipients": "PvP: ordinary spells can attack/assist across sanctuary; IGNORE_SANCTUARY spells are the ones blocked",
     "oracle_behaviour": "reproduced; trace defect=TG-B-D1"},
    {"id": "TG-B-D2", "file_line": "Spells/Spell.cpp:846 (with :2486-2488 and :8533)",
     "description": "SPELL_ATTR2_FAIL_ON_ALL_TARGETS_IMMUNE inspects MissCondition during target selection, but AddUnitTarget computes "
                    "SpellHitResult with canImmune=false and full immunity is only applied in PreprocessSpellLaunch; only damage-only "
                    "immunity (Object.cpp:1957) can be seen",
     "effect_on_recipients": "spell proceeds (and later misses IMMUNE) when every target is spell-immune but not damage-immune",
     "oracle_behaviour": "recipient stage reports miss from SpellHitResult(canImmune=false); launch stage applies immunity"},
    {"id": "TG-B-D3", "file_line": "Spells/SpellInfo.cpp:2494",
     "description": "aura-state exemption compares `unitCaster->GetCharmerOrOwner()` with `target` (the WorldObject, i.e. the corpse "
                    "for corpse targets) rather than `unitTarget`",
     "effect_on_recipients": "corpse targets of a pet's owner are not exempted from TargetAuraState (latent)",
     "oracle_behaviour": "reproduced (compares with the object id)"},
    {"id": "TG-B-D4", "file_line": "Entities/Object/Object.cpp:2061",
     "description": "two ownerless non-unit objects compare nullptr == nullptr and are always FRIENDLY",
     "effect_on_recipients": "gameobject/dynobj casters without owner are friendly to every other ownerless non-unit (latent)",
     "oracle_behaviour": "reproduced"},
]

MINIMAL_SUBSET = {
    "needed_for_combat_sim": [
        "faction templates (db2) of the player race and the target creature -> reaction both ways",
        "owner/charmer resolution for pets/guardians/totems (same owner => FRIENDLY; affecting player)",
        "UNIT_FLAG_PLAYER_CONTROLLED on players and their summons; creature unit flags NON_ATTACKABLE(_2)/IMMUNE_TO_PC/UNINTERACTIBLE",
        "alive state + IsAllowingDeadTarget",
        "group/raid membership (Track F) for PARTY/RAID/RAID_CLASS and for player-player reaction",
        "TypeFlags TREAT_AS_RAID_UNIT / CAN_ASSIST for assisting NPC allies",
        "reputation state only for rep-capable creature factions (training dummies: none)",
        "IsPositive of the spell (stated)",
    ],
    "classified_out_of_scope": [
        "duel, FFA, PvP/UNK1/sanctuary flags, contested guards, forced reactions, GM/uber, taxi/flight, mounted pets, vehicles, "
        "stealth/invisibility detection, attackable-by-summoner summons",
    ],
    "profile": "facts.profile = combat-sim supplies the out-of-scope facts as absent; anything else unstated fails closed",
}


def _provenance(command: str, extra: dict | None = None) -> dict:
    p = {"pins": PINS, "command": command}
    if extra:
        p.update(extra)
    return p


def _relations(args) -> int:
    from . import context, relations
    ctx = context.get()
    dummies = json.loads(Path(args.dummies).read_text()) if args.dummies else None
    diffs = json.loads(Path(args.dummy_difficulty).read_text()) if args.dummy_difficulty else None
    body = relations.census(ctx, dummies, diffs)
    extract_prov = {}
    for name, doc in (("dummies", dummies), ("dummy_difficulty", diffs)):
        if doc is not None:
            pv = doc.get("provenance", {})
            extract_prov[name] = {k: pv.get(k) for k in ("tool", "base_world_database", "base_world_database_sha256",
                                                          "update_files_total", "final_row_counts",
                                                          "unparsed_statement_count")}
            kept = set()
            for tab in doc.get("tables", {}).values():
                kept |= {int(r[0]) for r in tab.get("rows", [])}
            extract_prov[name]["unparsed_touching_kept_rows"] = any(
                f"({k}," in u.get("statement", "") for u in doc.get("unparsed", []) for k in kept)
    if args.differential_worlds:
        diff = differential(ctx.bundle.source, args.differential_worlds, 1)
        body["differential"] = {"probe": "tools/tc_target_relation_probe", "seed": 1, "stats": diff["stats"],
                                "mismatch_examples": diff["mismatches"], "evidence": "differential"}
    body.update({
        "provenance": _provenance("python3 targeting.py relations (inputs: targeting-corpora/inputs/training-dummies.json, "
                                  "training-dummy-difficulty.json) "
                                  f"--differential-worlds {args.differential_worlds} "
                                  "--out ../../docs/research/targeting-corpora/relations.json",
                                  {"world_db_extracts": DUMMY_EXTRACT_COMMANDS, "extract_provenance": extract_prov}),
        "consumers": {
            "GetReactionTo": "Entities/Object/Object.cpp:2035", "GetFactionReactionTo": "Entities/Object/Object.cpp:2141",
            "IsHostileTo": "Entities/Object/Object.cpp:2188", "IsFriendlyTo": "Entities/Object/Object.cpp:2193",
            "IsValidAttackTarget": "Entities/Object/Object.cpp:2331", "IsValidAssistTarget": "Entities/Object/Object.cpp:2489",
            "WorldObjectSpellTargetCheck": "Spells/Spell.cpp:9306", "IsInPartyWith": "Entities/Unit/Unit.cpp:12184",
            "IsInRaidWith": "Entities/Unit/Unit.cpp:12203", "FactionTemplateEntry": "DataStores/DB2Structure.h:1720",
        },
        "caster_identity": {
            "WorldObjectSpellTargetCheck._caster": "m_caster at every construction site (Spell.cpp:1301,1892,1994,2201,2216); no owner/charmer substitution",
            "WorldObjectSpellTargetCheck._referer": "m_caster, or the area referer (Spell.cpp:2216) -- PARTY/RAID membership only",
            "CheckCast CheckExplicitTarget": "m_originalCaster unless m_caster is a gameobject (Spell.cpp:5943-5947)",
            "CheckCast CheckTarget / CheckRange / LOS": "m_caster (Spell.cpp:5956, 7274, 5996)",
            "SpellHitResult in AddUnitTarget": "m_originalCaster ?: m_caster (Spell.cpp:2485)",
            "PreprocessSpellHit relation branch": "m_caster (Spell.cpp:3141-3146)",
            "owner resolution": "inside GetReactionTo/GetAffectingPlayer/IsInPartyWith (one GetCharmerOrOwner hop)",
        },
        "minimal_subset": MINIMAL_SUBSET,
        "unknowns": [u for u in UNKNOWNS if u["id"] in ("TG-B-01", "TG-B-02", "TG-B-06", "TG-B-08")],
        "retail_experiments": [RETAIL_EXPERIMENTS[0]],
        "trinity_defects": [d for d in TRINITY_DEFECTS if d["id"] in ("TG-B-D1", "TG-B-D4")],
    })
    emit(body, args.out)
    return 0


def _explicit_census(args) -> int:
    from . import context, explicit
    ctx = context.get()
    body = explicit.census(ctx)
    body.update({
        "provenance": _provenance("python3 targeting.py explicit-census --out ../../docs/research/targeting-corpora/explicit-validation.json"),
        "stages": {
            "init": "Spell::InitExplicitTargets Spell.cpp:621",
            "cast-eligibility": "Spell::CheckCast target part Spell.cpp:5936-6010 + CheckRange 7274; prepare strict (3492, IGNORE_TARGET_CHECK map 3495), completion non-strict (3756, skipped for instant casts)",
            "redirect": "Spell::SelectExplicitTargets Spell.cpp:691 (after completion checks)",
            "recipient-selection": "SelectImplicitTargetObjectTargets 1807 -> AddUnitTarget(checkIfValid=true, implicit=false) 2443",
            "effect-application": "PreprocessSpellLaunch 8526, TargetInfo::PreprocessTarget 2749, PreprocessSpellHit 3109, DoTargetSpellHit 2796",
        },
        "checkcast_boundary": list(explicit.CHECKCAST_BOUNDARY),
        "unknowns": UNKNOWNS,
        "retail_experiments": RETAIL_EXPERIMENTS,
        "trinity_defects": TRINITY_DEFECTS,
    })
    emit(body, args.out)
    return 0


def _validate(args) -> int:
    from . import explicit
    from .fixture import World
    from .trace import Trace
    w = World.load(args.fixture)
    spell = w.raw.get("spell_view")
    if spell is None:
        from . import FailClosed
        raise FailClosed("fixture: `spell_view` (Track B contract dict) required for validate")
    t = Trace()
    verdict = explicit.validate(w, spell, t)
    emit({"fixture": w.name, "verdict": verdict, "trace": t.to_json()}, args.out)
    return 0


def _relation(args) -> int:
    from . import relations
    from .fixture import World
    from .trace import Trace
    w = World.load(args.fixture)
    spell = json.loads(args.spell_json) if args.spell_json else None
    t = Trace()
    out = {"reaction": relations.REP_NAMES[relations.reaction(w, args.src, args.dst, t)],
           "reverse_reaction": relations.REP_NAMES[relations.reaction(w, args.dst, args.src, t)],
           "valid_attack": relations.valid_attack(w, args.src, args.dst, spell, t),
           "valid_assist": relations.valid_assist(w, args.src, args.dst, spell, t)}
    emit({"result": out, "trace": t.to_json()}, args.out)
    return 0


def _add_relations(p) -> None:
    p.add_argument("--dummies", default=str(CORPORA / "inputs" / "training-dummies.json"))
    p.add_argument("--dummy-difficulty", default=str(CORPORA / "inputs" / "training-dummy-difficulty.json"))
    p.add_argument("--differential-worlds", type=int, default=0,
                   help="also run the verbatim-probe differential (seed 1) and record its stats")
    p.add_argument("--out")


def _add_fixture(p) -> None:
    p.add_argument("fixture")
    p.add_argument("--out")


def _add_relation(p) -> None:
    p.add_argument("fixture")
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--spell-json")
    p.add_argument("--out")


COMMANDS = {
    "relations": ("relation corpus: player templates x training dummies, fact inventory, check types", _add_relations, _relations),
    "explicit-census": ("explicit-target check matrix + effect_classes over current-player spells",
                        lambda p: p.add_argument("--out"), _explicit_census),
    "validate": ("staged explicit-target validation of a fixture (needs `spell_view`)", _add_fixture, _validate),
    "relation": ("reaction / attack / assist between two fixture actors", _add_relation, _relation),
    "training-dummy-select": ("filter a full creature_template extract to the training-dummy selection",
                              _add_select, _dummy_select),
}

__all__ = ["COMMANDS", "CORPORA"]


# ---------------------------------------------------------------------------
# differential: relations.py vs tools/tc_target_relation_probe (verbatim Trinity)
# ---------------------------------------------------------------------------
PROBE_DIR = Path(__file__).resolve().parents[1] / "tools" / "tc_target_relation_probe"
PLAYER_FLAG_BITS = {"CONTESTED_PVP": 0x100, "UBER": 0x80000}          # Player.h PlayerFlags (probe-extracted)
DIFF_ATTRS = ("SPELL_ATTR5_IGNORE_AREA_EFFECT_PVP_CHECK", "SPELL_ATTR6_CAN_TARGET_UNTARGETABLE",
              "SPELL_ATTR6_CAN_ASSIST_IMMUNE_PC", "SPELL_ATTR6_IGNORE_PHASE_SHIFT", "SPELL_ATTR8_CAN_ATTACK_IMMUNE_PC",
              "SPELL_ATTR8_IGNORE_SANCTUARY", "SPELL_ATTR11_CAN_ASSIST_UNINTERACTIBLE")
DIFF_FLAGS = ("NON_ATTACKABLE", "PLAYER_CONTROLLED", "NOT_ATTACKABLE_1", "IMMUNE_TO_PC", "IMMUNE_TO_NPC",
              "PET_IN_COMBAT", "NON_ATTACKABLE_2", "ON_TAXI", "UNINTERACTIBLE")
_KINDS = ("player", "creature", "pet", "guardian", "totem", "gameobject")
SYNTHETIC_TEMPLATES = {
    "9001": {"faction": 7, "flags": 0x2000, "faction_group": 0, "friend_group": 0, "enemy_group": 0,
             "enemies": [0] * 8, "friends": [0] * 8},                       # HOSTILE_BY_DEFAULT
    "9002": {"faction": 72, "flags": 0x1000, "faction_group": 3, "friend_group": 2, "enemy_group": 12,
             "enemies": [0] * 8, "friends": [0] * 8},                       # contested guard, rep-capable
    "9003": {"faction": 14, "flags": 0, "faction_group": 0, "friend_group": 0, "enemy_group": 0,
             "enemies": [1, 2] + [0] * 6, "friends": [7] + [0] * 7},        # explicit enemy/friend lists
}


def _random_world(rng, templates: dict, factions: dict) -> tuple[dict, list[str], list[tuple]]:
    from procs.enums import attr

    from .relations import UNIT_FLAGS
    n = rng.randint(2, 5)
    tpl_ids = sorted(templates, key=int)
    actors, lines = [], []
    for tid in tpl_ids:
        t = templates[tid]
        lines.append(" ".join(map(str, ["T", tid, t["faction"], t["flags"], t["faction_group"], t["friend_group"],
                                        t["enemy_group"], *t["enemies"], *t["friends"]])))
    for fid, f in sorted(factions.items(), key=lambda kv: int(kv[0])):
        lines.append(f"F {fid} {f['reputation_index']}")
    kinds = []
    for i in range(n):
        kind = rng.choice(_KINDS) if i else rng.choice(("player", "creature", "pet", "gameobject"))
        kinds.append(kind)
    players = [i for i, k in enumerate(kinds) if k == "player"]
    rep_factions = sorted({t["faction"] for t in templates.values() if factions[str(t["faction"])]["reputation_index"] >= 0})
    for i, kind in enumerate(kinds):
        tpl = rng.choice(tpl_ids + ["-1"]) if rng.random() < 0.95 else "-1"
        flags = [f for f in DIFF_FLAGS if rng.random() < (0.12 if f != "PLAYER_CONTROLLED" else
                                                          (0.9 if kind in ("player", "pet", "guardian", "totem") else 0.1))]
        pvp = [f for f in ("PVP", "UNK1", "FFA_PVP", "SANCTUARY") if rng.random() < 0.2]
        pflags = [f for f in ("CONTESTED_PVP", "UBER") if kind == "player" and rng.random() < 0.1]
        owner = charmer = summoner = None
        earlier = list(range(i))
        if kind in ("pet", "guardian", "totem", "gameobject") and earlier and rng.random() < 0.85:
            owner = rng.choice(earlier)
        if kind in ("creature", "player", "pet") and earlier and rng.random() < 0.2:
            charmer = rng.choice(earlier)
        if kind in ("pet", "guardian", "totem") and rng.random() < 0.8:
            summoner = owner if owner is not None and rng.random() < 0.8 else (rng.choice(earlier) if earlier else None)
        group = None
        if kind == "player" and rng.random() < 0.6:
            group = {"id": rng.choice((1, 2)), "subgroup": rng.choice((1, 2))}
        facts = {"profile": "combat-sim", "faction_template": None if tpl == "-1" else int(tpl), "unit_flags": flags,
                 "unit_flags2": ["IGNORE_REPUTATION"] if rng.random() < 0.1 else [], "pvp_flags": pvp,
                 "player_flags": pflags, "game_master": kind == "player" and rng.random() < 0.05,
                 "unattackable_state": kind != "gameobject" and rng.random() < 0.05,
                 "mounted": kind == "player" and rng.random() < 0.2,
                 "treated_as_raid_unit": kind != "player" and kind != "gameobject" and rng.random() < 0.2,
                 "creature_type_flag_can_assist": kind != "player" and kind != "gameobject" and rng.random() < 0.2,
                 "attackable_by_summoner": kind in ("pet", "guardian", "totem") and rng.random() < 0.3,
                 "forced_reactions": {}, "duel": None}
        if kind == "gameobject":
            facts["go_type"] = "trap"
        if kind == "creature":
            facts["is_summon"] = False
        if kind in ("player", "gameobject"):
            del facts["treated_as_raid_unit"], facts["creature_type_flag_can_assist"]
        a = {"id": f"o{i}", "kind": kind, "alive": rng.random() < 0.8, "facts": facts,
             "owner": None if owner is None else f"o{owner}", "charmer": None if charmer is None else f"o{charmer}",
             "summoner": None if summoner is None else f"o{summoner}", "group": group}
        actors.append(a)
        uf = sum(UNIT_FLAGS[f] for f in flags)
        pv = sum({"PVP": 1, "UNK1": 2, "FFA_PVP": 4, "SANCTUARY": 8}[f] for f in pvp)
        pf = sum(PLAYER_FLAG_BITS[f] for f in pflags)
        lines.append(" ".join(map(str, [
            "O", i, _KINDS.index(kind), tpl, uf, 4 if facts["unit_flags2"] else 0, pv, pf, int(a["alive"]),
            int(facts["game_master"]), int(facts["unattackable_state"]), int(facts["mounted"]),
            int(facts.get("treated_as_raid_unit", False)), int(facts.get("creature_type_flag_can_assist", False)),
            -1 if owner is None else owner, -1 if charmer is None else charmer, -1 if summoner is None else summoner,
            int(facts["attackable_by_summoner"]), group["id"] if group else 0, group["subgroup"] if group else 0])))
    for p in players:
        reps = {}
        for fid in rep_factions:
            rank, war = rng.randint(0, 7), rng.random() < 0.5
            reps[str(fid)] = {"rank": rank, "at_war": war}
            lines.append(f"R {p} {fid} {rank} {int(war)}")
        actors[p]["facts"]["reputation"] = reps
        if rng.random() < 0.2:
            fid = rng.choice(sorted({t["faction"] for t in templates.values()}))
            rank = rng.randint(0, 7)
            actors[p]["facts"]["forced_reactions"] = {str(fid): rank}
            lines.append(f"X {p} {fid} {rank}")
    if len(players) >= 2 and rng.random() < 0.4:
        p, q = rng.sample(players, 2)
        prog = rng.random() < 0.7
        actors[p]["facts"]["duel"] = {"opponent": f"o{q}", "in_progress": prog}
        lines.append(f"D {p} {q} {int(prog)}")
    relations = []
    for i in range(n):
        for j in range(n):
            if i != j and rng.random() < 0.1:
                relations.append({"from": f"o{i}", "to": f"o{j}", "can_see": False})
                lines.append(f"V {i} {j}")
    queries = []
    for i in range(n):
        for j in range(n):
            if rng.random() < 0.3:
                queries.append((i, j, None))
            names = [x for x in DIFF_ATTRS if rng.random() < 0.25]
            spell = {"attributes": names, "is_positive": rng.random() < 0.5, "is_affecting_area": rng.random() < 0.3,
                     "is_allowing_dead_target": rng.random() < 0.3}
            words = {0: 0, 5: 0, 6: 0, 8: 0, 11: 0}
            for x in names:
                w, bit = attr(x)
                words[w] |= bit
            queries.append((i, j, spell))
            lines.append(" ".join(map(str, ["Q", i, j, 1, words[0], words[5], words[6], words[8], words[11], 0,
                                            int(spell["is_positive"]), int(spell["is_affecting_area"]),
                                            int(spell["is_allowing_dead_target"])])))
    # the no-spell queries were appended to `queries` before their spell twin; emit them in the same order
    lines = [ln for ln in lines if not ln.startswith("Q ")]
    for i, j, spell in queries:
        if spell is None:
            lines.append(f"Q {i} {j} 0 0 0 0 0 0 0 0 0 0")
        else:
            words = {0: 0, 5: 0, 6: 0, 8: 0, 11: 0}
            for x in spell["attributes"]:
                w, bit = attr(x)
                words[w] |= bit
            lines.append(" ".join(map(str, ["Q", i, j, 1, words[0], words[5], words[6], words[8], words[11], 0,
                                            int(spell["is_positive"]), int(spell["is_affecting_area"]),
                                            int(spell["is_allowing_dead_target"])])))
    lines.append("E")
    fixture = {"schema": "targeting-fixture/1", "name": "diff", "caster": "o0", "actors": actors,
               "relations": relations, "faction_templates": templates, "factions": factions}
    return fixture, lines, queries


def differential(ctx_source, worlds: int, seed: int) -> dict:
    """Run ``worlds`` random worlds through relations.py and the verbatim probe; return counts + mismatches."""
    import random
    import subprocess

    from . import FailClosed
    from . import relations as R
    from .fixture import World
    from .trace import Trace
    probe = PROBE_DIR / "probe"
    if not probe.exists():
        subprocess.run(["make", "-s", "-C", str(PROBE_DIR)], check=True)
    rng = random.Random(seed)
    templates, factions = R.faction_rows_from_db2(ctx_source, [1, 2, 7, 11, 14, 35, 1693, 1095])
    templates = {**templates, **SYNTHETIC_TEMPLATES}
    payload, expected = [], []
    for _ in range(worlds):
        fx, lines, queries = _random_world(rng, templates, factions)
        payload.extend(lines)
        expected.append((fx, queries))
    out = subprocess.run([str(probe)], input="\n".join(payload) + "\n", capture_output=True, text=True, check=True).stdout
    blocks = out.split("END\n")
    stats = {"worlds": worlds, "queries": 0, "fields_compared": 0, "mismatches": 0, "fail_closed": 0,
             "paths": {}}
    mismatches = []
    for (fx, queries), block in zip(expected, blocks):
        rows = [json.loads(x) for x in block.splitlines() if x.strip()]
        assert len(rows) == len(queries), (len(rows), len(queries))
        w = World.from_dict(fx)
        for (i, j, spell), row in zip(queries, rows):
            stats["queries"] += 1
            a, b = f"o{i}", f"o{j}"
            try:
                t = Trace()
                mine = {"r_ab": R.reaction(w, a, b), "r_ba": R.reaction(w, b, a),
                        "attack": R.valid_attack(w, a, b, spell, t), "assist": R.valid_assist(w, a, b, spell, t)}
                for st in t.stages:
                    if st.notes:
                        key = f"{st.name}:{st.notes[-1]}"
                        stats["paths"][key] = stats["paths"].get(key, 0) + 1
            except FailClosed as exc:
                stats["fail_closed"] += 1
                mismatches.append({"query": [a, b, spell], "fail_closed": str(exc)})
                continue
            for k in ("r_ab", "r_ba", "attack", "assist"):
                stats["fields_compared"] += 1
                if mine[k] != row[k]:
                    stats["mismatches"] += 1
                    mismatches.append({"field": k, "query": [a, b, spell], "python": mine[k], "trinity": row[k],
                                       "fixture": fx})
    stats["paths"] = dict(sorted(stats["paths"].items()))
    return {"stats": stats, "mismatches": mismatches[:5]}


def _differential(args) -> int:
    from . import context
    ctx = context.get()
    res = differential(ctx.bundle.source, args.worlds, args.seed)
    res["provenance"] = _provenance(f"python3 targeting.py relation-differential --worlds {args.worlds} --seed {args.seed}")
    emit(res, args.out)
    return 0 if not res["mismatches"] else 1


def _add_diff(p) -> None:
    p.add_argument("--worlds", type=int, default=300)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out")


COMMANDS["relation-differential"] = ("relations.py vs verbatim Trinity relation probe", _add_diff, _differential)
