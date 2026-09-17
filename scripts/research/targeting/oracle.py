"""``SpellView``: the immutable spell/effect facts the targeting stages read.

A view is built either

* from the pinned DB2 snapshot (:func:`from_data`): ``ctx.data`` effect /
  restriction / misc rows, ``SpellRange``, ``SpellRadius``, catalog attributes and
  DmgClass, the Dummy-pass script bindings (target hooks), the world-DB overlay
  (implicit-target conditions, ``spell_custom_attr``) and the load-time target
  rewrites of ``LoadSpellInfoCorrections`` (track A ``attributes.corrected_effects``); or
* from a synthetic ``spell`` block of a fixture (:func:`from_fixture`), for pure
  discriminating fixtures that do not name a real spell.

What the view does not know is ``None`` and every accessor that would read it
raises :class:`targeting.FailClosed` (``SpellView.need``).  Known
not-modelled inputs are refused when the view is built from data:
``LoadSpellInfoCorrections`` writes of targeting members other than
TargetA/TargetB/Effect (radius/range/cap/attribute rewrites), server-side
(``serverside_spell``) spells, non-``DIFFICULTY_NONE`` rows.

Runtime-dependent SpellInfo arithmetic that the stages share lives here too
(``calc_radius``, ``max_range``, ``apply_spell_mod``) because each needs a
caster fact from the fixture (level, spell-mod owner, movement) and must fail
closed on its own.

Fixture ``spell`` block (synthetic; ``"synthetic": true``)::

    {"synthetic": true, "id": 900001, "dmg_class": 1, "speed": 0.0,
     "attributes": ["SPELL_ATTR1_REQUIRE_ALL_TARGETS"],     # complete set (unlisted = clear)
     "attributes_cu": [],                                    # optional; absent = unknown
     "range": {"min": [0, 0], "max": [40, 40]},              # [hostile, friendly]; null = no RangeEntry
     "cone_angle": 0.0, "width": 0.0, "max_affected_targets": 0,
     "is_positive": false,                                   # optional; absent = unknown
     "script_hooks": [],                                     # optional; absent = unknown
     "effects": [{"index": 0, "effect": "SCHOOL_DAMAGE", "target_a": "TARGET_UNIT_TARGET_ENEMY",
                  "target_b": 0, "radius_a": {"radius": 8, "per_level": 0, "min": 0, "max": 8},
                  "radius_b": null, "chain_targets": 0, "attributes": 0, "conditions": null}]}

A fixture naming a real spell uses ``{"id": 1064, "difficulty": 0}``; optional
``"override"`` keys replace view fields (recorded in ``view.source``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace
from functools import cache
from typing import Any

from . import FailClosed

# ---------------------------------------------------------------------------
# binary32 helpers
# ---------------------------------------------------------------------------


def f32(x: float) -> float:
    """Round a double to IEEE binary32 (what a C++ ``float`` holds)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


# ---------------------------------------------------------------------------
# enums (verbatim values, pinned 7f3d43b)
# ---------------------------------------------------------------------------

#: DBCEnums.h:2400 ``enum class SpellEffectAttributes`` (only the bits the targeting code reads).
EFFECT_ATTR = {
    "AlwaysAoeLineOfSight": 0x00000020,
    "ChainFromInitialTarget": 0x00000080,
    "PlayersOnly": 0x00004000,
    "EnforceLineOfSightToChainTargets": 0x00010000,
    "DontFailSpellOnTargetingFailure": 0x00100000,
}
#: SpellInfo.h:142 ``SpellCustomAttributes`` (verbatim).
CU_ATTR = {
    "SPELL_ATTR0_CU_ENCHANT_PROC": 0x00000001,
    "SPELL_ATTR0_CU_CONE_BACK": 0x00000002,
    "SPELL_ATTR0_CU_CONE_LINE": 0x00000004,
    "SPELL_ATTR0_CU_SHARE_DAMAGE": 0x00000008,
    "SPELL_ATTR0_CU_NO_INITIAL_THREAT": 0x00000010,
    "SPELL_ATTR0_CU_AURA_CC": 0x00000020,
    "SPELL_ATTR0_CU_DONT_BREAK_STEALTH": 0x00000040,
    "SPELL_ATTR0_CU_CAN_CRIT": 0x00000080,
    "SPELL_ATTR0_CU_DIRECT_DAMAGE": 0x00000100,
    "SPELL_ATTR0_CU_CHARGE": 0x00000200,
    "SPELL_ATTR0_CU_PICKPOCKET": 0x00000400,
    "SPELL_ATTR0_CU_DEPRECATED_ROLLING_PERIODIC": 0x00000800,
    "SPELL_ATTR0_CU_DEPRECATED_NEGATIVE_EFF0": 0x00001000,
    "SPELL_ATTR0_CU_DEPRECATED_NEGATIVE_EFF1": 0x00002000,
    "SPELL_ATTR0_CU_DEPRECATED_NEGATIVE_EFF2": 0x00004000,
    "SPELL_ATTR0_CU_IGNORE_ARMOR": 0x00008000,
    "SPELL_ATTR0_CU_REQ_TARGET_FACING_CASTER": 0x00010000,
    "SPELL_ATTR0_CU_REQ_CASTER_BEHIND_TARGET": 0x00020000,
    "SPELL_ATTR0_CU_ALLOW_INFLIGHT_TARGET": 0x00040000,
    "SPELL_ATTR0_CU_NEEDS_AMMO_DATA": 0x00080000,
    "SPELL_ATTR0_CU_BINARY_SPELL": 0x00100000,
    "SPELL_ATTR0_CU_SCHOOLMASK_NORMAL_WITH_MAGIC": 0x00200000,
    "SPELL_ATTR0_CU_DEPRECATED_LIQUID_AURA": 0x00400000,
    "SPELL_ATTR0_CU_IS_TALENT": 0x00800000,
    "SPELL_ATTR0_CU_AURA_CANNOT_BE_SAVED": 0x01000000,
    "SPELL_ATTR0_CU_CAN_TARGET_ANY_PRIVATE_OBJECT": 0x02000000,
}
#: SharedDefines.h:3143 ``SpellDmgClass``.
DMG_CLASS = {"NONE": 0, "MAGIC": 1, "MELEE": 2, "RANGED": 3}


def target_id(token: int | str | None) -> int:
    """A ``Targets`` value from an int or a ``TARGET_*`` name (SharedDefines.h)."""
    if token is None:
        return 0
    if isinstance(token, int):
        return token
    from procs._trinity_names import SPELL_TARGET_NAMES
    for k, v in SPELL_TARGET_NAMES.items():
        if v == token:
            return k
    raise FailClosed(f"spell view: unknown target name {token!r}")


def target_name(value: int) -> str:
    from procs._trinity_names import SPELL_TARGET_NAMES
    return SPELL_TARGET_NAMES.get(value, f"TARGET_{value}")


def effect_id(token: int | str) -> int:
    if isinstance(token, int):
        return token
    from procs.enums import effect
    return effect(token.removeprefix("SPELL_EFFECT_"))


def aura_id(token: int | str | None) -> int:
    if not token:
        return 0
    if isinstance(token, int):
        return token
    from procs.enums import aura
    return aura(token.removeprefix("SPELL_AURA_"))


def _attr_words(names: list[str]) -> tuple[int, ...]:
    from procs.enums import attr
    words = [0] * 17
    for n in names:
        try:
            w, bit = attr(n)
        except KeyError as exc:
            raise FailClosed(f"spell view: {exc}") from None
        words[w] |= bit
    return tuple(words)


# ---------------------------------------------------------------------------
# view types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RadiusEntry:
    """``SpellRadiusEntry`` (all binary32)."""
    radius: float
    per_level: float
    min: float
    max: float

    @classmethod
    def from_row(cls, row: dict) -> RadiusEntry:
        return cls(f32(float(row["Radius"])), f32(float(row["RadiusPerLevel"])),
                   f32(float(row["RadiusMin"])), f32(float(row["RadiusMax"])))

    @classmethod
    def from_json(cls, d: dict | None) -> RadiusEntry | None:
        if d is None:
            return None
        return cls(f32(float(d["radius"])), f32(float(d.get("per_level", 0.0))),
                   f32(float(d.get("min", 0.0))), f32(float(d["max"])))


@dataclass(frozen=True)
class RangeEntry:
    """``SpellRangeEntry``: index 0 = hostile, 1 = friendly (``GetMaxRange(positive)``).

    Subscriptable (``entry["flags"]``) for track B's dict contract.
    """
    min: tuple[float, float]
    max: tuple[float, float]
    flags: int = 0

    def __getitem__(self, key: str):
        return getattr(self, key)


@dataclass(frozen=True)
class EffectView:
    index: int
    effect: int
    aura: int = 0
    target_a: int = 0
    target_b: int = 0
    radius_a: RadiusEntry | None = None
    radius_b: RadiusEntry | None = None
    chain_targets: int = 0
    attributes: int = 0
    pos_facing: float = 0.0
    trigger_spell: int = 0
    #: identity of ``ImplicitTargetConditions`` (shared_ptr identity); None = no conditions
    conditions: str | None = None

    @property
    def is_effect(self) -> bool:
        """Mirrors: SpellInfo.h ``SpellEffectInfo::IsEffect`` -- ``Effect != 0``."""
        return self.effect != 0

    def has_attribute(self, name: str) -> bool:
        return bool(self.attributes & EFFECT_ATTR[name])

    def radius_entry(self, target_index: str) -> RadiusEntry | None:
        return self.radius_a if target_index == "A" else self.radius_b

    def target(self, target_index: str) -> int:
        return self.target_a if target_index == "A" else self.target_b


@dataclass(frozen=True)
class SpellView:
    id: int
    difficulty: int
    source: str                                  # "db2" | "fixture" | "db2+override"
    effects: tuple[EffectView, ...]              # dense, index == position (gaps are Effect 0)
    attributes: tuple[int, ...]                  # 17 words, SpellMisc.Attributes_N (after corrections)
    attributes_cu: int | None = None             # None = not modelled for this view
    dmg_class: int | None = None
    speed: float = 0.0
    launch_delay: float = 0.0
    min_duration: float = 0.0
    range: RangeEntry | None = None
    cone_angle: float = 0.0
    width: float = 0.0
    max_affected_targets: int = 0
    max_target_level: int = 0
    target_creature_type: int = 0
    targets: int = 0
    positive: bool | None = None                 # SpellInfo::IsPositive (load-time positivity not ported)
    #: executing target hooks: ({list, target, affected_mask, script, handler}, ...); None = unknown
    script_hooks: tuple[dict[str, Any], ...] | None = None
    name: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    mechanic: int = 0
    family: int = 0
    category: int = 0
    #: SpellAuraRestrictions (target side) as track B reads them; all-zero when the spell has no row
    aura_restrictions: dict[str, int] | None = None
    #: DisableMgr::IsDisabledFor(SPELL, id, nullptr, SPELL_DISABLE_LOS); None = world `disables` not loaded
    los_disabled: bool | None = None
    #: SpellCastingRequirements.FacingCasterFlags
    facing_caster_flags: int = 0
    #: CalcCastTime without a Spell (SpellInfo.cpp:4000-4015: max(Base, Minimum), <=0 -> 0,
    #: +500 for ranged-slot spells); caster ModSpellCastTime is NOT applied.  None = unknown.
    cast_time_ms: int | None = None
    #: AttributesCu bits the view actually models (reading any other bit fails closed)
    cu_known_mask: int = 0xFFFFFFFF

    # -- fail-closed access --------------------------------------------------
    def need(self, attr: str) -> Any:
        value = getattr(self, attr)
        if value is None:
            raise FailClosed(f"spell view {self.id}: {attr!r} is not modelled ({self.source})")
        return value

    def effect(self, index: int) -> EffectView:
        if not 0 <= index < len(self.effects):
            raise FailClosed(f"spell view {self.id}: no effect index {index}")
        return self.effects[index]

    def has_attr(self, name: str) -> bool:
        """Mirrors: SpellInfo.h ``SpellInfo::HasAttribute(SpellAttrN)``."""
        if name.startswith("SPELL_ATTR0_CU_"):
            return self.has_cu(name)
        from procs.enums import attr
        word, bit = attr(name)
        return bool(self.attributes[word] & bit)

    def has_cu(self, name: str) -> bool:
        """Mirrors: SpellInfo.h ``HasAttribute(SpellCustomAttributes)``; fails closed when AttributesCu is unmodelled."""
        bit = CU_ATTR[name]
        if not bit & self.cu_known_mask:
            raise FailClosed(f"spell view {self.id}: AttributesCu bit {name} is not modelled ({self.source})")
        return bool(self.need("attributes_cu") & bit)

    @property
    def is_positive(self) -> bool:
        """``SpellInfo::IsPositive`` (SpellInfo.cpp:1879) -- must be stated (fixture / override)."""
        return bool(self.need("positive"))

    @property
    def is_allowing_dead_target(self) -> bool:
        """Mirrors: SpellInfo.cpp:1838 ``SpellInfo::IsAllowingDeadTarget``."""
        from .selectors import info
        if self.has_attr("SPELL_ATTR2_ALLOW_DEAD_TARGET") or self.targets & (0x8000 | 0x200 | 0x400):
            return True
        return any(e.is_effect and (info(e.target_a).object == "CORPSE" or info(e.target_b).object == "CORPSE")
                   for e in self.effects)

    @property
    def is_affecting_area(self) -> bool:
        """Mirrors: SpellInfo.cpp:1693 ``IsAffectingArea`` (IsTargetingArea / PERSISTENT_AREA_AURA / area auras)."""
        from procs.enums import effect

        from .attributes import AREA_AURA_EFFECTS
        from .selectors import info
        paa = effect("PERSISTENT_AREA_AURA")
        return any(e.is_effect and (info(e.target_a).is_area or info(e.target_b).is_area or e.effect == paa
                                    or e.effect in AREA_AURA_EFFECTS) for e in self.effects)

    @property
    def is_next_melee_swing(self) -> bool:
        """Mirrors: SpellInfo.cpp:1899 ``IsNextMeleeSwingSpell``."""
        return self.has_attr("SPELL_ATTR0_ON_NEXT_SWING_NO_DAMAGE") or self.has_attr("SPELL_ATTR0_ON_NEXT_SWING")

    @property
    def has_only_damage_effects(self) -> bool:
        """Mirrors: SpellInfo.cpp:1579 ``HasOnlyDamageEffects``."""
        from procs.enums import effect
        ok = {effect(n) for n in ("WEAPON_DAMAGE", "WEAPON_DAMAGE_NOSCHOOL", "NORMALIZED_WEAPON_DMG",
                                  "WEAPON_PERCENT_DAMAGE", "SCHOOL_DAMAGE", "ENVIRONMENTAL_DAMAGE", "HEALTH_LEECH",
                                  "DAMAGE_FROM_MAX_HEALTH_PCT")}
        return all(e.effect in ok for e in self.effects if e.is_effect)

    @property
    def resurrect(self) -> bool:
        """``HasEffect(SPELL_EFFECT_SELF_RESURRECT) || HasEffect(SPELL_EFFECT_RESURRECT)`` (SpellInfo.cpp:2517)."""
        from procs.enums import effect
        return any(e.effect in (effect("SELF_RESURRECT"), effect("RESURRECT")) for e in self.effects)

    def _explicit_masks(self) -> tuple[int, int]:
        """Mirrors: SpellInfo.cpp:4572 ``_InitializeExplicitTargetMask`` (via track A composition)."""
        from .composition import explicit_target_mask
        rng = self.range
        return explicit_target_mask(
            [e for e in self.effects], max_range_negative=rng.max[0] if rng else 0.0,
            max_range_positive=rng.max[1] if rng else 0.0, targets=self.targets, attributes13=self.attributes[13])

    @property
    def explicit_target_mask(self) -> int:
        return self._explicit_masks()[0]

    @property
    def required_explicit_target_mask(self) -> int:
        return self._explicit_masks()[1]

    @property
    def is_channeled(self) -> bool:
        """Mirrors: SpellInfo.cpp:1889 ``IsChanneled`` (ATTR1_IS_CHANNELLED | ATTR1_IS_SELF_CHANNELLED)."""
        return self.has_attr("SPELL_ATTR1_IS_CHANNELLED") or self.has_attr("SPELL_ATTR1_IS_SELF_CHANNELLED")

    def target_hooks(self, effect_index: int, target: int, lst: str) -> list[dict[str, Any]]:
        """Executing script target hooks bound to (effect, target, hook list).

        Mirrors: Spell.cpp:8991-9030 ``CallScript*TargetSelectHandlers`` -- a hook
        runs when ``IsEffectAffected(effIndex)`` and ``targetType.GetTarget() == hook.GetTarget()``.
        """
        hooks = self.script_hooks
        if hooks is None:
            raise FailClosed(f"spell view {self.id}: script target hooks unknown ({self.source})")
        return [h for h in hooks if h["list"] == lst and h["target"] == target
                and h["affected_mask"] is not None and h["affected_mask"] & (1 << effect_index)]

    def to_json(self) -> dict[str, Any]:
        from dataclasses import asdict
        d = asdict(self)
        d["effects"] = [asdict(e) for e in self.effects]
        return d


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def from_fixture(block: dict[str, Any]) -> SpellView:
    """A synthetic view from a fixture ``spell`` block (see module docstring)."""
    if not block.get("synthetic"):
        raise FailClosed("spell view: fixture spell block is not marked synthetic")
    effects: dict[int, EffectView] = {}
    for e in block.get("effects", []):
        idx = int(e["index"])
        if idx in effects:
            raise FailClosed(f"spell view: duplicate effect index {idx}")
        effects[idx] = EffectView(
            index=idx, effect=effect_id(e["effect"]), aura=aura_id(e.get("aura")),
            target_a=target_id(e.get("target_a")), target_b=target_id(e.get("target_b")),
            radius_a=RadiusEntry.from_json(e.get("radius_a")), radius_b=RadiusEntry.from_json(e.get("radius_b")),
            chain_targets=int(e.get("chain_targets", 0)),
            attributes=_effect_attr_value(e.get("attributes", 0)),
            pos_facing=f32(float(e.get("pos_facing", 0.0))), trigger_spell=int(e.get("trigger_spell", 0)),
            conditions=e.get("conditions"))
    rng = block.get("range", "missing")
    if rng == "missing":
        raise FailClosed("spell view: synthetic spell must state `range` (null = no RangeEntry)")
    range_entry = None if rng is None else RangeEntry(
        tuple(f32(float(v)) for v in rng["min"]), tuple(f32(float(v)) for v in rng["max"]), int(rng.get("flags", 0)))
    cu = block.get("attributes_cu")
    hooks = block.get("script_hooks")
    dmg = block.get("dmg_class")
    return SpellView(
        id=int(block.get("id", 0)), difficulty=0, source="fixture", effects=_dense(effects),
        attributes=_attr_words(block.get("attributes", [])),
        attributes_cu=None if cu is None else sum(CU_ATTR[n] for n in cu),
        dmg_class=DMG_CLASS[dmg] if isinstance(dmg, str) else dmg,
        speed=f32(float(block.get("speed", 0.0))), launch_delay=f32(float(block.get("launch_delay", 0.0))),
        min_duration=f32(float(block.get("min_duration", 0.0))), range=range_entry,
        cone_angle=f32(float(block.get("cone_angle", 0.0))), width=f32(float(block.get("width", 0.0))),
        max_affected_targets=int(block.get("max_affected_targets", 0)),
        positive=block.get("is_positive"), targets=int(block.get("targets", 0)),
        max_target_level=int(block.get("max_target_level", 0)),
        target_creature_type=int(block.get("target_creature_type", 0)),
        script_hooks=None if hooks is None else tuple(_norm_hook(h) for h in hooks),
        name=block.get("name", "synthetic"), mechanic=int(block.get("mechanic", 0)),
        family=int(block.get("family", 0)), category=int(block.get("category", 0)),
        aura_restrictions={k: int(block.get("aura_restrictions", {}).get(k, 0)) for k in AURA_RESTRICTION_KEYS},
        # a synthetic id is in no world `disables` row (fact, not a default)
        los_disabled=bool(block.get("los_disabled", False)),
        facing_caster_flags=int(block.get("facing_caster_flags", 0)),
        cast_time_ms=block.get("cast_time_ms"))


def _effect_attr_value(v: int | list[str]) -> int:
    if isinstance(v, int):
        return v
    return sum(EFFECT_ATTR[n] for n in v)


def _norm_hook(h: dict[str, Any]) -> dict[str, Any]:
    """Fixture hook: a synthetic hook is a registered, loaded script unless it states ``loads: false``."""
    return {"list": h["list"], "target": target_id(h["target"]), "affected_mask": int(h["affected_mask"]),
            "script": h.get("script", ""), "handler": h.get("handler", ""), "loads": bool(h.get("loads", True))}


def _dense(effects: dict[int, EffectView]) -> tuple[EffectView, ...]:
    if not effects:
        return ()
    return tuple(effects.get(i, EffectView(index=i, effect=0)) for i in range(max(effects) + 1))


@cache
def _bindings():
    from dummy_semantics.bindings import BindingMap

    from . import context
    return BindingMap(context.get().bundle)


@cache
def _implicit_conditions() -> dict[int, list[dict]]:
    from . import context
    return dict(context.get().bundle.world.conditions_by_source().get(13, {}))  # CONDITION_SOURCE_TYPE_SPELL_IMPLICIT_TARGET


def from_data(spell: int, difficulty: int = 0, *, allow_corrections: bool = False) -> SpellView:
    """A view of a real spell from the pinned snapshot.

    Mirrors (loading): SpellInfo.cpp ``SpellInfo::SpellInfo`` (effect rows by
    ``EffectIndex``; ``SpellTargetRestrictions`` -> ConeAngle/Width/MaxAffectedTargets/
    MaxTargetLevel/TargetCreatureType/Targets; SpellMisc -> RangeEntry/Speed/LaunchDelay/
    MinDuration), ConditionMgr.cpp:1575 (implicit-target conditions bound per effect mask).
    """
    from . import context
    ctx = context.get()
    d = ctx.data
    info = ctx.catalog.get(spell, difficulty)
    rows = d.effects(spell, difficulty)
    if info is None or not rows:
        overlay = ctx.bundle.serverside_spells() if hasattr(ctx.bundle, "serverside_spells") else {}
        if (spell, difficulty) in overlay:
            raise FailClosed(f"spell view {spell}: serverside_spell (world DB) spells are not modelled")
        raise FailClosed(f"spell view {spell}: not in the DB2 snapshot")
    notes: list[str] = []
    # LoadSpellInfoCorrections (SpellMgr.cpp): TargetA/TargetB/Effect writes and the generic area-aura
    # rewrite (5286-5291) are applied through track A; any other targeting member write fails closed.
    from .attributes import corrected_effects, corrections
    other = [w for w in corrections(spell) if w["member"] not in ("TargetA", "TargetB", "Effect")]
    if other and not allow_corrections:
        raise FailClosed(f"spell view {spell}: LoadSpellInfoCorrections writes targeting members not applied here: "
                         f"{sorted({(w['member'], w['consumer']) for w in other})}")
    if other:
        notes.append(f"corrections NOT applied: {sorted({(w['member'], w['consumer']) for w in other})}")
    slots, fix_notes = corrected_effects(spell, rows)
    notes += fix_notes
    fixed = {r["index"]: r for r in slots}
    conds = _implicit_conditions().get(spell, [])
    radii = d.radii

    def radius(idx: int) -> RadiusEntry | None:
        if not idx:
            return None
        row = radii.get(idx)
        if row is None:  # sSpellRadiusStore.LookupEntry -> nullptr
            return None
        return RadiusEntry.from_row(row)

    # ConditionMgr.cpp:1575 container identity per effect (track F loader mirror)
    from .recipients import implicit_condition_identity
    dense_rows = {e.index: e for e in rows}
    slots = [dense_rows.get(i) or EffectView(index=i, effect=0) for i in range(max(dense_rows) + 1)]
    cond_ids = implicit_condition_identity(
        slots, [(int(c["SourceGroup"]), int(c["ConditionValue1"]) if int(c["ConditionTypeOrReference"]) == 51 else None)
                for c in conds]) if conds else [None] * len(slots)
    effects: dict[int, EffectView] = {}
    for e in rows:
        cid = cond_ids[e.index]
        fx = fixed[e.index]
        effects[e.index] = EffectView(
            index=e.index, effect=fx["effect"], aura=e.aura, target_a=fx["a"], target_b=fx["b"],
            radius_a=radius(e.radius_a), radius_b=radius(e.radius_b), chain_targets=e.chain_targets,
            attributes=e.attributes, pos_facing=f32(e.pos_facing), trigger_spell=e.trigger_spell,
            conditions=None if cid is None else f"spell{spell}:cond{cid}")
    misc = d.misc(spell, difficulty) or {}
    rest = d.restrictions(spell, difficulty) or {}
    range_entry = None
    if misc.get("RangeIndex"):
        r = d.ranges.get(misc["RangeIndex"])
        if r is not None:
            range_entry = RangeEntry((f32(float(r["RangeMin_0"])), f32(float(r["RangeMin_1"]))),
                                     (f32(float(r["RangeMax_0"])), f32(float(r["RangeMax_1"]))), int(r["Flags"]))
    hooks = []
    for b in _bindings().bindings(spell):
        for h in b.hooks:
            if h.list in ("OnObjectAreaTargetSelect", "OnObjectTargetSelect", "OnDestinationTargetSelect"):
                hooks.append({"list": h.list, "target": h.eff_value, "affected_mask": h.affected_mask,
                              "script": b.script_name, "handler": h.handler})
                if h.affected_mask is None:
                    notes.append(f"unresolved target hook {b.script_name}::{h.handler}")
    return SpellView(
        id=spell, difficulty=difficulty, source="db2", effects=_dense(effects),
        attributes=tuple(int(w) & 0xFFFFFFFF for w in info.attributes),
        attributes_cu=_custom_attributes(spell), cu_known_mask=_cu_known_mask(),
        dmg_class=info.dmg_class, speed=f32(float(misc.get("Speed", 0.0))),
        launch_delay=f32(float(misc.get("LaunchDelay", 0.0))), min_duration=f32(float(misc.get("MinDuration", 0.0))),
        range=range_entry, cone_angle=f32(float(rest.get("ConeDegrees", 0.0))), width=f32(float(rest.get("Width", 0.0))),
        max_affected_targets=int(rest.get("MaxTargets", 0)), max_target_level=int(rest.get("MaxTargetLevel", 0)),
        target_creature_type=int(rest.get("TargetCreatureType", 0)), targets=int(rest.get("Targets", 0)),
        positive=_positive(spell, notes), script_hooks=tuple(sorted(hooks, key=lambda h: (h["list"], h["target"] or 0, h["handler"]))),
        name=ctx.name(spell), notes=tuple(notes), mechanic=int(info.mechanic), family=int(info.family),
        category=int(info.category), aura_restrictions=_aura_restrictions(d.aura_restrictions(spell, difficulty)),
        los_disabled=_los_disabled(spell),
        facing_caster_flags=int((d.casting_requirements(spell) or {}).get("FacingCasterFlags", 0) or 0),
        cast_time_ms=_cast_time(spell, int(misc.get("CastingTimeIndex", 0) or 0) if "CastingTimeIndex" in misc
                                else _misc_cast_index(spell, difficulty), info))


AURA_RESTRICTION_KEYS = ("target_aura_state", "exclude_target_aura_state", "target_aura_spell",
                         "exclude_target_aura_spell", "target_aura_type", "exclude_target_aura_type",
                         "caster_aura_state", "exclude_caster_aura_state", "caster_aura_spell",
                         "exclude_caster_aura_spell", "caster_aura_type", "exclude_caster_aura_type")


def _aura_restrictions(row: dict | None) -> dict[str, int]:
    """SpellAuraRestrictions row -> track B keys (SpellInfo ctor copies; absent row = zeros)."""
    row = row or {}
    cols = {"target_aura_state": "TargetAuraState", "exclude_target_aura_state": "ExcludeTargetAuraState",
            "target_aura_spell": "TargetAuraSpell", "exclude_target_aura_spell": "ExcludeTargetAuraSpell",
            "target_aura_type": "TargetAuraType", "exclude_target_aura_type": "ExcludeTargetAuraType",
            "caster_aura_state": "CasterAuraState", "exclude_caster_aura_state": "ExcludeCasterAuraState",
            "caster_aura_spell": "CasterAuraSpell", "exclude_caster_aura_spell": "ExcludeCasterAuraSpell",
            "caster_aura_type": "CasterAuraType", "exclude_caster_aura_type": "ExcludeCasterAuraType"}
    return {k: int(row.get(c, 0) or 0) for k, c in cols.items()}


@cache
def _cast_time_rows() -> dict[int, tuple[int, int]]:
    from . import context
    return {int(r[0]): (int(r[1]), int(r[2]))
            for r in context.get().data.source.project("SpellCastTimes", ("ID", "Base", "Minimum"))}


@cache
def _misc_cast_indexes() -> dict[tuple[int, int], int]:
    from . import context
    return {(int(r[0]), int(r[1])): int(r[2]) for r in context.get().data.source.project(
        "SpellMisc", ("SpellID", "DifficultyID", "CastingTimeIndex"))}


def _misc_cast_index(spell: int, difficulty: int) -> int:
    return _misc_cast_indexes().get((spell, difficulty), 0)


def _cast_time(spell: int, index: int, info) -> int | None:
    """Mirrors: SpellInfo.cpp:4000 ``CalcCastTime(nullptr)`` (no caster mods)."""
    row = _cast_time_rows().get(index) if index else None
    cast = max(row[0], row[1]) if row else 0
    if cast <= 0:
        return 0
    from procs.enums import attr
    if info.has_attr(attr("SPELL_ATTR0_USES_RANGED_SLOT")):
        return None  # IsAutoRepeatRangedSpell / ATTR9_COOLDOWN_IGNORES_RANGED_WEAPON branch not ported
    return cast


def _positive(spell: int, notes: list[str]) -> bool | None:
    """``SpellInfo::IsPositive`` from the load-time positivity port (targeting.positivity); None when it
    fails closed (load-order dependent / unported input) -- reading ``is_positive`` then fails closed."""
    from .positivity import is_positive
    try:
        return is_positive(spell)
    except FailClosed as exc:
        notes.append(f"positivity unknown: {exc}")
        return None


def _custom_attributes(spell: int) -> int:
    """AttributesCu bits targeting reads (track A ``attributes.custom_attributes``: spell_custom_attr + SpellMgr code)."""
    from . import context
    from .attributes import custom_attributes
    return int(custom_attributes(context.get(), spell))


def _cu_known_mask() -> int:
    from .attributes import CUSTOM_ATTRIBUTES
    mask = 0
    for v in CUSTOM_ATTRIBUTES.values():
        mask |= v
    return mask


DISABLES_EXTRACT = "inputs/disables.json"  # tools/tdb_world_extract.py --tables disables (regen_targeting tdb stage)


@cache
def _disables() -> dict[int, int] | None:
    """Spell rows (``sourceType`` 0) of world table ``disables``: the Dummy-pass overlay if it carries the
    table, else the targeting input extract; ``None`` when neither exists."""
    import json

    from . import CORPORA, SourceError, context
    try:
        rows = context.get().bundle.world.table("disables").dicts()
    except SourceError:
        path = CORPORA / DISABLES_EXTRACT
        if not path.exists():
            return None
        t = json.loads(path.read_text(encoding="utf-8"))["tables"]["disables"]
        rows = [dict(zip(t["columns"], r)) for r in t["rows"]]
    return {int(r["entry"]): int(r["flags"]) for r in rows if int(r["sourceType"]) == 0}


def _los_disabled(spell: int) -> bool | None:
    """Mirrors: DisableMgr.cpp:302-364 ``IsDisabledFor(DISABLE_TYPE_SPELL, id, nullptr, SPELL_DISABLE_LOS)``.

    With ``ref == nullptr``: DEPRECATED_SPELL (0x08) -> true even without the LOS bit (defect-like
    quirk), else ``flags & SPELL_DISABLE_LOS (0x40)``.  Load filter DisableMgr.cpp:103-112
    (flags 0 / > MAX skipped).  None when the world `disables` table is not in the overlay.
    """
    rows = _disables()
    if rows is None:
        return None
    flags = rows.get(spell)
    if flags is None or not flags or flags > 0x3FF:
        return False
    return bool(flags & 0x08) or bool(flags & 0x40)


#: override key that supplies the corrected value of a LoadSpellInfoCorrections member
CORRECTION_OVERRIDES = {"MaxAffectedTargets": "max_affected_targets"}


def for_world(world) -> SpellView:
    """The view a fixture names: a synthetic block, or a real spell with an optional ``override``.

    ``override`` keys: ``positive``, ``los_disabled``, ``max_affected_targets``, ``attributes_cu``
    (names), ``script_hooks``, and ``accept_unapplied_corrections`` (list of correction members the
    fixture declares irrelevant).  A LoadSpellInfoCorrections write that the view does not apply is
    accepted only when an override supplies its corrected value (``CORRECTION_OVERRIDES``) or the
    fixture lists it in ``accept_unapplied_corrections``; the view records both in ``notes``.
    """
    block = world.spell
    if block.get("synthetic"):
        return from_fixture(block)
    if "id" not in block:
        raise FailClosed("fixture: spell block names neither a synthetic spell nor an id")
    spell, difficulty = int(block["id"]), int(block.get("difficulty", 0))
    override = dict(block.get("override") or {})
    accepted = set(override.pop("accept_unapplied_corrections", []))
    from .attributes import corrections
    unapplied = {w["member"] for w in corrections(spell) if w["member"] not in ("TargetA", "TargetB", "Effect")}
    uncovered = {m for m in unapplied if m not in accepted and CORRECTION_OVERRIDES.get(m) not in override}
    if uncovered:
        raise FailClosed(f"spell view {spell}: LoadSpellInfoCorrections members {sorted(uncovered)} are not applied; "
                         "state the corrected value in spell.override or list them in accept_unapplied_corrections")
    view = from_data(spell, difficulty, allow_corrections=bool(unapplied))
    if override:
        conv = {"positive": bool, "los_disabled": bool, "max_affected_targets": int,
                "attributes_cu": lambda v: sum(CU_ATTR[n] for n in v),
                "script_hooks": lambda v: tuple(_norm_hook(h) for h in v)}
        unknown = set(override) - set(conv)
        if unknown:
            raise FailClosed(f"fixture: unsupported spell override keys {sorted(unknown)}")
        notes = tuple(view.notes) + tuple(f"override {k}={override[k]!r}" for k in sorted(override)) + \
            tuple(f"correction {m} accepted unapplied by fixture" for m in sorted(accepted & unapplied))
        view = replace(view, source="db2+override", notes=notes, **{k: conv[k](v) for k, v in override.items()})
    return view


# ---------------------------------------------------------------------------
# shared runtime SpellInfo arithmetic
# ---------------------------------------------------------------------------

UNIT_KINDS = ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle")


def spell_mod_owner(world, caster_id: str) -> str | None:
    """Mirrors: Object.cpp:1648 ``WorldObject::GetSpellModOwner``."""
    a = world.actor(caster_id)
    if a.kind == "player":
        return a.id
    if a.kind in ("pet", "totem", "gameobject"):
        owner = a.owner
        if owner is None:
            if "owner" in a.facts and a.facts["owner"] is None:
                return None
            raise FailClosed(f"fixture: {a.kind} {a.id!r} does not state its owner (spell-mod owner)")
        return owner if world.actor(owner).kind == "player" else None
    if a.kind in ("creature", "guardian", "minion", "vehicle", "corpse", "dynamicobject", "areatrigger"):
        # IsPet() is false for these kinds; guardians/minions are not pets.
        return None
    raise FailClosed(f"spell mod owner: unknown kind {a.kind!r}")


def apply_spell_mod(world, caster_id: str | None, op: str, base: float, *, is_float: bool = True) -> float:
    """Mirrors: Player.cpp:22844 ``Player::ApplySpellMod`` -- ``T((double(base) + int32 flat) * float mul)``.

    The fixture's ``modifiers[op]`` states the aggregate ``{"flat": int, "pct": float}`` of
    ``GetSpellModValues`` (or a bare int meaning flat only).  No mod owner -> no change.
    """
    if caster_id is None or spell_mod_owner(world, caster_id) is None:
        return base
    if op not in world.modifiers:
        raise FailClosed(f"fixture: spell mod {op!r} applies (caster has a mod owner) but `modifiers.{op}` is not stated")
    m = world.modifiers[op]
    if isinstance(m, dict):
        flat, pct = m.get("flat", 0), m.get("pct", 1.0)
    else:
        flat, pct = m, 1.0
    if int(flat) != flat:
        raise FailClosed(f"fixture: modifiers.{op}.flat must be an int32 (totalflat)")
    value = (float(base) + int(flat)) * float(f32(pct))
    if is_float:
        return f32(value)
    return int(value)  # T(double) truncation toward zero for int types


def calc_radius(world, sv: SpellView, eff: EffectView, target_index: str, caster_id: str | None,
                trace=None) -> tuple[float, float]:
    """``SpellEffectInfo::CalcRadius`` -> (Min, Max) as binary32.

    Mirrors: SpellInfo.cpp:783-829.
    ``caster_id=None`` models ``CalcRadius(nullptr, ...)`` (e.g. Spell.cpp:1673).
    The ``*_RANDOM`` targets consume one ``rand_norm()`` draw from the fixture (a float in [0,1)).
    """
    entry = eff.radius_a
    target = eff.target_a
    if target_index == "B" and eff.radius_b is not None:
        entry = eff.radius_b
        target = eff.target_b
    if entry is None:
        return (0.0, 0.0)
    from . import geometry as g
    rmin, rmax = entry.min, entry.max
    if caster_id is not None:
        caster = world.actor(caster_id)
        if caster.kind in UNIT_KINDS:
            # std::min(Radius + RadiusPerLevel * uint8 level, RadiusMax): float arithmetic
            level = caster.fact("level") if entry.per_level != 0.0 else caster.facts.get("level", 0)
            rmax = min(g.add(entry.radius, g.mul(entry.per_level, float(level))), rmax)
        rmax = apply_spell_mod(world, caster_id, "radius", rmax)
        if not sv.has_attr("SPELL_ATTR9_NO_MOVEMENT_RADIUS_BONUS") and caster.kind in UNIT_KINDS:
            # Spell.cpp:7322 CanIncreaseRangeByMovement: (FORWARD|STRAFE|FALLING) && !walking -> fixture fact.
            if caster.fact("range_movement_bonus"):
                rmin = max(g.sub(rmin, 2.0), 0.0)
                rmax = g.add(rmax, 2.0)
    if target in (72, 74, 86):  # TARGET_DEST_CASTER_RANDOM / TARGET_DEST_TARGET_RANDOM / TARGET_DEST_DEST_RANDOM
        from .rng import rand_norm
        draw = rand_norm(world, "CalcRadius")        # float rand_norm() (Random.cpp:75)
        rmax = g.mul(g.sub(rmax, rmin), g.sqrt(draw))  # std::sqrt(float) -> float
        if trace is not None:
            trace.add("calc_radius.random", "SpellInfo.cpp:825-827", output=rmax, draws=[draw],
                      defect="TG-C-D06/TG-D-DEF-03" if rmin else None,
                      notes=["Min is not added back: Max=(Max-Min)*sqrt(rand_norm)"])
    return (rmin, rmax)


def max_range(world, sv: SpellView, positive: bool, caster_id: str | None) -> float:
    """Mirrors: SpellInfo.cpp:3949 ``SpellInfo::GetMaxRange(positive, caster)``."""
    if sv.range is None:
        return 0.0
    value = sv.range.max[1 if positive else 0]
    if caster_id is not None:
        value = apply_spell_mod(world, caster_id, "range", value)
    return value


def min_max_range(world, sv: SpellView, positive: bool, caster_id: str | None) -> tuple[float, float]:
    """Mirrors: SpellInfo.cpp:3961 ``SpellInfo::GetMinMaxRange``."""
    if sv.range is None:
        return (0.0, 0.0)
    lo, hi = sv.range.min[1 if positive else 0], sv.range.max[1 if positive else 0]
    if caster_id is not None:
        hi = apply_spell_mod(world, caster_id, "range", hi)
    return (lo, hi)


