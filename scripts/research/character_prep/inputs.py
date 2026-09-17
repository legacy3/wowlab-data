"""Track C1 / C3: the canonical input inventory and the initial-state inventory.

``input_inventory_document()`` classifies every input a character preparation could ask for as

``identity``   the caller must supply it; nothing derives it;
``derived``    never asked for; the compiler derives it from the snapshot + existing research;
``server``     a Trinity-authored world-database / configuration fact (agent F owns the values);
``encounter``  state of a fight or of the host, not of the character;
``excluded``   not part of combat preparation (display, legacy, bookkeeping).

``initial_state_document()`` classifies the Player state a prepared character starts with, each class tied to
the Trinity consumer that establishes it.  Both documents are static research tables plus snapshot-derived
power facts; every coordinate is ``path:line`` at the pinned TrinityCore commit
(``src/server/game/...`` unless stated otherwise).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import CORPORA, EVIDENCE_CLASSES, SNAPSHOT_BUILD, TDB_RELEASE, TRINITY_COMMIT, SourceError
from .fixture import canonical_json

INPUT_KINDS = ("identity", "derived", "server", "encounter", "excluded")
REQUIREMENT = ("required", "optional", "never")

P = "Entities/Player/Player.cpp"


def _i(name: str, kind: str, required: str, *, fixture_path: str | None = None, derived_path: str | None = None,
       source_tables: list[str] | None = None, consumer: list[str] | None = None, evidence: str,
       research: list[str] | None = None, notes: str = "") -> dict[str, Any]:
    return {"name": name, "kind": kind, "required": required, "fixture_path": fixture_path, "derived_path": derived_path,
            "source_tables": source_tables or [], "consumer": consumer or [], "evidence_class": evidence,
            "derivation_research": research or [], "notes": notes}


#: The canonical input inventory (C1).  ``fixture_path`` is a JSON pointer into the neutral fixture,
#: ``derived_path`` a pointer into the compiled output.
INPUT_INVENTORY: list[dict[str, Any]] = [
    # -- identity ---------------------------------------------------------------------------------------------------
    _i("build / snapshot provenance", "identity", "required", fixture_path="/provenance/snapshot_build",
       evidence="structural-inference", consumer=["every id in the fixture is only meaningful for one DB2 build"],
       notes="also source_kind, captured_at, external_ids (opaque); the compiler refuses nothing on build mismatch yet but "
             "reports it in provenance"),
    _i("race", "identity", "required", fixture_path="/identity/race_id", derived_path="/derived/identity",
       source_tables=["ChrRaces"], consumer=[f"{P}:387 Player::Create", "Globals/ObjectMgr.cpp:4319-4330 race x class level info"],
       evidence="db2-fact", research=["charstats.identity"]),
    _i("class", "identity", "required", fixture_path="/identity/class_id", derived_path="/derived/identity",
       source_tables=["ChrClasses"], consumer=[f"{P}:387 Player::Create"], evidence="db2-fact", research=["charstats.identity"]),
    _i("specialization", "identity", "required", fixture_path="/identity/spec_id", derived_path="/derived/identity",
       source_tables=["ChrSpecialization"], consumer=[f"{P}:30631-30648 LearnSpecializationSpells",
                                                      "Spells/TraitMgr.cpp:611-619 SpecSet conditions"],
       evidence="db2-fact", research=["charstats.identity", "charstats.acquisition"]),
    _i("level", "identity", "required", fixture_path="/identity/level", derived_path="/derived/identity",
       source_tables=["SpellLevels", "TraitCurrencySource", "TraitCond"],
       consumer=["World/World.cpp:750 MaxPlayerLevel default (90)", f"{P}:2323 InitStatsForLevel"],
       evidence="trinity-consumer", notes="1..90; levels 81-90 have no player_classlevelstats row (server gap, see F)"),
    _i("trait selections {node_entry_id, rank}", "identity", "required", fixture_path="/traits/entries",
       derived_path="/derived/traits", source_tables=["TraitNodeEntry", "TraitNodeXTraitNodeEntry", "TraitNode", "TraitCond",
                                                     "TraitCost", "TraitEdge", "TraitCurrencySource"],
       consumer=[f"{P}:28480 _LoadTraits (merge :28533-28550, validate :28553)", "Spells/TraitMgr.cpp:838-1015 ValidateConfig",
                 f"{P}:29380-29409 ApplyTraitConfig / ApplyTraitEntry"],
       evidence="trinity-consumer", research=["character_prep.compiler.TraitEngine (port of TraitMgr)"],
       notes="rank = purchased ranks, excluding granted ranks (the character DB stores the same); tree and node are derived"),
    _i("active hero sub-tree", "identity", "optional", fixture_path="/traits/hero_subtree_id",
       derived_path="/derived/traits/value/hero_subtree", source_tables=["TraitSubTree", "TraitNodeEntry.TraitSubTreeID"],
       consumer=["Spells/TraitMgr.cpp:928-964 sub-tree cache", "Spells/TraitMgr.cpp:1017-1031 CanApplyTraitNode"],
       evidence="trinity-consumer", notes="the SubTreeSelection entry is derived from it when not listed"),
    _i("loadout / export string", "excluded", "optional", fixture_path="/traits/loadout_string", evidence="structural-inference",
       notes="opaque provenance only; never decoded at runtime (its content is exactly the trait selections above)"),
    _i("PvP talents", "identity", "optional", fixture_path="/pvp_traits", derived_path="/derived/pvp_traits",
       source_tables=["PvpTalent", "PvpTalentCategory", "PvpTalentSlotUnlock"],
       consumer=[f"{P}:28467 _LoadPvpTalents", f"{P}:27675-27702 LearnPvpTalent", f"{P}:27730 AddPvpTalent"],
       evidence="trinity-consumer", notes="acquired only with options.enable_pvp_talents; ACTIVATION is encounter/host state"),
    _i("explicitly learned spells (character_spell)", "identity", "optional", fixture_path="/learned_spells",
       derived_path="/derived/spells", source_tables=["SpellName", "SkillLineAbility (AcquireMethod 0)"],
       consumer=[f"{P}:18609 _LoadSpells -> AddSpell (:2690, loading=true)", f"{P}:25404-25417 AcquireMethod 0 never auto-learned"],
       evidence="trinity-consumer",
       notes="spells no automatic path grants (trainer, quest, item, AcquireMethod 0 'Learned'), e.g. 296087 Dual Wield for Arms "
             "(report §14); the compiler roots them as kind learned-spell and fails closed on an unknown SpellID"),
    _i("saved skills and skill values (character_skills)", "identity", "optional", fixture_path=None,
       source_tables=["SkillRaceClassInfo (any Availability)", "SkillLineAbility", "world: skill_tiers"],
       consumer=[f"{P}:18591 _LoadSkills (:27228-27345) -> LearnSkillRewardedSpells with the SAVED value",
                 f"{P}:25267 LearnDefaultSkills skips skills already loaded", f"{P}:11207-11213 CanUseItem RequiredSkill"],
       evidence="unresolved",
       notes="not in the fixture format: the compiler assumes the default-skill values of the identity. Saved skills of any "
             "SkillRaceClassInfo availability (professions, riding, Availability-2 lines) teach their AcquireMethod 1/2 "
             "abilities on load and gate items with RequiredSkill; census of combat-relevant abilities on such lines open"),
    _i("glyphs (character_glyphs)", "identity", "optional", fixture_path=None, source_tables=["GlyphProperties", "GlyphBindableSpell"],
       consumer=[f"{P}:18614 _LoadGlyphs (:28410)", f"{P}:18616 _LoadGlyphAuras (:19110-19114) casts GlyphProperties.SpellID"],
       evidence="unresolved",
       notes="not in the fixture format. 686 glyph spells in the snapshot; their aura effects include 103 SPELL_AURA_DUMMY, "
             "5 PROC_TRIGGER_SPELL, 5 ADD_FLAT_MODIFIER, 3 aura 285, 6 aura 332 (census in the closeout review): "
             "not all cosmetic"),
    _i("equipment: slot -> item", "identity", "required", fixture_path="/equipment", derived_path="/derived/gear",
       source_tables=["Item", "ItemSparse"],
       consumer=[f"{P}:9194-9290 FindEquipSlot", f"{P}:19240 _LoadInventory -> CanEquipItem (:10714-)",
                 f"{P}:11129-11234 CanUseItem (required level, class/race, skill, spell, holiday, reputation)",
                 f"{P}:27374-27437 CanEquipUniqueItem"],
       evidence="trinity-consumer", research=["gearing.loadout.LoadoutEntry"],
       notes="the slot is identity: the same ItemID can be MAINHAND or OFFHAND; an item failing CanEquipItem at load is mailed "
             "(Player.cpp:19295-19299), so the compiler fails closed on it"),
    _i("item context (difficulty / source)", "identity", "optional", fixture_path="/equipment/<slot>/context",
       source_tables=["ItemBonusListGroupEntry", "ItemContextPickerEntry"], evidence="db2-fact", research=["gearing report §2"],
       notes="difficulty is an item identity input, NOT encounter state"),
    _i("bonus lists", "identity", "optional", fixture_path="/equipment/<slot>/bonus_list_ids",
       source_tables=["ItemBonus", "ItemBonusList"], consumer=["Entities/Item/Item.cpp BonusData::AddBonus"],
       evidence="db2-fact", research=["gearing.resolver"], notes="upgrade track, sockets, tertiaries, crafted choices"),
    _i("gems", "identity", "optional", fixture_path="/equipment/<slot>/gems", source_tables=["GemProperties", "SpellItemEnchantment"],
       evidence="db2-fact", research=["gearing.gems"]),
    _i("permanent enchants", "identity", "optional", fixture_path="/equipment/<slot>/enchant_ids",
       source_tables=["SpellItemEnchantment"], consumer=[f"{P}:13417 ApplyEnchantment"], evidence="db2-fact",
       research=["gearing.enchants"]),
    _i("M+ keystone level / PvP tier of an item", "identity", "optional",
       fixture_path="/equipment/<slot>/mythic_plus_keystone_level", evidence="db2-fact", research=["gearing report"],
       notes="item-instance modifiers that select bonus lists"),
    _i("crafted stat modifiers", "identity", "optional", fixture_path="/equipment/<slot>/bonus_list_ids",
       consumer=["DataStores/DBCEnums.h:1273 ITEM_BONUS_MODIFIED_CRAFTING_STAT = 25 /*NYI*/"], evidence="unresolved",
       research=["gearing-pipeline-archaeology.md §1 crafted modifiers"],
       notes="only as bonus lists; ItemModifiedCraftingStat is absent from the snapshot; Trinity does not implement type 25"),
    _i("hunter pet: creature family / pet specialization / creature entry", "identity", "optional",
       fixture_path="/controlled_units/hunter_pet", derived_path="/derived/controlled_units", source_tables=["CreatureFamily",
                                                                                                             "ChrSpecialization"],
       consumer=["character DB character_pet"], evidence="structural-inference",
       research=["Track A (controlled_units) owns the contents"]),
    _i("warlock active demon", "identity", "optional", fixture_path="/controlled_units/active_demon",
       derived_path="/derived/controlled_units", evidence="structural-inference", research=["Track A"]),
    _i("preparation options", "identity", "optional", fixture_path="/options", evidence="structural-inference",
       notes="enable_pvp_talents, include_class_skill_lines (reporting only), rng (reserved, must be null)"),
    # -- derived ----------------------------------------------------------------------------------------------------
    _i("trait tree, node, granted ranks, owned/spent trait currency", "derived", "never", derived_path="/derived/traits",
       source_tables=["SkillLineXTraitTree", "SkillLine", "SkillRaceClassInfo", "TraitCond", "TraitCurrencySource"],
       consumer=["Spells/TraitMgr.cpp:338-354 GetTreesForConfig", "Spells/TraitMgr.cpp:766-820 granted",
                 "Spells/TraitMgr.cpp:409-473 owned currency"],
       evidence="trinity-consumer", research=["character_prep.compiler.TraitEngine"]),
    _i("item level, stats, ratings, armour, sockets", "derived", "never", derived_path="/derived/gear",
       evidence="db2-fact", research=["gearing.loadout.resolve_loadout", "gearing-pipeline-archaeology.md"]),
    _i("set membership and thresholds", "derived", "never", derived_path="/derived/gear/value/set_bonuses",
       source_tables=["ItemSet", "ItemSetSpell"], evidence="db2-fact", research=["gearing.sets"]),
    _i("weapon configuration (dps, delay, dual wield, titan grip)", "derived", "never", derived_path="/derived/weapon",
       consumer=["Spells/SpellEffects.cpp:2237-2242 EffectDualWield", "Spells/SpellEffects.cpp:4954-4960 EffectTitanGrip",
                 f"{P}:9249 / :9261 off-hand gates"],
       evidence="trinity-consumer", research=["gearing.resolver", "Track B/C weapon_combat (hook)"]),
    _i("gem / enchant payloads", "derived", "never", derived_path="/derived/gear", evidence="db2-fact", research=["gearing"]),
    _i("acquired spells (spec, mastery, traits, default skills, gear, PvP)", "derived", "never", derived_path="/derived/spells",
       source_tables=["SpecializationSpells", "SkillLineAbility", "SkillRaceClassInfo", "SpellLevels", "ItemEffect"],
       consumer=[f"{P}:30631-30648", f"{P}:25259-25441 default skills", f"{P}:29406 trait LearnSpell"],
       evidence="trinity-consumer", research=["charstats.acquisition", "character_prep.compiler.Snapshot.default_skill_spells"]),
    _i("passive auras active at preparation", "derived", "never", derived_path="/derived/spells/value/passives_active_at_prep",
       consumer=[f"{P}:3079-3104 HandlePassiveSpellLearn", f"{P}:8280-8300 ApplyItemDependentAuras",
                 "Entities/Unit/Unit.cpp:6078-6098 ModifyAuraState"],
       evidence="trinity-consumer"),
    _i("proc providers / marker Dummy auras", "derived", "never", derived_path="/derived/spells/value/proc_providers",
       source_tables=["SpellAuraOptions"], evidence="db2-fact",
       research=["procs-corpora/census.json", "dummy-corpora/markers.json"]),
    _i("mastery", "derived", "never", derived_path="/derived/stats/value/mastery", evidence="trinity-consumer",
       research=["charstats.mastery"], notes="Mastery 114585 (+8) arrives through default skill 183; see agent F"),
    _i("armour specialization", "derived", "never", derived_path="/derived/armor_specialization",
       consumer=[f"{P}:26203-26213 HasItemFitToSpellRequirements"], evidence="trinity-consumer",
       research=["charstats.acquisition.armor_specializations (rule)", "character_prep.compiler (whole acquired set)"]),
    _i("primary / secondary stats, max health, max power", "derived", "never", derived_path="/derived/stats",
       consumer=["Entities/Unit/StatSystem.cpp:198-345"], evidence="trinity-consumer", research=["charstats.character"]),
    _i("initial health / power / cooldown / charge state", "derived", "never", derived_path="/derived/initial_state",
       consumer=[f"{P}:2487-2493", f"{P}:497-502", "Spells/SpellHistory.cpp:147-179"], evidence="trinity-consumer",
       notes="full health at the prepared maximum is an encounter convention (structural-inference): no Trinity construction "
             "path refills health after gear is applied except GiveLevel"),
    # -- server ------------------------------------------------------------------------------------------------------
    _i("base primary stats (player_classlevelstats + player_racestats)", "server", "required",
       fixture_path="/server_inputs/base_stats", derived_path="/derived/stats",
       source_tables=["world: player_classlevelstats", "world: player_racestats"],
       consumer=["Globals/ObjectMgr.cpp:3807 LoadPlayerInfo (stats :4261-4356)"], evidence="world-db-fact",
       research=["world-db-corpora/player-base-stats.json", "agent F: character-prep-corpora/base-stat-gap.json"],
       notes="levels 81-90 absent: reported unresolved, never interpolated; Trinity copies level 80 (F)"),
    _i("base-stat gap fill opt-in (Trinity ObjectMgr fill rule)", "server", "optional",
       fixture_path="/server_inputs/base_stats/fill_rule", derived_path="/derived/stats/value/base_stats_evidence",
       consumer=["Globals/ObjectMgr.cpp:4336-4356 fill gaps"], evidence="trinity-consumer",
       research=["character_prep.basestats_tdb (agent F)"],
       notes="explicit; filled cells are trinity-consumer(fill-rule), Retail value unresolved (reported per fixture)"),
    _i("playercreateinfo (race x class pair exists)", "server", "required", source_tables=["world: playercreateinfo"],
       consumer=[f"{P}:396-402 Player::Create refuses a missing pair"], evidence="world-db-fact",
       research=["agent F: character-prep-corpora/server-inputs.json"]),
    _i("MaxPlayerLevel / Stats.Limits.* configuration", "server", "required", consumer=["World/World.cpp:750"],
       evidence="trinity-consumer", research=["agent F: server-inputs.json"]),
    _i("skill_tiers (SKILL_RANGE_RANK default skills)", "server", "optional", source_tables=["world: skill_tiers"],
       consumer=["Globals/ObjectMgr.cpp:9002-9023 GetSkillRangeType"], evidence="world-db-fact",
       notes="only matters for AutomaticSkillRank abilities of tiered default skills"),
    _i("conditions for SkillLineAbility AcquireMethod 4", "server", "optional", source_tables=["world: conditions",
                                                                                               "PlayerCondition"],
       consumer=[f"{P}:25409-25415"], evidence="unresolved",
       notes="per fixture the compiler lists the affected spells as unresolved. Spells without ShowFutureSpellPlayerConditionID "
             "(e.g. 54197, 50977) are learned unless a conditions row (source type 35) exists -- the pinned TDB has none for them; "
             "the others need PlayerCondition 83446 (not on NPE maps 2175/2236/2261/2369 + ContentTuning 958): host location"),
    _i("spell_* script / proc / pet-aura tables", "server", "never",
       evidence="world-db-fact", research=["dummy_semantics overlay", "procs overlay", "agent F: server-inputs.json"],
       notes="consumed by other tracks; not a preparation input"),
    # -- encounter ---------------------------------------------------------------------------------------------------
    _i("consumables: flasks, food, potions, augment runes", "encounter", "optional", evidence="structural-inference",
       notes="auras applied before the pull; could become an optional prep input, never part of the character"),
    _i("temporary weapon enchants / oils", "encounter", "optional", consumer=[f"{P}:13417 TEMP_ENCHANTMENT_SLOT"],
       evidence="trinity-consumer"),
    _i("raid buffs, bloodlust, external auras", "encounter", "optional", evidence="structural-inference"),
    _i("target, position, facing, pull timer", "encounter", "optional", evidence="structural-inference"),
    _i("PvP talent activation (PvP area)", "encounter", "optional",
       consumer=[f"{P}:27778 TogglePvpTalents", f"{P}:27856 IsAreaThatActivatesPvpTalents"], evidence="trinity-consumer"),
    _i("PvP item-level context", "encounter", "optional",
       consumer=["Entities/Player/Player.h:2754 IsUsingPvpItemLevels", f"{P}:30928-30929", "Entities/Item/Item.cpp:2270"],
       evidence="trinity-consumer"),
    _i("area item-level scaling (UnitData MinItemLevel / MaxItemLevel)", "encounter", "optional",
       consumer=[f"{P}:30768 UpdateItemLevelAreaBasedScaling"], evidence="trinity-consumer"),
    _i("saved auras of a logged-out character (character_aura)", "encounter", "optional",
       consumer=[f"{P}:18615 _LoadAuras (:18976-)"], evidence="trinity-consumer",
       notes="positive auras (flasks, food, buffs) survive logout with their remaining duration; state of the host session, "
             "not of the character build"),
    _i("saved health / power / cooldowns of a logged-out character", "encounter", "optional",
       consumer=[f"{P}:18690-18723", "Spells/SpellHistory.cpp:147-179"], evidence="trinity-consumer",
       notes="preparation starts from full health and ready cooldowns; a mid-fight snapshot is encounter state"),
    _i("RNG seed", "encounter", "optional", fixture_path="/options/rng", evidence="structural-inference",
       notes="no preparation step consumes randomness; reserved"),
    # -- excluded ----------------------------------------------------------------------------------------------------
    _i("appearance / customization", "excluded", "never", source_tables=["ChrCustomizationChoice"],
       consumer=[f"{P}:415 ValidateAppearance", f"{P}:471 SetCustomizations", f"{P}:18186-18217"],
       evidence="trinity-consumer", notes="display only"),
    _i("legacy azerite (Heart of Azeroth, empowered powers, essences)", "excluded", "never",
       source_tables=["AzeriteEmpoweredItem", "AzeritePower"],
       consumer=[f"{P}:8965 ApplyAllAzeriteItemMods", f"{P}:8492-8514 ApplyAzeritePowers (empowered powers need "
                 "ITEM_ID_HEART_OF_AZEROTH 158075 equipped, :8509; AzeriteItem.h:23)"],
       evidence="legacy-only",
       notes="14 current-season M+ corpus items are AzeriteEmpoweredItem rows (legacy dungeon loot) but their powers "
             "stay inert without the equipped heart; reopen if a fixture equips item 158075"),
    _i("transmog, titles, name, guid, mounts / pet collections", "excluded", "never", evidence="structural-inference"),
    _i("rewarded quests / achievements / currencies", "excluded", "never",
       consumer=["Spells/TraitMgr.cpp:605-609 trait conditions", "Spells/TraitMgr.cpp:446-450 currency sources",
                 f"{P}:18181 achievements, :18258 currencies, :18632-18640 quests"],
       evidence="db2-fact",
       notes="no class-tree TraitCond uses QuestID / AchievementID / account elements, and every quest-gated "
             "TraitCurrencySource of the class currencies has Amount 0 (census in the report); reopen if that changes. "
             "Quest-reward spells (LearnQuestRewardedSpells, Player.cpp:25318) reach the character only as learned_spells"),
    _i("reputation (item requirements)", "excluded", "never", consumer=[f"{P}:18651 ReputationMgr::LoadFromDB (before inventory)",
                                                                        f"{P}:11225-11226 CanUseItem MinFactionID"],
       evidence="db2-fact",
       notes="no current-gear corpus item has ItemSparse.MinFactionID; the compiler reports any such item unresolved"),
    _i("action bars, equipment sets, transmog outfits, mail, social, garrison, artifacts", "excluded", "never",
       consumer=[f"{P}:18624-18630", f"{P}:18666 StartLoadingActionButtons", f"{P}:18655 artifacts (legacy)",
                 f"{P}:18809 garrison"], evidence="structural-inference",
       notes="no prepared combat value; artifacts are legacy-only like azerite"),
    _i("professions and profession gear", "excluded", "never", consumer=[f"{P}:9289-9290 INVTYPE_PROFESSION_*"],
       evidence="structural-inference"),
]


def input_inventory_document() -> dict[str, Any]:
    for row in INPUT_INVENTORY:
        if row["kind"] not in INPUT_KINDS or row["required"] not in REQUIREMENT or row["evidence_class"] not in EVIDENCE_CLASSES:
            raise SourceError(f"inventory row {row['name']!r} has an invalid classification")
    counts: dict[str, int] = {}
    for row in INPUT_INVENTORY:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT, "world_database": TDB_RELEASE,
                       "generator": "python3 character_prep.py inventory", "coordinates_root": "src/server/game"},
        "kinds": {"identity": "must be supplied", "derived": "never asked for", "server": "world-DB / config (agent F)",
                  "encounter": "fight or host state", "excluded": "not combat preparation"},
        "counts": dict(sorted(counts.items())),
        "inputs": INPUT_INVENTORY,
    }


# ---------------------------------------------------------------------------
# initial state (C3)
# ---------------------------------------------------------------------------

INITIAL_STATE_CLASSES: list[dict[str, Any]] = [
    {"class": "immutable-facts", "items": ["race / class / spec / level", "equipped items and their resolved stats",
                                           "trait config", "server base stats"],
     "consumer": [f"{P}:17925 LoadFromDB", f"{P}:2323 InitStatsForLevel"], "evidence_class": "trinity-consumer",
     "derived_path": "/derived/identity"},
    {"class": "compiled-program", "items": ["reachable spell set: spec spells, mastery, applied traits, default-skill spells, "
                                            "gear roots, PvP talents (selected vs possible)"],
     "consumer": [f"{P}:30631-30648", f"{P}:29380-29409", f"{P}:25259-25441", f"{P}:8315 ApplyItemEquipSpell"],
     "evidence_class": "trinity-consumer", "derived_path": "/derived/spells",
     "notes": "selected = applied config entries; possible = the whole class tree (TraitMgr::GetTreesForConfig)"},
    {"class": "mutable-combat-state", "items": ["health", "power per power type"],
     "rule": {"health": "full at the prepared maximum by convention (structural-inference): InitStatsForLevel SetFullHealth "
                        "(Player.cpp:2487) and Create (:497-498) fill before gear is applied, SetMaxHealth never refills, login "
                        "restores the saved value (:18708)",
              "mana/energy/focus": "full (Player.cpp:2488-2492)", "rage": "unchanged, clamped only if above max (:2490-2491)",
              "runic power": "0 (:2493)", "lunar power": "0 on login (:18725)",
              "create": "PowerType flag SetToMaxOnInitialLogIn -> full (Player.cpp:500-502)",
              "level-up": "PowerType flag SetToMaxOnLevelUp -> full (Player.cpp:2254-2257)",
              "login": "saved values clamped to max (Player.cpp:18708-18723)"},
     "consumer": ["Entities/Unit/StatSystem.cpp:90-99 GetCreatePowerValue", "Entities/Unit/StatSystem.cpp:331-345 UpdateMaxPower"],
     "evidence_class": "trinity-consumer", "derived_path": "/derived/initial_state/value/powers"},
    {"class": "active-at-start-passives", "items": ["learn -> cast passive", "shapeshift gate", "equipment gate (armour spec)",
                                                    "caster aura state gate", "set thresholds", "marker Dummy auras"],
     "rule": {"cast on learn": f"{P}:2792-2794 / :2916-2929 -> HandlePassiveSpellLearn :3079-3104",
              "shapeshift": "Stances with no form and no ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED -> not cast (:3083-3085)",
              "equipment": "EquippedItemClass >= 0 with an aura effect -> AddAura only if HasItemFitToSpellRequirements (:3089-3099)",
              "caster aura state": "not cast on learn unless the state holds (:3102-3103); health states are set on the first "
                                   "Unit::Update (Unit.cpp:474-483) and ModifyAuraState casts the passive then (Unit.cpp:6078-6098)",
              "sets": "Player::ApplyEquipSpell via set thresholds (gearing.sets)"},
     "evidence_class": "trinity-consumer", "derived_path": "/derived/initial_state/value/auras"},
    {"class": "cooldowns-and-charges", "items": ["every cooldown ready", "every charge category full"],
     "rule": {"cooldowns": "SpellHistory::LoadFromDB replays only persisted rows (Spells/SpellHistory.cpp:147-179); an in-game "
                           "EquipItem starts a 30 s cooldown on on-use item spells (Player.cpp:25146-25186), QuickEquipItem at "
                           "load does not",
              "charges": "GetMaxCharges = SpellCategory.MaxCharges + SPELL_AURA_MOD_MAX_CHARGES (Spells/SpellHistory.cpp:964-973); "
                         "the compiler adds the aura base points of passives active at preparation"},
     "evidence_class": "trinity-consumer", "derived_path": "/derived/initial_state/value/charges"},
    {"class": "weapon-swing-state", "items": ["base attack time per attack type", "attack timers"],
     "rule": {"base": "InitStatsForLevel BASE_ATTACK_TIME for every type (Player.cpp:2394-2395); weapon delay on equip (:5396)",
              "timers": "m_attackTimer = {} (Entities/Unit/Unit.cpp:326): ready"},
     "owner": "Track B (weapon_combat.swing)", "evidence_class": "trinity-consumer", "derived_path": "/derived/weapon"},
    {"class": "pet-state", "items": ["summoned / active pet, pet stats and spells"], "owner": "Track A (controlled_units)",
     "evidence_class": "unresolved", "derived_path": "/derived/controlled_units",
     "notes": "hook degrades to unresolved until controlled_units exposes CHARACTER_PREP_HOOK"},
    {"class": "rng-identity", "items": ["seed"], "evidence_class": "structural-inference", "derived_path": None,
     "notes": "not needed for preparation; options.rng reserved and must be null"},
    {"class": "encounter-only", "items": ["target", "position", "raid buffs", "consumables", "bloodlust", "PvP item levels",
                                          "PvP talent activation", "area item-level scaling"],
     "evidence_class": "structural-inference", "derived_path": "/derived/initial_state/value/not_character_preparation",
     "notes": "item context from difficulty is NOT here: it is ItemContext inside each equipment entry (an identity input)"},
]


def power_facts(snapshot: Any) -> dict[str, Any]:
    from .compiler import POWER_NAMES, POWER_TYPE_FLAG_SET_TO_MAX_ON_INITIAL_LOGIN, POWER_TYPE_FLAG_SET_TO_MAX_ON_LEVEL_UP
    rows = []
    for enum, pt in sorted(snapshot.power_types.items()):
        rows.append({"power_type": enum, "name": POWER_NAMES.get(enum, str(enum)), "min": pt["min"], "max_base": pt["max_base"],
                     "center": pt["center"], "default": pt["default"], "flags": pt["flags"],
                     "set_to_max_on_initial_login": bool(pt["flags"] & POWER_TYPE_FLAG_SET_TO_MAX_ON_INITIAL_LOGIN),
                     "set_to_max_on_level_up": bool(pt["flags"] & POWER_TYPE_FLAG_SET_TO_MAX_ON_LEVEL_UP)})
    return {"power_types": rows,
            "class_powers": {str(c): p for c, p in sorted(snapshot.class_powers.items())},
            "source": "PowerType.db2, ChrClassesXPowerTypes.db2 (db2-fact)"}


def initial_state_document(snapshot: Any | None = None) -> dict[str, Any]:
    from .compiler import Snapshot
    snap = snapshot or Snapshot()
    return {
        "provenance": {"snapshot_build": SNAPSHOT_BUILD, "trinitycore_commit": TRINITY_COMMIT,
                       "generator": "python3 character_prep.py initial-state", "coordinates_root": "src/server/game"},
        "classes": INITIAL_STATE_CLASSES,
        "powers": power_facts(snap),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_inventory(args: Any) -> int:
    doc = input_inventory_document()
    if args.output:
        Path(args.output).write_text(canonical_json(doc), encoding="utf-8")
        print(f"wrote {args.output}")
        return 0
    if args.json:
        print(canonical_json(doc), end="")
        return 0
    for row in doc["inputs"]:
        print(f"{row['kind']:9} {row['required']:8} {row['evidence_class']:21} {row['name']}")
    print(f"counts: {doc['counts']}")
    return 0


def cmd_initial_state(args: Any) -> int:
    if args.fixture is None:
        doc = initial_state_document()
        if args.output:
            Path(args.output).write_text(canonical_json(doc), encoding="utf-8")
            print(f"wrote {args.output}")
        else:
            print(canonical_json(doc), end="")
        return 0
    from .compiler import Compiler, Snapshot
    from .fixture import Fixture
    compiled = Compiler(Snapshot()).compile(
        Fixture.from_file(args.fixture), world_db_corpus=Path(args.world_db_corpus) if args.world_db_corpus else None)
    out = {"fixture": args.fixture, "validation": compiled["validation"],
           "initial_state": compiled["derived"].get("initial_state"),
           "unresolved": [u for u in compiled["unresolved"] if u["item"].startswith("/derived/initial_state")
                          or u["item"] == "/derived/stats"]}
    print(json.dumps(out, indent=1) if args.json else canonical_json(out), end="")
    return 0 if compiled["validation"]["ok"] else 1


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("inventory", help="the canonical input inventory (C1)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_inventory)
    s = subparsers.add_parser("initial-state", help="initial-state inventory (C3), or one fixture's compiled initial state")
    s.add_argument("--fixture", default=None)
    s.add_argument("--world-db-corpus", default=None)
    s.add_argument("--json", action="store_true")
    s.add_argument("--output", default=None)
    s.set_defaults(func=cmd_initial_state)


def write_corpora(snapshot: Any | None = None) -> list[Path]:
    CORPORA.mkdir(parents=True, exist_ok=True)
    out = []
    for name, doc in (("input-inventory.json", input_inventory_document()),
                      ("initial-state.json", initial_state_document(snapshot))):
        path = CORPORA / name
        path.write_text(canonical_json(doc), encoding="utf-8")
        out.append(path)
    return out
