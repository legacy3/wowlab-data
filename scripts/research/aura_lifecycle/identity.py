"""Track A -- aura identity: when two applications are the same object.

Separates two things Trinity conflates in storage:

* **observable identity** -- given an existing aura and a new application, is the
  result the *same* mutable object (refresh / stack), a *second* object that coexists,
  a *replacement* (old object removed, new one created), or a *suppressed* new object;
* **Trinity storage** -- ``Unit::m_ownedAuras`` (multimap keyed by SpellId),
  ``Unit::m_appliedAuras`` (multimap keyed by SpellId), ``AuraApplication::_slot``
  (client visible slot) and ``AuraKey`` (caster, item, spell, effect mask; only used
  for DB save/load).  None of these is itself an identity rule.

The model is a pure port of the decision chain

    Aura::TryRefreshStackOrCreate          SpellAuras.cpp:350
      Unit::_TryStackingOrRefreshingExistingAura  Unit.cpp:3386  (lookup key)
        Unit::GetOwnedAura                 Unit.cpp:3811
      Aura::Create -> UnitAura ctor -> Unit::_AddAura  Unit.cpp:3449
        Unit::_RemoveNoStackAurasDueToAura Unit.cpp:3714
          Unit::IsHighestExclusiveAura     Unit.cpp:14284
          Aura::CanStackWith               SpellAuras.cpp:1633
            SpellMgr::CheckSpellGroupStackRules  SpellMgr.cpp:447

over :class:`Props` (the ``SpellInfo`` members the chain reads) built from the
snapshot plus the pinned Trinity load-time corrections and TDB overlays.
Everything runtime-dependent (vehicle seats, spell mods, scripts) fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from procs.enums import AREA_AURA_EFFECTS, attr, aura, aura_name, effect, effect_name

from . import FailClosed

# ---------------------------------------------------------------------------
# Constants (all verified at the pinned Trinity revision)
# ---------------------------------------------------------------------------

APPLY_AURA = effect("APPLY_AURA")
APPLY_AURA_ON_PET = effect("APPLY_AURA_ON_PET")
PERSISTENT_AREA_AURA = effect("PERSISTENT_AREA_AURA")
ENCHANT_EFFECTS = frozenset(effect(n) for n in ("ENCHANT_ITEM", "ENCHANT_ITEM_TEMPORARY",
                                                 "ENCHANT_ITEM_PRISMATIC", "ENCHANT_HELD_ITEM"))
SKILL_ENCHANTING = 333
ITEM_ENCHANTMENT_TYPE_COMBAT_SPELL = 1

#: SpellInfo.cpp:1805 hardcoded multislot ids (Power Spark, Fel Flak Fire, Incanter's Absorption).
MULTISLOT_IDS = (55849, 40075, 44413)

#: SpellMgr.cpp LoadSpellInfoCorrections sites that change an identity input.
DOT_STACKING_CORRECTIONS = {37408: "SpellMgr.cpp:3708-3711", 48278: "SpellMgr.cpp:3846-3849",
                            65584: "SpellMgr.cpp:4083-4090", 64381: "SpellMgr.cpp:4083-4090",
                            71169: "SpellMgr.cpp:4241-4244", 70602: "SpellMgr.cpp:4320-4323"}
PASSIVE_CORRECTIONS = {59630: "SpellMgr.cpp:3839-3843"}
#: SpellMgr.cpp:5301-5302 ``ActiveIconFileDataId == 135754`` (flight) -> SPELL_ATTR0_PASSIVE.
PASSIVE_ICON = 135754

SPELL_ATTR0_CU_ENCHANT_PROC = 0x00000001          # SpellInfo.h:144
AURA_POINTS_STACK = 0x00000200                    # DBCEnums.h:2412 SpellEffectAttributes::AuraPointsStack
AURA_INTERRUPT_STANDING = 0x00040000              # SpellDefines.h:98 SpellAuraInterruptFlags::Standing

# SpellFamilyNames (SharedDefines.h)
FAMILY = {"GENERIC": 0, "MAGE": 3, "WARRIOR": 4, "WARLOCK": 5, "PRIEST": 6, "DRUID": 7, "ROGUE": 8,
          "HUNTER": 9, "PALADIN": 10, "SHAMAN": 11, "POTION": 13, "DEATHKNIGHT": 15}
DISPEL_CURSE, DISPEL_POISON = 2, 4                # SharedDefines.h DispelType

# SpellGroupStackRule (SpellMgr.h:343)
STACK_RULES = ("DEFAULT", "EXCLUSIVE", "EXCLUSIVE_FROM_SAME_CASTER", "EXCLUSIVE_SAME_EFFECT", "EXCLUSIVE_HIGHEST")
SPELL_GROUP_CORE_RANGE_MAX = 5                    # SpellMgr.h:318
SPELL_GROUP_DB_RANGE_MIN = 1000                   # SpellMgr.h:333

#: Aura::CanStackWith lambda ``hasPeriodicNonAreaEffect`` (SpellAuras.cpp:1702-1731).
PERIODIC_STACK_AURAS = frozenset(aura(n) for n in (
    "PERIODIC_DAMAGE", "PERIODIC_WEAPON_PERCENT_DAMAGE", "PERIODIC_DUMMY", "PERIODIC_HEAL",
    "PERIODIC_TRIGGER_SPELL", "PERIODIC_ENERGIZE", "PERIODIC_MANA_LEECH", "PERIODIC_LEECH",
    "POWER_BURN", "OBS_MOD_POWER", "OBS_MOD_HEALTH", "PERIODIC_TRIGGER_SPELL_WITH_VALUE",
    "PERIODIC_DAMAGE_PERCENT"))
CONTROL_VEHICLE = aura("CONTROL_VEHICLE")
CONFIRMATION_PROMPTS = frozenset(aura(n) for n in ("SHOW_CONFIRMATION_PROMPT",
                                                   "SHOW_CONFIRMATION_PROMPT_WITH_DIFFICULTY"))
CHARM_AURAS = frozenset(aura(n) for n in ("MOD_CHARM", "MOD_POSSESS_PET", "MOD_POSSESS", "AOE_CHARM"))
TRACK_AURAS = frozenset(aura(n) for n in ("TRACK_CREATURES", "TRACK_RESOURCES", "TRACK_STEALTHED"))
FOOD_AURAS = frozenset(aura(n) for n in ("MOD_REGEN", "OBS_MOD_HEALTH"))
DRINK_AURAS = frozenset(aura(n) for n in ("MOD_POWER_REGEN", "OBS_MOD_POWER"))

TARGET_UNIT_CASTER = 1

# ---------------------------------------------------------------------------
# SpellInfo members the identity chain reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Eff:
    index: int
    effect: int
    aura: int
    trigger: int
    target_a: int
    target_b: int
    attributes: int

    @property
    def is_area_aura(self) -> bool:
        """Mirrors: SpellInfo.cpp:480 ``SpellEffectInfo::IsAreaAuraEffect``."""
        return self.effect in AREA_AURA_EFFECTS

    @property
    def is_unit_owned_aura(self) -> bool:
        """Mirrors: SpellInfo.cpp:494 ``IsUnitOwnedAuraEffect`` (no ``ApplyAuraName != 0`` test)."""
        return self.is_area_aura or self.effect in (APPLY_AURA, APPLY_AURA_ON_PET)

    @property
    def is_targeting_area(self) -> bool:
        """Mirrors: SpellInfo.cpp:475 ``IsTargetingArea`` (TargetA.IsArea() || TargetB.IsArea())."""
        from targeting.selectors import info
        return any(t and info(t).is_area for t in (self.target_a, self.target_b))

    def label(self) -> str:
        return effect_name(self.effect) + (f"/{aura_name(self.aura)}" if self.aura else "")


@dataclass(frozen=True)
class Props:
    """The identity-relevant ``SpellInfo`` of one spell (DIFFICULTY_NONE, after load-time fixes)."""

    spell: int
    first_rank: int
    rank: int
    family: int
    family_flags: int
    stack_amount: int
    attributes: tuple[int, ...]
    cu: int
    dispel: int
    aura_interrupt0: int
    effects: tuple[Eff, ...]
    max_targets: int
    corrections: tuple[str, ...] = ()
    name: str = field(default="", compare=False)

    def has(self, name: str) -> bool:
        word, bit = attr(name)
        return bool(self.attributes[word] & bit)

    # -- SpellInfo predicates ----------------------------------------------
    @property
    def passive(self) -> bool:
        """Mirrors: SpellInfo.cpp:1753 ``IsPassive``."""
        return self.has("SPELL_ATTR0_PASSIVE")

    @property
    def channeled(self) -> bool:
        """Mirrors: SpellInfo.cpp:1889 ``IsChanneled``."""
        return self.has("SPELL_ATTR1_IS_CHANNELLED") or self.has("SPELL_ATTR1_IS_SELF_CHANNELLED")

    @property
    def dot_stacking_rule(self) -> bool:
        return self.has("SPELL_ATTR3_DOT_STACKING_RULE")

    @property
    def multislot(self) -> bool:
        """Mirrors: SpellInfo.cpp:1803 ``IsMultiSlotAura``."""
        return self.passive or self.spell in MULTISLOT_IDS

    @property
    def one_slot_any_caster(self) -> bool:
        """Mirrors: SpellInfo.cpp:1808 ``IsStackableOnOneSlotWithDifferentCasters``."""
        return self.stack_amount > 1 and not self.channeled and not self.dot_stacking_rule

    @property
    def passive_stackable_with_ranks(self) -> bool:
        """Mirrors: SpellInfo.cpp:1798 ``IsPassiveStackableWithRanks``."""
        return self.passive and not any(e.effect == APPLY_AURA for e in self.effects)

    @property
    def single_target(self) -> bool:
        """Mirrors: SpellInfo.cpp:2052 ``IsSingleTarget`` (SPELL_ATTR5_LIMIT_N)."""
        return self.has("SPELL_ATTR5_LIMIT_N")

    @property
    def enchant_proc(self) -> bool:
        return bool(self.cu & SPELL_ATTR0_CU_ENCHANT_PROC)

    def has_aura(self, aura_type: int) -> bool:
        """Mirrors: ``SpellInfo::HasAura`` (effect IsAura with that ApplyAuraName)."""
        return any((e.is_unit_owned_aura or e.effect == PERSISTENT_AREA_AURA) and e.aura == aura_type
                   for e in self.effects if e.aura)

    def unit_aura_mask(self) -> int:
        """Mirrors: SpellAuras.cpp:325 ``BuildEffectMaskForOwner`` (TYPEID_UNIT/PLAYER branch)."""
        return sum(1 << e.index for e in self.effects if e.is_unit_owned_aura)

    def dynobj_aura_mask(self) -> int:
        """Mirrors: SpellAuras.cpp:325 ``BuildEffectMaskForOwner`` (TYPEID_DYNAMICOBJECT branch)."""
        return sum(1 << e.index for e in self.effects if e.effect == PERSISTENT_AREA_AURA)

    @cached_property
    def spell_specific(self) -> str:
        """Mirrors: SpellInfo.cpp:2803 ``SpellInfo::_LoadSpellSpecific``."""
        f, ff = self.family, self.family_flags
        w = [(ff >> (32 * i)) & 0xFFFFFFFF for i in range(4)]
        if f == FAMILY["GENERIC"]:
            if self.aura_interrupt0 & AURA_INTERRUPT_STANDING:
                auras = {e.aura for e in self.effects if (e.is_unit_owned_aura or e.effect == PERSISTENT_AREA_AURA) and e.aura}
                food, drink = bool(auras & FOOD_AURAS), bool(auras & DRINK_AURAS)
                if food and drink:
                    return "FOOD_AND_DRINK"
                if food:
                    return "FOOD"
                if drink:
                    return "DRINK"
            elif self.first_rank in (8118, 8099, 8112, 8096, 8115, 8091):
                return "SCROLL"
        elif f == FAMILY["MAGE"]:
            if w[0] & 0x12040000:
                return "MAGE_ARMOR"
            if w[0] & 0x400:
                return "MAGE_ARCANE_BRILLANCE"
            e0 = next((e for e in self.effects if e.index == 0), None)
            if (w[0] & 0x1000000) and e0 is not None and e0.aura == aura("MOD_CONFUSE") and \
                    (e0.is_unit_owned_aura or e0.effect == PERSISTENT_AREA_AURA):
                return "MAGE_POLYMORPH"
        elif f == FAMILY["WARRIOR"]:
            if self.spell == 12292:
                return "WARRIOR_ENRAGE"
        elif f == FAMILY["WARLOCK"]:
            if self.spell in (603, 980, 80240):
                return "BANE"
            if self.dispel == DISPEL_CURSE:
                return "CURSE"
            if w[1] & 0x20000020 or w[2] & 0x00000010:
                return "WARLOCK_ARMOR"
        elif f == FAMILY["PRIEST"]:
            if w[0] & 0x20:
                return "PRIEST_DIVINE_SPIRIT"
        elif f == FAMILY["HUNTER"]:
            if self.dispel == DISPEL_POISON:
                return "STING"
            if w[0] & 0x00200000 or w[2] & 0x00001010:
                return "ASPECT"
        elif f == FAMILY["PALADIN"]:
            if w[1] & 0xA2000800:
                return "SEAL"
            if w[0] & 0x00002190:
                return "HAND"
            if self.spell in (465, 32223, 183435, 317920):
                return "AURA"
        elif f == FAMILY["SHAMAN"]:
            if w[1] & 0x420 or w[0] & 0x00000400 or self.spell == 23552:
                return "ELEMENTAL_SHIELD"
        elif f == FAMILY["DEATHKNIGHT"]:
            if self.spell in (48266, 48263, 48265):
                return "PRESENCE"
        for e in self.effects:
            if e.effect == APPLY_AURA:
                if e.aura in CHARM_AURAS:
                    return "CHARM"
                if e.aura in TRACK_AURAS:
                    if e.aura == aura("TRACK_CREATURES") and self.spell == 30645:
                        return "NORMAL"
                    return "TRACKER"
        return "NORMAL"


_SPECIFIC_EXCLUSIVE_SAME = frozenset({"WARLOCK_ARMOR", "MAGE_ARMOR", "ELEMENTAL_SHIELD", "MAGE_POLYMORPH",
                                      "PRESENCE", "CHARM", "SCROLL", "WARRIOR_ENRAGE",
                                      "MAGE_ARCANE_BRILLANCE", "PRIEST_DIVINE_SPIRIT"})
_SPECIFIC_PER_CASTER = frozenset({"SEAL", "HAND", "AURA", "STING", "CURSE", "BANE", "ASPECT"})


def exclusive_by_specific(a: Props, b: Props) -> bool:
    """Mirrors: SpellInfo.cpp:2061 ``IsAuraExclusiveBySpecificWith``."""
    s1, s2 = a.spell_specific, b.spell_specific
    if s1 in _SPECIFIC_EXCLUSIVE_SAME:
        return s1 == s2
    if s1 == "FOOD":
        return s2 in ("FOOD", "FOOD_AND_DRINK")
    if s1 == "DRINK":
        return s2 in ("DRINK", "FOOD_AND_DRINK")
    if s1 == "FOOD_AND_DRINK":
        return s2 in ("FOOD", "DRINK", "FOOD_AND_DRINK")
    return False


def exclusive_by_specific_per_caster(a: Props, b: Props) -> bool:
    """Mirrors: SpellInfo.cpp:2093 ``IsAuraExclusiveBySpecificPerCasterWith``."""
    return a.spell_specific in _SPECIFIC_PER_CASTER and a.spell_specific == b.spell_specific


def has_periodic_non_area_effect(p: Props) -> bool:
    """Mirrors: SpellAuras.cpp:1702-1731 lambda ``hasPeriodicNonAreaEffect``.

    Iterates **every** effect's ``ApplyAuraName`` (not only aura effects) and returns at
    the first listed periodic aura type: ``false`` if that effect targets an area.
    """
    for e in p.effects:
        if e.aura in PERIODIC_STACK_AURAS:
            return not e.is_targeting_area
    return False


# ---------------------------------------------------------------------------
# Spell groups (spell_group / spell_group_stack_rules from the TDB overlay)
# ---------------------------------------------------------------------------

class SpellGroups:
    """Mirrors: SpellMgr.cpp:1266 ``LoadSpellGroups`` / 1345 ``LoadSpellGroupStackRules`` / 447 ``CheckSpellGroupStackRules``."""

    def __init__(self, group_rows: list[tuple[int, int]], rule_rows: list[tuple[int, int]],
                 exists=lambda s: True, rank=lambda s: 1) -> None:
        self.dropped: list[dict[str, Any]] = []
        groups: set[int] = set()
        raw: list[tuple[int, int]] = []
        for gid, sid in group_rows:
            if gid <= SPELL_GROUP_DB_RANGE_MIN and gid >= SPELL_GROUP_CORE_RANGE_MAX:
                self.dropped.append({"group": gid, "spell": sid, "why": "group id in reserved range"})
                continue
            groups.add(gid)
            raw.append((gid, sid))
        self.group_spell: dict[int, list[int]] = {}
        for gid, sid in raw:
            if sid < 0 and abs(sid) not in groups:
                self.dropped.append({"group": gid, "spell": sid, "why": "sub-group does not exist"})
                continue
            if sid > 0 and not exists(sid):
                self.dropped.append({"group": gid, "spell": sid, "why": "spell not in snapshot"})
                continue
            if sid > 0 and rank(sid) > 1:
                self.dropped.append({"group": gid, "spell": sid, "why": "rank > 1"})
                continue
            self.group_spell.setdefault(gid, []).append(sid)
        self.spell_groups: dict[int, set[int]] = {}
        for gid in sorted(groups):
            for sid in self._flatten(gid, set()):
                self.spell_groups.setdefault(sid, set()).add(gid)
        self.rules: dict[int, int] = {}
        for gid, rule in rule_rows:
            if rule >= len(STACK_RULES) or not self.group_spell.get(gid):
                self.dropped.append({"group": gid, "rule": rule, "why": "invalid rule or empty group"})
                continue
            self.rules[gid] = rule

    def _flatten(self, gid: int, used: set[int]) -> set[int]:
        """Mirrors: SpellMgr.cpp:391 ``GetSetOfSpellsInSpellGroup``."""
        if gid in used:
            return set()
        used.add(gid)
        out: set[int] = set()
        for sid in self.group_spell.get(gid, ()):
            out |= self._flatten(-sid, used) if sid < 0 else {sid}
        return out

    def member(self, spell: int, gid: int) -> bool:
        return gid in self.spell_groups.get(spell, ())

    def rule(self, a_first: int, b_first: int) -> str:
        """Mirrors: SpellMgr.cpp:447 ``CheckSpellGroupStackRules`` (arguments are first ranks)."""
        common: set[int] = set()
        for gid in self.spell_groups.get(a_first, ()):
            if not self.member(b_first, gid):
                continue
            add = True
            for sid in self.group_spell.get(gid, ()):
                if sid < 0 and self.member(a_first, -sid) and self.member(b_first, -sid):
                    add = False
                    break
            if add:
                common.add(gid)
        rule = 0
        for gid in sorted(common):
            if gid in self.rules:
                rule = self.rules[gid]
            if rule:
                break
        return STACK_RULES[rule]

    @classmethod
    def from_overlay(cls, ctx) -> SpellGroups:
        world = ctx.bundle.world
        g = [(int(r["id"]), int(r["spell_id"])) for r in world.table("spell_group").dicts()]
        r = [(int(x["group_id"]), int(x["stack_rule"])) for x in world.table("spell_group_stack_rules").dicts()]
        cat = ctx.catalog

        def rank(s: int) -> int:
            n = 1
            while s in cat._ranks_prev:
                s = cat._ranks_prev[s]
                n += 1
            return n
        return cls(g, r, exists=cat.exists, rank=rank)


# ---------------------------------------------------------------------------
# Building Props from the snapshot
# ---------------------------------------------------------------------------

class PropsBuilder:
    """Builds :class:`Props` from an aura-lifecycle context (snapshot + overlays + corrections)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._cache: dict[int, Props] = {}

    @cached_property
    def enchant_proc_spells(self) -> dict[int, list[int]]:
        """Mirrors: SpellMgr.cpp:3122-3150 (``SPELL_ATTR0_CU_ENCHANT_PROC`` derivation).

        Enchanting-skill (SkillLine 333) spells with an ENCHANT_ITEM* effect whose
        ``SpellItemEnchantment`` has a COMBAT_SPELL slot: the slot's spell gets the CU bit
        unless it has a PROC_TRIGGER_SPELL aura.  Returns proc spell -> enchanting spells.
        """
        src = self.ctx.bundle.source
        enchanting = {s for s, sl in src.project("SkillLineAbility", ("Spell", "SkillLine")) if sl == SKILL_ENCHANTING}
        cols = ("ID", "Effect_0", "Effect_1", "Effect_2", "EffectArg_0", "EffectArg_1", "EffectArg_2")
        enchants = {r[0]: r for r in src.project("SpellItemEnchantment", cols)}
        out: dict[int, list[int]] = {}
        proc_trigger = aura("PROC_TRIGGER_SPELL")
        for spell in sorted(enchanting):
            for e in self.ctx.data.effects(spell):
                if e["Effect"] not in ENCHANT_EFFECTS:
                    continue
                row = enchants.get(e["EffectMiscValue_0"])
                if row is None:
                    continue
                for s in range(3):
                    if row[1 + s] != ITEM_ENCHANTMENT_TYPE_COMBAT_SPELL:
                        continue
                    proc = row[4 + s]
                    info = self.ctx.catalog.get(proc)
                    if info is None or info.has_aura(proc_trigger):
                        continue
                    out.setdefault(proc, []).append(spell)
        return out

    def __call__(self, spell: int) -> Props:
        if spell in self._cache:
            return self._cache[spell]
        ctx = self.ctx
        info = ctx.catalog.get(spell)
        if info is None:
            raise FailClosed(f"spell {spell} has no DIFFICULTY_NONE SpellInfo in the snapshot")
        attrs = [int(a) & 0xFFFFFFFF for a in info.attributes]
        corrections: list[str] = []
        if spell in DOT_STACKING_CORRECTIONS:
            w, b = attr("SPELL_ATTR3_DOT_STACKING_RULE")
            attrs[w] |= b
            corrections.append(f"ATTR3_DOT_STACKING_RULE |= ({DOT_STACKING_CORRECTIONS[spell]})")
        misc = ctx.data.row("SpellMisc", spell) or {}
        if spell in PASSIVE_CORRECTIONS or misc.get("ActiveIconFileDataID") == PASSIVE_ICON:
            w, b = attr("SPELL_ATTR0_PASSIVE")
            attrs[w] |= b
            corrections.append("ATTR0_PASSIVE |= (" + PASSIVE_CORRECTIONS.get(spell, "SpellMgr.cpp:5301-5302 flight icon") + ")")
        cats = ctx.data.row("SpellCategories", spell) or {}
        interrupts = ctx.data.row("SpellInterrupts", spell) or {}
        restr = ctx.targeting._restrictions.get((spell, 0)) or {}
        max_targets = int(restr.get("MaxTargets", 0) or 0)
        effects = []
        for e in ctx.data.effects(spell):
            ta, tb = e["ImplicitTarget_0"], e["ImplicitTarget_1"]
            ef = Eff(e["EffectIndex"], e["Effect"], e["EffectAura"], e["EffectTriggerSpell"], ta, tb,
                     int(e["EffectAttributes"]) & 0xFFFFFFFF)
            if ef.is_area_aura and ef.is_targeting_area:
                # SpellMgr.cpp:5286-5291: area auras may not target area -> (TARGET_UNIT_CASTER, 0)
                ef = Eff(ef.index, ef.effect, ef.aura, ef.trigger, TARGET_UNIT_CASTER, 0, ef.attributes)
                corrections.append(f"effect {ef.index} targets -> UNIT_CASTER (SpellMgr.cpp:5286-5291)")
            effects.append(ef)
        cu = int(info.attributes_cu)
        if spell in self.enchant_proc_spells:
            cu |= SPELL_ATTR0_CU_ENCHANT_PROC
        p = Props(spell=spell, first_rank=ctx.catalog.first_rank(spell), rank=self._rank(spell),
                  family=info.family, family_flags=info.family_flags, stack_amount=int(info.stack_amount),
                  attributes=tuple(attrs), cu=cu, dispel=int(cats.get("DispelType", 0) or 0),
                  aura_interrupt0=int(interrupts.get("AuraInterruptFlags_0", 0) or 0) & 0xFFFFFFFF,
                  effects=tuple(effects), max_targets=max_targets if max_targets or not
                  (attrs[attr("SPELL_ATTR5_LIMIT_N")[0]] & attr("SPELL_ATTR5_LIMIT_N")[1]) else 1,
                  corrections=tuple(corrections), name=ctx.name(spell))
        self._cache[spell] = p
        return p

    def _rank(self, spell: int) -> int:
        n = 1
        prev = self.ctx.catalog._ranks_prev
        while spell in prev:
            spell = prev[spell]
            n += 1
        return n


# ---------------------------------------------------------------------------
# Aura objects and the decision chain
# ---------------------------------------------------------------------------

@dataclass
class AuraObj:
    """One Trinity ``Aura`` as far as identity is concerned."""

    props: Props
    caster: str
    owner: str
    kind: str = "unit"                 # "unit" | "dynobj"
    cast_item: str = ""
    effect_mask: int | None = None     # None -> BuildEffectMaskForOwner(all)
    label: str = ""
    removed: bool = False
    stacks: int = 1
    base_from: str = ""                # whose base points the effects carry (latest applier on refresh)
    events: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.effect_mask is None:
            self.effect_mask = self.props.unit_aura_mask() if self.kind == "unit" else self.props.dynobj_aura_mask()
        if not self.base_from:
            self.base_from = self.caster

    @property
    def spell(self) -> int:
        return self.props.spell

    def aura_types(self) -> set[int]:
        return {e.aura for e in self.props.effects if self.effect_mask & (1 << e.index)}

    def is_area(self) -> bool:
        """Mirrors: SpellAuras.cpp:1141 ``Aura::IsArea``."""
        return any(e.is_area_aura for e in self.props.effects if self.effect_mask & (1 << e.index))


def can_stack_with(new: AuraObj, existing: AuraObj, groups: SpellGroups, *,
                   vehicle_seats: int | None = None, trace: list[str] | None = None) -> bool:
    """Mirrors: SpellAuras.cpp:1633-1770 ``Aura::CanStackWith`` (``this`` = *new*).

    ``trace`` receives the deciding line.  A CONTROL_VEHICLE pair needs the owner's
    vehicle kit: ``vehicle_seats`` (None = no vehicle kit, as the source defaults to true).
    """
    def done(result: bool, why: str) -> bool:
        if trace is not None:
            trace.append(why)
        return result

    if new is existing:
        return done(True, "SpellAuras.cpp:1636 self")
    a, b = new.props, existing.props
    same_caster = new.caster == existing.caster
    if new.kind == "dynobj" or existing.kind == "dynobj":
        if same_caster and a.spell == b.spell:
            return done(False, "SpellAuras.cpp:1643-1648 dynobj same spell same caster")
        return done(True, "SpellAuras.cpp:1643-1648 dynobj otherwise")
    if a.passive and same_caster and ((a.first_rank == b.first_rank and a.spell != b.spell)
                                      or (a.spell == b.spell and not new.cast_item)):
        return done(False, "SpellAuras.cpp:1651-1652 passive same caster (other rank, or same id without cast item)")
    if any(e.trigger == a.spell for e in b.effects):
        return done(True, "SpellAuras.cpp:1654-1659 existing triggers new")
    if any(e.trigger == b.spell for e in a.effects):
        return done(True, "SpellAuras.cpp:1661-1666 new triggers existing")
    if exclusive_by_specific(a, b) or (same_caster and exclusive_by_specific_per_caster(a, b)):
        return done(False, f"SpellAuras.cpp:1669-1671 exclusive by SpellSpecific {a.spell_specific}/{b.spell_specific}")
    rule = groups.rule(a.first_rank, b.first_rank)
    if rule in ("EXCLUSIVE", "EXCLUSIVE_HIGHEST"):
        return done(False, f"SpellAuras.cpp:1676-1678 spell group {rule}")
    if rule == "EXCLUSIVE_FROM_SAME_CASTER" and same_caster:
        return done(False, "SpellAuras.cpp:1679-1681 spell group EXCLUSIVE_FROM_SAME_CASTER")
    if a.family != b.family:
        return done(True, "SpellAuras.cpp:1689-1690 different SpellFamilyName")
    if not same_caster:
        if b.channeled:
            return done(True, "SpellAuras.cpp:1695-1696 existing is channeled")
        if a.dot_stacking_rule:
            return done(True, "SpellAuras.cpp:1698-1699 ATTR3_DOT_STACKING_RULE")
        if has_periodic_non_area_effect(a) and has_periodic_non_area_effect(b):
            return done(True, "SpellAuras.cpp:1733-1734 both periodic non-area")
    if CONTROL_VEHICLE in new.aura_types() and CONTROL_VEHICLE in existing.aura_types():
        if vehicle_seats is None:
            return done(True, "SpellAuras.cpp:1743-1744 no vehicle kit")
        return done(vehicle_seats > 0, "SpellAuras.cpp:1746-1749 vehicle seat count")
    if new.aura_types() & CONFIRMATION_PROMPTS and existing.aura_types() & CONFIRMATION_PROMPTS:
        return done(False, "SpellAuras.cpp:1752-1754 confirmation prompts")
    if a.first_rank == b.first_rank:
        if a.multislot and not new.is_area():
            return done(True, "SpellAuras.cpp:1760-1761 multislot non-area same rank chain")
        if new.cast_item and existing.cast_item and new.cast_item != existing.cast_item and a.enchant_proc:
            return done(True, "SpellAuras.cpp:1762-1764 different cast items, CU_ENCHANT_PROC")
        return done(False, "SpellAuras.cpp:1766 same rank chain")
    return done(True, "SpellAuras.cpp:1769 default")


@dataclass
class CreateInfo:
    props: Props
    caster: str
    owner: str
    cast_item: str = ""
    effect_mask: int | None = None     # _auraEffectMask before BuildEffectMaskForOwner
    stack_amount: int = 1              # AuraCreateInfo::StackAmount (SpellValue AuraStackAmount)


def lookup_key(p: Props, caster: str, cast_item: str) -> dict[str, Any] | None:
    """The ``GetOwnedAura`` key used for refresh (Unit.cpp:3397-3400), or None for multislot."""
    if p.multislot:
        return None
    return {"spell": p.spell,
            "caster": "*" if p.one_slot_any_caster else caster,
            "cast_item": cast_item if p.enchant_proc else "*"}


def get_owned_aura(owned: list[AuraObj], spell: int, caster: str, item: str, req_mask: int = 0) -> AuraObj | None:
    """Mirrors: Unit.cpp:3811 ``Unit::GetOwnedAura`` (``*`` / empty = wildcard; multimap order = insertion)."""
    for a in owned:
        if a.removed or a.spell != spell:
            continue
        if (a.effect_mask & req_mask) != req_mask:
            continue
        if caster not in ("", "*") and a.caster != caster:
            continue
        if item not in ("", "*") and a.cast_item != item:
            continue
        return a
    return None


def try_refresh_stack_or_create(owned: list[AuraObj], ci: CreateInfo, groups: SpellGroups) -> dict[str, Any]:
    """Mirrors: SpellAuras.cpp:350 ``Aura::TryRefreshStackOrCreate`` for a unit owner.

    Chain: BuildEffectMaskForOwner -> Unit::_TryStackingOrRefreshingExistingAura (Unit.cpp:3386) ->
    either the found aura (refresh; ModStackAmount handed to track C) or Aura::Create -> UnitAura ctor ->
    Unit::_AddAura (Unit.cpp:3449) -> _RemoveNoStackAurasDueToAura(owned=true) (Unit.cpp:3714).
    ``IsHighestExclusiveAura`` is vacuous unless an EXCLUSIVE_HIGHEST group exists; such pairs fail closed.
    Mutates ``owned`` (removed flags / appended aura) and returns the outcome.
    """
    p = ci.props
    mask = p.unit_aura_mask() & (ci.effect_mask if ci.effect_mask is not None else 0xFFFFFFFF)
    if not mask:
        return {"outcome": "none", "why": "SpellAuras.cpp:363-364 empty effect mask"}
    trace: list[str] = []
    if not p.multislot:
        key = lookup_key(p, ci.caster, ci.cast_item)
        found = get_owned_aura(owned, p.spell, key["caster"], key["cast_item"])
        if found is not None:
            if mask != found.effect_mask:
                trace.append("Unit.cpp:3405-3406 effect masks differ -> recreate")
            else:
                changes = []
                if found.caster != ci.caster:
                    changes.append(f"caster stays {found.caster} (applier {ci.caster})")
                if ci.cast_item != found.cast_item:
                    changes.append(f"cast item {found.cast_item or '-'} -> {ci.cast_item or '-'} (Unit.cpp:3420-3429)")
                    found.cast_item = ci.cast_item
                changes.append(f"effect base points := applier {ci.caster}'s (Unit.cpp:3409-3419; += under "
                               "SpellEffectAttributes::AuraPointsStack); amounts recalculated with the kept caster")
                found.base_from = ci.caster
                found.events.append(f"ModStackAmount({ci.stack_amount})")
                return {"outcome": "refresh", "aura": found.label, "changes": changes,
                        "why": ["Unit.cpp:3397-3400 GetOwnedAura hit", "Unit.cpp:3432 ModStackAmount"]}
    else:
        trace.append("Unit.cpp:3393 multislot: no lookup")
    new = AuraObj(p, ci.caster, ci.owner, "unit", ci.cast_item, mask,
                  label=f"{p.spell}@{ci.caster}" + (f"#{ci.cast_item}" if ci.cast_item else ""),
                  stacks=ci.stack_amount)
    owned.append(new)
    removed: list[dict[str, Any]] = []
    if not p.passive_stackable_with_ranks:
        for other in owned:
            if other is new or other.removed:
                continue
            if groups.rule(p.first_rank, other.props.first_rank) == "EXCLUSIVE_HIGHEST":
                raise FailClosed("EXCLUSIVE_HIGHEST pair: IsHighestExclusiveAura needs applied amounts")
        for other in list(owned):
            if other is new or other.removed:
                continue
            why: list[str] = []
            if not can_stack_with(new, other, groups, trace=why):
                other.removed = True
                removed.append({"aura": other.label, "why": why[-1]})
    else:
        trace.append("Unit.cpp:3719 IsPassiveStackableWithRanks: no no-stack removal")
    return {"outcome": "replace" if removed else "create", "aura": new.label, "removed": removed, "why": trace}


# ---------------------------------------------------------------------------
# Static identity signature of a spell (census)
# ---------------------------------------------------------------------------

def has_area_aura(p: Props) -> bool:
    return any(e.is_area_aura for e in p.effects)


def area_other_caster(p: Props, groups: SpellGroups) -> dict[str, str]:
    """Second owner's area aura (caster B) meeting caster A's application of the same spell.

    An area aura is owned by its caster, so B's copy reaches a recipient only through B's
    recipient map.  Mirrors: SpellAuras.cpp:732-745 ``Aura::UpdateTargetMap`` (target != owner:
    ``!CanStackWith(applied)`` -> not added, nothing removed) and, when the recipient *is* B
    (B's own application meeting A's), Unit.cpp:3548 ``_ApplyAura`` -> Unit.cpp:3714-3731
    ``_RemoveNoStackAurasDueToAura(owned=false)`` (early return for IsPassiveStackableWithRanks).
    Outcome is order dependent: blocked or coexist on recipients, never a replacement there.
    """
    a = AuraObj(p, "A", "A")
    b = AuraObj(p, "B", "B")
    why: list[str] = []
    stack = can_stack_with(b, a, groups, trace=why)
    recipient = "coexist" if stack else "blocked"
    if p.passive_stackable_with_ranks:
        owner = "coexist"
        owner_why = "Unit.cpp:3719-3720 IsPassiveStackableWithRanks: no no-stack removal"
    else:
        owner = "coexist" if stack else "replace"
        owner_why = why[-1]
    return {"recipient": recipient, "recipient_why": ("SpellAuras.cpp:739-742 recipient map: " if not stack else "") + why[-1],
            "on_owner": owner, "on_owner_why": owner_why}


def same_spell_other_caster(p: Props, groups: SpellGroups) -> tuple[str, str]:
    """Outcome when caster B applies spell *p* to an owner already holding caster A's aura.

    Unit-owned target-held auras; spells with area-aura effects are classified by
    :func:`area_other_caster` (``area:blocked`` / ``area:coexist``) instead.  Returns (outcome, deciding coordinate).
    """
    if has_area_aura(p):
        r = area_other_caster(p, groups)
        return f"area:{r['recipient']}", r["recipient_why"]
    if p.multislot:
        a = AuraObj(p, "A", "T")
        b = AuraObj(p, "B", "T")
        why: list[str] = []
        return ("coexist" if can_stack_with(b, a, groups, trace=why) else "replace"), why[-1]
    if p.one_slot_any_caster:
        return "shared-refresh", "Unit.cpp:3397-3400 IsStackableOnOneSlotWithDifferentCasters -> caster wildcard"
    a = AuraObj(p, "A", "T")
    b = AuraObj(p, "B", "T")
    why = []
    return ("coexist" if can_stack_with(b, a, groups, trace=why) else "replace"), why[-1]


def same_spell_same_caster(p: Props, groups: SpellGroups, cast_item: bool = False) -> tuple[str, str]:
    """Outcome when the same caster re-applies *p* (same effect mask).

    ``cast_item``: both applications carry (different or equal) non-empty cast items, as for
    item-sourced passives -- SpellAuras.cpp:1651 is skipped and 1760-1761 returns coexist.
    """
    if not p.multislot:
        return "refresh", "Unit.cpp:3397-3400 GetOwnedAura hit"
    item = "I" if cast_item else ""
    a = AuraObj(p, "A", "T", cast_item=item)
    b = AuraObj(p, "A", "T", cast_item=item)
    why: list[str] = []
    if p.passive_stackable_with_ranks:
        return "coexist", "Unit.cpp:3719 IsPassiveStackableWithRanks: no no-stack removal"
    return ("coexist" if can_stack_with(b, a, groups, trace=why) else "replace"), why[-1]


def signature(p: Props, groups: SpellGroups, item_sourced: bool = False) -> dict[str, Any]:
    """Static identity signature of a provider spell (unit-owned part + dynobj part).

    ``item_sourced``: the spell is reachable only through item roots (gear/set/gem/enchant), so its
    applications may carry a cast item; the multislot same-caster outcome is then evaluated with one.
    """
    unit = p.unit_aura_mask()
    dyn = p.dynobj_aura_mask()
    out: dict[str, Any] = {"spell": p.spell, "unit_mask": unit, "dynobj_mask": dyn}
    if unit:
        key = lookup_key(p, "<caster>", "<item>")
        out["lookup"] = ("multislot" if key is None else
                         ("any-caster" if key["caster"] == "*" else "per-caster")
                         + ("+cast-item" if key["cast_item"] != "*" else ""))
        out["same_caster"], out["same_caster_why"] = same_spell_same_caster(p, groups)
        if p.multislot:
            out["same_caster_with_cast_item"] = same_spell_same_caster(p, groups, cast_item=True)[0]
            if item_sourced:
                out["same_caster_no_item"] = out["same_caster"]
                out["same_caster"], out["same_caster_why"] = same_spell_same_caster(p, groups, cast_item=True)
                out["same_caster_why"] = "item-sourced: " + out["same_caster_why"]
        out["other_caster"], out["other_caster_why"] = same_spell_other_caster(p, groups)
        if has_area_aura(p):
            out["area"] = area_other_caster(p, groups)
        out["zero_aura_effects"] = [e.index for e in p.effects if e.is_unit_owned_aura and not e.aura]
    if dyn:
        out["dynobj"] = {"per_cast_object": True, "same_caster": "newest-replaces-on-recipient",
                         "other_caster": "coexist",
                         "why": "SpellEffects.cpp:1512-1556 new DynamicObject per cast (TryCreate); "
                                "SpellAuras.cpp:1643-1648 dynobj CanStackWith; Unit.cpp:3548 _ApplyAura no-stack removal"}
    flags = []
    if p.passive:
        flags.append("passive")
    if p.channeled:
        flags.append("channeled")
    if p.dot_stacking_rule:
        flags.append("dot-stacking-rule")
    if p.stack_amount > 1:
        flags.append("stack>1")
    if p.single_target:
        flags.append("single-target")
    if p.enchant_proc:
        flags.append("enchant-proc")
    if p.spell_specific != "NORMAL":
        flags.append(f"specific:{p.spell_specific}")
    if groups.spell_groups.get(p.first_rank):
        flags.append("spell-group")
    if p.rank > 1 or p.first_rank != p.spell:
        flags.append("ranked")
    if p.corrections:
        flags.append("corrected")
    if has_area_aura(p):
        flags.append("area-aura")
    if item_sourced:
        flags.append("item-sourced")
    out["flags"] = flags
    return out


# ---------------------------------------------------------------------------
# Identity taxonomy: key components x observable consequence
# ---------------------------------------------------------------------------

TAXONOMY: list[dict[str, Any]] = [
    {"component": "owner (holder unit / dynamic object)", "role": "identity key",
     "observable": "an aura object belongs to exactly one owner; refresh lookup only searches the target's own m_ownedAuras",
     "coords": ["Unit.cpp:3397 GetOwnedAura on createInfo._owner", "SpellAuras.cpp:374 _owner->ToUnit()"],
     "storage": "Unit::m_ownedAuras multimap<SpellId, Aura*>", "evidence": "trinity-consumer"},
    {"component": "SpellId", "role": "identity key (with rank chain for no-stack)",
     "observable": "only an aura with the same SpellId can be refreshed; different SpellIds of one rank chain replace (CanStackWith IsRankOf)",
     "coords": ["Unit.cpp:3397", "SpellAuras.cpp:1757-1766"], "storage": "multimap key", "evidence": "trinity-consumer"},
    {"component": "caster GUID", "role": "identity key unless IsStackableOnOneSlotWithDifferentCasters",
     "observable": "per-caster objects by default; stack>1 (not channeled, no ATTR3_DOT_STACKING_RULE) -> one shared object whose caster stays the FIRST applier",
     "coords": ["Unit.cpp:3390-3398", "SpellInfo.cpp:1808-1812"], "storage": "Aura::m_casterGuid (never rewritten on refresh)",
     "evidence": "trinity-consumer"},
    {"component": "cast item GUID", "role": "identity key only with SPELL_ATTR0_CU_ENCHANT_PROC",
     "observable": "otherwise a different cast item refreshes the same object and overwrites CastItemGUID/Id/Level",
     "coords": ["Unit.cpp:3397-3400", "Unit.cpp:3420-3429", "SpellMgr.cpp:3122-3150"], "storage": "Aura::m_castItemGuid",
     "evidence": "trinity-consumer"},
    {"component": "effect mask (_effectMask of the Aura)", "role": "refresh precondition",
     "observable": "mask mismatch with the found aura -> new object is created and the old one removed (callbacks fire, stacks/charges/duration restart)",
     "coords": ["Unit.cpp:3402-3406", "Unit.cpp:3714-3730"], "storage": "Aura::m_effects", "evidence": "trinity-consumer"},
    {"component": "application effect mask (AuraApplication::_effectsToApply/_effectMask)", "role": "per-recipient, not identity",
     "observable": "one aura object may be applied with different effect subsets per recipient; ActiveFlags = applied mask",
     "coords": ["SpellAuras.cpp:188-213 UpdateApplyEffectMask", "SpellEffects.cpp:1110-1124"], "storage": "AuraApplication",
     "evidence": "trinity-consumer"},
    {"component": "difficulty", "role": "not a key",
     "observable": "a recast at another difficulty refreshes the existing aura, which keeps its original SpellInfo/m_castDifficulty",
     "coords": ["Unit.cpp:3397 (lookup by Id only)", "SpellAuras.cpp:473 m_castDifficulty"], "storage": "Aura::m_castDifficulty",
     "evidence": "trinity-consumer"},
    {"component": "CastId (cast GUID)", "role": "not a key",
     "observable": "refresh keeps the first cast's CastID (sent as AuraDataInfo.CastID)",
     "coords": ["SpellAuras.cpp:473 m_castId", "SpellAuras.cpp:238 BuildUpdatePacket"], "storage": "Aura::m_castId",
     "evidence": "trinity-consumer"},
    {"component": "application slot (AuraApplication::_slot)", "role": "storage/presentation",
     "observable": "first free visible slot < MAX_AURAS (300); beyond that the application exists but is not sent to the client",
     "coords": ["SpellAuras.cpp:73-97", "SpellAuraDefines.h:22"], "storage": "Unit::m_visibleAuras", "evidence": "trinity-consumer"},
    {"component": "multislot (IsPassive / 55849 / 40075 / 44413)", "role": "disables refresh lookup",
     "observable": "every application builds a new object; same passive without cast item from same caster replaces the old one",
     "coords": ["SpellInfo.cpp:1803-1806", "Unit.cpp:3393", "SpellAuras.cpp:1651-1652"], "storage": "-", "evidence": "trinity-consumer"},
    {"component": "dynamic object (PERSISTENT_AREA_AURA)", "role": "one object per cast",
     "observable": "each cast owns a new DynamicObject + DynObjAura (no refresh); on a recipient, same-caster same-spell applications replace each other",
     "coords": ["SpellEffects.cpp:1512-1556", "SpellAuras.cpp:1643-1648"], "storage": "DynamicObject::_aura", "evidence": "trinity-consumer"},
    {"component": "area source vs recipient application", "role": "one object, many applications",
     "observable": "recipient applications of an area aura share the owner's object (duration, stacks, charges); no per-recipient identity",
     "coords": ["SpellAuras.cpp:658 UpdateTargetMap", "SpellAuras.cpp:586 _ApplyForTarget"], "storage": "Aura::m_applications (GUID map)",
     "evidence": "trinity-consumer", "handoff": "F"},
    {"component": "triggered-by / parent spell", "role": "not a key; exempts from no-stack",
     "observable": "an aura never removes the aura that triggers it or that it triggers (EffectTriggerSpell link), regardless of groups",
     "coords": ["SpellAuras.cpp:1654-1666"], "storage": "-", "evidence": "trinity-consumer"},
    {"component": "SpellSpecific / spell_group", "role": "cross-spell exclusivity",
     "observable": "different spells replace each other (EXCLUSIVE groups, SpellSpecific classes); per-caster variants only for the same caster",
     "coords": ["SpellAuras.cpp:1668-1686", "SpellInfo.cpp:2061-2107", "SpellMgr.cpp:447"], "storage": "-",
     "evidence": ["trinity-consumer", "world-db-fact"]},
    {"component": "single-target (SPELL_ATTR5_LIMIT_N)", "role": "caster-side cap, not identity",
     "observable": "caster keeps at most MaxAffectedTargets such auras (IsSingleTargetWith); oldest over the cap removed",
     "coords": ["Unit.cpp:3457-3478", "SpellAuras.cpp:1207"], "storage": "Unit::m_scAuras", "evidence": "trinity-consumer",
     "handoff": "E"},
    {"component": "spell steal (stolen copy)", "role": "ownership: caster stays the victim's caster",
     "observable": "the stealer's copy is created with SetCasterGUID(original caster): caster-live inputs, caster-keyed removal and "
                   "'same caster' refresh on the stealer all key on the enemy caster, not the stealer",
     "coords": ["Unit.cpp:4063-4088 RemoveAurasDueToSpellBySteal"], "storage": "Aura::m_casterGuid", "evidence": "trinity-consumer",
     "handoff": "E"},
    {"component": "SPELL_EFFECT_MODIFY_AURA_STACKS (289)", "role": "ownership: any caster's aura",
     "observable": "unitTarget->GetAura(TriggerSpell) takes the first applied aura of that id from ANY caster (including a foreign-owned "
                   "area parent) and adds/sets its stacks; mode 0 to zero removes it for all its recipients",
     "coords": ["SpellEffects.cpp:6126-6145", "Unit.cpp:4699-4703"], "storage": "-", "evidence": "trinity-consumer", "handoff": "C, E"},
    {"component": "base points of a shared any-caster object", "role": "latest applier's",
     "observable": "refresh overwrites every effect's m_baseAmount with the new applier's base points (+= under AuraPointsStack) while the "
                   "caster GUID stays the first applier: amount = latest base points x first caster's live bonuses",
     "coords": ["Unit.cpp:3409-3419", "SpellAuras.cpp:1056-1071"], "storage": "AuraEffect::m_baseAmount", "evidence": "trinity-consumer"},
    {"component": "area aura from a second owner", "role": "recipient-map admission",
     "observable": "a second owner's copy is not added to a recipient holding a non-stackable one (nothing removed); on its own owner it "
                   "removes the other's application unless IsPassiveStackableWithRanks",
     "coords": ["SpellAuras.cpp:732-745", "Unit.cpp:3714-3731"], "storage": "-", "evidence": "trinity-consumer", "handoff": "F"},
    {"component": "AuraKey (caster, item, spell, effect mask)", "role": "save/load only",
     "observable": "none at runtime", "coords": ["SpellAuras.h:151-159", "SpellAuras.cpp:1252"], "storage": "character DB",
     "evidence": "trinity-consumer"},
    {"component": "generation", "role": "absent in Trinity",
     "observable": "Trinity has no generation counter; removed auras are pointers parked in m_removedAuras until the next update",
     "coords": ["Unit.cpp:3760"], "storage": "-", "evidence": "structural-inference", "handoff": "I"},
]


ITEM_ROOT_KINDS = frozenset({"current-gear", "current-set", "current-gem", "current-enchant"})


def item_sourced_spells(ctx) -> frozenset[int]:
    """Player-reach spells reachable **only** from item roots (gear / set / gem / enchant).

    Structural: whether every such route propagates a cast item GUID is per-route
    (``CastSpellExtraArgs::SetCastItem``) and not proven here, so this is an upper bound
    (evidence ``structural-inference``; track G owns acquisition routes).
    """
    scope = ctx.scope
    roots = scope.roots.by_spell
    non_item = [s for s, kinds in roots.items() if set(kinds) - ITEM_ROOT_KINDS]
    seen = set(non_item)
    stack = list(non_item)
    while stack:
        node = stack.pop()
        for child, _ in scope.edges.get(node, []):
            if child not in seen and child in scope.reach:
                seen.add(child)
                stack.append(child)
    return frozenset(set(scope.reach) - seen)


# ---------------------------------------------------------------------------
# Census over the shared provider denominator
# ---------------------------------------------------------------------------

def census(ctx, pb: PropsBuilder | None = None, groups: SpellGroups | None = None) -> dict[str, Any]:
    """Identity signatures of every provider spell, aggregated per population.

    Population: ``providers.provider_spells`` (all), restricted to player / controlled
    by ``providers.populations``.  Returns ``{"rows": {spell: signature}, "counts": ...}``.
    """
    from collections import Counter

    from .providers import populations
    pb = pb or PropsBuilder(ctx)
    groups = groups or SpellGroups.from_overlay(ctx)
    pops = populations(ctx)
    rows: dict[int, dict[str, Any]] = {}
    failed: dict[int, str] = {}
    items = item_sourced_spells(ctx)
    for spell in sorted(pops["all"]):
        try:
            rows[spell] = signature(pb(spell), groups, item_sourced=spell in items)
        except FailClosed as exc:
            failed[spell] = str(exc)
    counts: dict[str, Any] = {}
    for pop, members in sorted(pops.items()):
        sub = [rows[s] for s in sorted(members) if s in rows]
        c: dict[str, Counter] = {k: Counter() for k in ("lookup", "same_caster", "other_caster", "other_caster_why",
                                                        "same_caster_why", "flags", "dynobj", "area_on_owner",
                                                        "same_caster_with_cast_item")}
        for r in sub:
            if "lookup" in r:
                for k in ("lookup", "same_caster", "other_caster", "other_caster_why", "same_caster_why"):
                    c[k][r[k]] += 1
                if "area" in r:
                    c["area_on_owner"][r["area"]["on_owner"]] += 1
                if "same_caster_with_cast_item" in r:
                    c["same_caster_with_cast_item"][r["same_caster_with_cast_item"]] += 1
            if "dynobj" in r:
                c["dynobj"]["dynobj-aura spells"] += 1
            for f in r["flags"]:
                c["flags"][f] += 1
        counts[pop] = {"population": pop, "spells": len(members), "evaluated": len(sub),
                       "zero_aura_apply_effects": sum(1 for r in sub if r.get("zero_aura_effects")),
                       **{k: dict(sorted(v.items())) for k, v in c.items()}}
    return {"rows": rows, "counts": counts, "failed": failed, "groups_dropped": groups.dropped,
            "group_rules": {str(k): STACK_RULES[v] for k, v in sorted(groups.rules.items())}}


# ---------------------------------------------------------------------------
# Probe serialisation (tools/tc_aura_identity_probe protocol)
# ---------------------------------------------------------------------------

SPECIFIC_VALUES = {"NORMAL": 0, "SEAL": 1, "AURA": 3, "STING": 4, "CURSE": 5, "ASPECT": 6, "TRACKER": 7,
                   "WARLOCK_ARMOR": 8, "MAGE_ARMOR": 9, "ELEMENTAL_SHIELD": 10, "MAGE_POLYMORPH": 11, "FOOD": 19,
                   "DRINK": 20, "FOOD_AND_DRINK": 21, "PRESENCE": 22, "CHARM": 23, "SCROLL": 24,
                   "MAGE_ARCANE_BRILLANCE": 25, "WARRIOR_ENRAGE": 26, "PRIEST_DIVINE_SPIRIT": 27, "HAND": 28,
                   "PHASE": 29, "BANE": 30}   # SpellInfo.h:115-140


def probe_spell_line(p: Props) -> str:
    """``spell`` line of the identity probe protocol for *p*."""
    ff = [(p.family_flags >> (32 * i)) & 0xFFFFFFFF for i in range(4)]
    attrs = list(p.attributes)[:17] + [0] * max(0, 17 - len(p.attributes))
    effs = " ".join(f"{e.index} {e.effect} {e.aura} {e.trigger} {int(e.is_targeting_area)} {e.attributes}" for e in p.effects)
    return (f"spell {p.spell} {p.first_rank} {p.family} {' '.join(map(str, ff))} {p.stack_amount} {p.dispel} "
            f"{p.aura_interrupt0} {p.cu} {' '.join(map(str, attrs))} {len(p.effects)} {effs}").strip()
