"""Track J: full current-data census of aura providers along lifecycle axes.

Every provider spell of :mod:`aura_lifecycle.providers` (``DIFFICULTY_NONE``)
is projected onto a fixed set of *lifecycle axes* -- the authored fields that
select a different path through Trinity's aura lifecycle (identity, duration,
stacks, charges, periodic, removal, recipients) -- plus the external
server-side surfaces that can override authored behaviour.  The projection is
pure over an :class:`~aura_lifecycle.context.AuraData`-shaped reader, so the
same code runs on the primary snapshot and on the drift build.

Nothing here claims executable support: an axis value records *which authored
input is present*, never that any engine consumes it correctly.

Axis vocabulary (spell level)
-----------------------------
``passive``            ``passive.effective_passive`` (ATTR0_PASSIVE + SpellMgr corrections); ``undetermined`` w/o SpellMisc
``duration``           duration family (:func:`duration_family`)
``pvp_duration``       ``SpellMisc.PvPDurationIndex != 0`` (Trinity never reads it)
``channel``            ``SpellInfo::IsChanneled``
``stack_capacity``     ``SpellAuraOptions.CumulativeAura`` bucket ``0`` / ``1`` / ``>1``
``proc_charges``       ``SpellAuraOptions.ProcCharges`` bucket ``0`` / ``1`` / ``>1``
``aura_options``       shape of the ``SpellAuraOptions`` row (which fields are nonzero)
``restrictions``       nonzero ``SpellAuraRestrictions`` columns
``shapeshift``         ``SpellShapeshift`` requires / excludes forms
``equipment``          ``SpellEquippedItems`` requirement present
``interrupts``         ``SpellInterrupts`` aura / channel interrupt flag words nonzero
``dispel``             ``SpellCategories.DispelType`` (raw) and mechanic presence
``ranked``             ``SpellCatalog.is_ranked``
``effects``            count bucket of provider effects, and whether non-aura sibling effects exist
``attrs``              lifecycle attributes present (:data:`LIFECYCLE_ATTRS`)
``external``           externally touched lifecycle surfaces ``surface:policy`` (Track H, :func:`external_axis`)

Axis vocabulary (effect level, one tuple per provider effect)
-------------------------------------------------------------
``kind``      unit / pet / area:<KIND> / persistent-area
``class``     lifecycle class of the subtype: periodic / absorb-depletable / other
``period``    ``EffectAuraPeriod`` present
``target``    ``ImplicitTarget_0``: caster (1) / none (0) / other
``points``    EffectAttributes SuppressPointsStacking / AuraPointsStack (:data:`EFFECT_POINTS_BITS`)
``aura``      raw ``EffectAura`` -- only in the *exact* granularity
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from procs.enums import AREA_AURA_EFFECTS, aura, effect, effect_name, is_aura_effect

from . import FailClosed

# ---------------------------------------------------------------------------
# vocabularies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleAttr:
    label: str          # short axis label
    word: int
    bit: int
    trinity: str        # SharedDefines.h name at the pin ("" when Trinity leaves the bit UNK)
    core_raw: int       # Core SpellAttributeKind raw (word*32 + bit index)
    topic: str          # owning track topic (dependency edge, not a claim)


def _raw(word: int, bit: int) -> int:
    return word * 32 + bit.bit_length() - 1


def _la(label: str, word: int, bit: int, trinity: str, topic: str) -> LifecycleAttr:
    return LifecycleAttr(label, word, bit, trinity, _raw(word, bit), topic)


#: Lifecycle-relevant attribute bits.  Trinity names are SharedDefines.h at the
#: pin (lines 437-975); the three Trinity-UNK bits carry the client names Core's
#: ``crates/dbc/src/spell_attribute.rs`` uses (raws 344, 428, 489, 490).
LIFECYCLE_ATTRS: tuple[LifecycleAttr, ...] = (
    _la("PASSIVE", 0, 0x00000040, "SPELL_ATTR0_PASSIVE", "G"),
    _la("NOT_SHAPESHIFTED", 0, 0x00010000, "SPELL_ATTR0_NOT_SHAPESHIFTED", "E"),
    _la("HEARTBEAT_RESIST", 0, 0x40000000, "SPELL_ATTR0_HEARTBEAT_RESIST", "E"),
    _la("NO_AURA_CANCEL", 0, 0x80000000, "SPELL_ATTR0_NO_AURA_CANCEL", "E"),
    _la("IS_CHANNELLED", 1, 0x00000004, "SPELL_ATTR1_IS_CHANNELLED", "B"),
    _la("IS_SELF_CHANNELLED", 1, 0x00000040, "SPELL_ATTR1_IS_SELF_CHANNELLED", "B"),
    _la("AURA_UNIQUE", 1, 0x00000800, "SPELL_ATTR1_AURA_UNIQUE", "A"),
    _la("IMMUNITY_PURGES_EFFECT", 1, 0x00008000, "SPELL_ATTR1_IMMUNITY_PURGES_EFFECT", "E"),
    _la("FINISHING_MOVE_DURATION", 1, 0x00400000, "SPELL_ATTR1_FINISHING_MOVE_DURATION", "B"),
    _la("IGNORE_OWNERS_DEATH", 1, 0x00800000, "SPELL_ATTR1_IGNORE_OWNERS_DEATH", "E"),
    _la("AURA_STAYS_AFTER_COMBAT", 1, 0x02000000, "SPELL_ATTR1_AURA_STAYS_AFTER_COMBAT", "E"),
    _la("DISPEL_ALL_STACKS", 1, 0x40000000, "SPELL_ATTR1_DISPEL_ALL_STACKS", "E"),
    _la("ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM", 2, 0x00080000,
        "SPELL_ATTR2_ALLOW_WHILE_NOT_SHAPESHIFTED_CASTER_FORM", "E"),
    _la("CANT_CRIT", 2, 0x20000000, "SPELL_ATTR2_CANT_CRIT", "D"),
    _la("DOT_STACKING_RULE", 3, 0x00000080, "SPELL_ATTR3_DOT_STACKING_RULE", "A"),
    _la("ALLOW_AURA_WHILE_DEAD", 3, 0x00100000, "SPELL_ATTR3_ALLOW_AURA_WHILE_DEAD", "E"),
    _la("TREAT_AS_PERIODIC", 3, 0x02000000, "SPELL_ATTR3_TREAT_AS_PERIODIC", "D"),
    _la("CANNOT_BE_STOLEN", 4, 0x00000040, "SPELL_ATTR4_CANNOT_BE_STOLEN", "E"),
    _la("AURA_EXPIRES_OFFLINE", 4, 0x00000004, "SPELL_ATTR4_AURA_EXPIRES_OFFLINE", "E"),
    _la("ALLOW_ENTERING_ARENA", 4, 0x00200000, "SPELL_ATTR4_ALLOW_ENTERING_ARENA", "E"),
    _la("REMOVE_ENTERING_ARENA", 5, 0x00000004, "SPELL_ATTR5_REMOVE_ENTERING_ARENA", "E"),
    _la("LIMIT_N", 5, 0x00000020, "SPELL_ATTR5_LIMIT_N", "A"),
    _la("EXTRA_INITIAL_PERIOD", 5, 0x00000200, "SPELL_ATTR5_EXTRA_INITIAL_PERIOD", "D"),
    _la("SPELL_HASTE_AFFECTS_PERIODIC", 5, 0x00002000, "SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC", "D"),
    _la("AURA_UNIQUE_PER_CASTER", 5, 0x20000000, "SPELL_ATTR5_AURA_UNIQUE_PER_CASTER", "A"),
    _la("NO_TARGET_DURATION_MOD", 7, 0x00000002, "SPELL_ATTR7_NO_TARGET_DURATION_MOD", "B"),
    _la("DISABLE_AURA_WHILE_DEAD", 7, 0x00000004, "SPELL_ATTR7_DISABLE_AURA_WHILE_DEAD", "E"),
    _la("DISPEL_REMOVES_CHARGES", 7, 0x00000400, "SPELL_ATTR7_DISPEL_REMOVES_CHARGES", "C"),
    _la("REMOVE_OUTSIDE_DUNGEONS_AND_RAIDS", 8, 0x00000004, "SPELL_ATTR8_REMOVE_OUTSIDE_DUNGEONS_AND_RAIDS", "E"),
    _la("PERIODIC_CAN_CRIT", 8, 0x00000200, "SPELL_ATTR8_PERIODIC_CAN_CRIT", "D"),
    _la("HASTE_AFFECTS_DURATION", 8, 0x00020000, "SPELL_ATTR8_HASTE_AFFECTS_DURATION", "B"),
    _la("MELEE_HASTE_AFFECTS_PERIODIC", 8, 0x00400000, "SPELL_ATTR8_MELEE_HASTE_AFFECTS_PERIODIC", "D"),
    _la("MASTERY_AFFECTS_POINTS", 8, 0x20000000, "SPELL_ATTR8_MASTERY_AFFECTS_POINTS", "D"),
    _la("ROLLING_PERIODIC", 10, 0x00004000, "SPELL_ATTR10_ROLLING_PERIODIC", "B"),
    _la("UPDATE_PASSIVES_ON_APPLY_REMOVE", 10, 0x01000000, "", "G"),
    _la("DO_NOT_CONSUME_AURA_STACK_ON_PROC", 13, 0x00001000, "", "C"),
    _la("PERIODIC_REFRESH_EXTENDS_DURATION", 13, 0x00100000, "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION", "B"),
    _la("AURA_DOES_NOT_REFRESH", 15, 0x00000200, "", "B"),
    _la("ASYNCHRONOUS_STACKING_BUFF", 15, 0x00000400, "", "C"),
)
ATTR_BY_LABEL = {a.label: a for a in LIFECYCLE_ATTRS}

#: Server-derived custom attribute (no DB2 bit, no Core raw): ``SPELL_ATTR0_CU_AURA_CANNOT_BE_SAVED``,
#: read by Aura::CanBeSaved (SpellAuras.cpp:1193).  Set by SpellMgr::LoadSpellInfoCustomAttributes for
#: any effect with one of these subtypes (SpellMgr.cpp:3054-3067), for AuraInterruptFlags LeaveWorld
#: (SpellMgr.cpp:3306-3308, SpellDefines.h:99), for LiquidType spells (SpellMgr.cpp:3352-3357 -- LiquidType
#: is not read here: reported as a coverage gap) and by ApplySpellFix 404468 (SpellMgr.cpp:5218-5221).
CU_AURA_CANNOT_BE_SAVED = "CU_AURA_CANNOT_BE_SAVED"
CANNOT_BE_SAVED_AURAS = frozenset(aura(n) for n in (
    "OPEN_STABLE", "CONTROL_VEHICLE", "BIND_SIGHT", "MOD_POSSESS", "MOD_POSSESS_PET", "MOD_CHARM", "AOE_CHARM",
    "BATTLEGROUND_PLAYER_POSITION", "BATTLEGROUND_PLAYER_POSITION_FACTIONAL"))
AURA_INTERRUPT_LEAVE_WORLD = 0x00080000
CANNOT_BE_SAVED_FIXES = frozenset({404468})

#: SpellEffectAttributes bits the aura code reads (DBCEnums.h:2409/2412):
#: SuppressPointsStacking -- SpellAuraEffects.cpp:844/880 (amount not multiplied by stacks);
#: AuraPointsStack -- Unit.cpp:3423 (refresh adds the remaining amount to the new aura).
EFFECT_POINTS_BITS = {"suppress-points-stacking": 0x40, "aura-points-stack": 0x200}


def cannot_be_saved(spell: int, effects: list[dict], interrupts_row: dict | None) -> bool:
    """Mirrors: SpellMgr.cpp:3054-3067, 3306-3308, 5218-5221 (LiquidType branch not covered)."""
    if spell in CANNOT_BE_SAVED_FIXES:
        return True
    if any(int(e.get("EffectAura") or 0) in CANNOT_BE_SAVED_AURAS for e in effects):
        return True
    return bool(interrupts_row and int(interrupts_row.get("AuraInterruptFlags_0") or 0) & AURA_INTERRUPT_LEAVE_WORLD)


def effect_points(e: dict) -> str:
    bits = int(e.get("EffectAttributes") or 0)
    names = [k for k, v in EFFECT_POINTS_BITS.items() if bits & v]
    return "+".join(names) or "plain"

#: ``AuraEffect::CalculatePeriodic`` sets ``m_isPeriodic`` for exactly these subtypes.
#: Mirrors: SpellAuraEffects.cpp:966-984
PERIODIC_AURAS = frozenset(aura(n) for n in (
    "OBS_MOD_POWER", "PERIODIC_DAMAGE", "PERIODIC_HEAL", "OBS_MOD_HEALTH", "PERIODIC_TRIGGER_SPELL",
    "PERIODIC_TRIGGER_SPELL_FROM_CLIENT", "PERIODIC_ENERGIZE", "PERIODIC_LEECH", "PERIODIC_HEALTH_FUNNEL",
    "PERIODIC_MANA_LEECH", "PERIODIC_DAMAGE_PERCENT", "POWER_BURN", "PERIODIC_DUMMY",
    "PERIODIC_TRIGGER_SPELL_WITH_VALUE"))

#: Subtypes whose aura Trinity removes when the absorb amount reaches 0.
#: Mirrors: Unit.cpp:1899/1945 (SCHOOL_ABSORB), 1969/2022 (MANA_SHIELD), 2114/2155 (SCHOOL_HEAL_ABSORB)
ABSORB_AURAS = frozenset(aura(n) for n in ("SCHOOL_ABSORB", "MANA_SHIELD", "SCHOOL_HEAL_ABSORB"))

APPLY_AURA = effect("APPLY_AURA")
APPLY_AURA_ON_PET = effect("APPLY_AURA_ON_PET")
PERSISTENT_AREA_AURA = effect("PERSISTENT_AREA_AURA")
PET_TARGET_EFFECTS = frozenset({APPLY_AURA_ON_PET} | {effect(n) for n in (
    "APPLY_AREA_AURA_PET", "APPLY_AREA_AURA_OWNER", "APPLY_AREA_AURA_SUMMONS")})

TARGET_UNIT_CASTER = 1

RESTRICTION_COLUMNS = ("CasterAuraState", "TargetAuraState", "ExcludeCasterAuraState", "ExcludeTargetAuraState",
                       "CasterAuraSpell", "TargetAuraSpell", "ExcludeCasterAuraSpell", "ExcludeTargetAuraSpell",
                       "CasterAuraType", "TargetAuraType", "ExcludeCasterAuraType", "ExcludeTargetAuraType")

SPELL_AXES = ("passive", "duration", "pvp_duration", "channel", "stack_capacity", "proc_charges",
              "aura_options", "restrictions", "shapeshift", "equipment", "interrupts", "dispel",
              "ranked", "effects", "attrs", "external")
EFFECT_AXES = ("kind", "class", "period", "target", "points")

# ---------------------------------------------------------------------------
# single-field projections
# ---------------------------------------------------------------------------


def attributes(misc: dict | None) -> tuple[int, ...]:
    """The 17 attribute words; a missing SpellMisc row reads as all-zero (flagged by :func:`duration_family`)."""
    if misc is None:
        return (0,) * 17
    return tuple(int(misc.get(f"Attributes_{i}", 0) or 0) for i in range(17))


def has_attr(words: tuple[int, ...], word: int, bit: int) -> bool:
    return word < len(words) and bool(words[word] & bit)


#: Carried by their own axis (``passive``); kept out of ``attrs`` so it is not counted twice.
AXIS_DUPLICATES = frozenset({"PASSIVE"})


def lifecycle_attrs(words: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(sorted(a.label for a in LIFECYCLE_ATTRS
                        if a.label not in AXIS_DUPLICATES and has_attr(words, a.word, a.bit)))


def trinity_consumers(tc_root, names: Iterable[str]) -> dict[str, list[str]]:
    """``file:line`` of every non-comment mention of each attribute name in the pinned Trinity tree.

    Excludes the declaration (``SharedDefines.h``) and the generated ``enuminfo_*`` tables.
    Returns ``{}`` when the tree is absent (navigation evidence is optional)."""
    import re
    from pathlib import Path
    root = Path(tc_root) / "src" / "server"
    wanted = [n for n in names if n]
    if not root.is_dir() or not wanted:
        return {}
    pat = re.compile(r"\b(" + "|".join(sorted(wanted)) + r")\b")
    out: dict[str, list[str]] = {n: [] for n in wanted}
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".cpp", ".h") or path.name == "SharedDefines.h" or path.name.startswith("enuminfo_"):
            continue
        rel = path.relative_to(root.parent.parent).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("//"):
                continue
            for m in pat.finditer(line):
                out[m.group(1)].append(f"{rel}:{i}")
    return out


def effective_passive(data, spell: int) -> bool | None:
    """The pass's single passive definition (Track G ``passive.effective_passive``: SPELL_ATTR0_PASSIVE plus
    SpellMgr load-time corrections).  ``None`` when it fails closed (no DIFFICULTY_NONE SpellMisc row)."""
    from .passive import effective_passive as _ep
    try:
        return bool(_ep(data, spell)["passive"])
    except FailClosed:
        return None


def duration_family(data, misc: dict | None, passive: bool | None = None) -> str:
    """Duration family of the base (non-PvP) duration.

    Mirrors: SpellInfo.cpp:3986-3998 (``GetDuration``/``GetMaxDuration``: no
    ``DurationEntry`` -> passive ? -1 : 0; ``-1`` is the only permanent sentinel;
    other negatives are taken ``abs``) and Object.cpp:1707-1730 (``CalcSpellDuration``:
    min..max interpolation only through ``DurationPerResource``).

    Families: ``no-misc`` (no DIFFICULTY_NONE SpellMisc row), ``implicit-permanent``
    (no entry, passive), ``implicit-zero`` (no entry, not passive), ``permanent``,
    ``zero``, ``abs-negative`` (negative non-sentinel), ``finite-fixed``,
    ``finite-max-permanent`` (finite minimum, ``MaxDuration == -1``),
    ``finite-per-resource`` (min != max, per-resource step), ``finite-varying``
    (min != max, no step: interpolation rule unknown).
    """
    if misc is None:
        return "no-misc"
    idx = int(misc.get("DurationIndex") or 0)
    entry = data.duration(idx) if idx else None
    if entry is None:
        p = has_attr(attributes(misc), 0, 0x40) if passive is None else passive
        return "implicit-permanent" if p else "implicit-zero"
    dur, mx, per = int(entry["Duration"]), int(entry["MaxDuration"]), int(entry["DurationPerResource"])
    if dur == -1:
        return "permanent"
    if dur < 0 or mx < -1:
        return "abs-negative"
    if dur == 0 and mx == 0:
        return "zero"
    if dur != mx and mx != -1:
        return "finite-per-resource" if per else "finite-varying"
    if mx == -1:
        return "finite-max-permanent"
    return "finite-fixed"


def base_duration_ms(data, misc: dict | None, passive: bool | None = None) -> int | None:
    """Mirrors: SpellInfo.cpp:3986-3991 ``GetDuration`` (``None`` when no misc row)."""
    if misc is None:
        return None
    idx = int(misc.get("DurationIndex") or 0)
    entry = data.duration(idx) if idx else None
    if entry is None:
        p = has_attr(attributes(misc), 0, 0x40) if passive is None else passive
        return -1 if p else 0
    d = int(entry["Duration"])
    return -1 if d == -1 else abs(d)


def bucket(n: int) -> str:
    return "0" if n <= 0 else "1" if n == 1 else ">1"


def aura_options_shape(opts: dict | None) -> tuple[str, ...]:
    if opts is None:
        return ("absent",)
    out = []
    if int(opts.get("ProcTypeMask_0") or 0) or int(opts.get("ProcTypeMask_1") or 0):
        out.append("proc-flags")
    chance = float(opts.get("ProcChance") or 0)
    if chance:
        out.append("chance-100+" if chance >= 100 else "chance-partial")
    if int(opts.get("SpellProcsPerMinuteID") or 0):
        out.append("rppm")
    if int(opts.get("ProcCategoryRecovery") or 0):
        out.append("icd")
    return tuple(out) or ("empty",)


def restrictions(row: dict | None) -> tuple[str, ...]:
    if row is None:
        return ()
    return tuple(c for c in RESTRICTION_COLUMNS if int(row.get(c) or 0))


def shapeshift(row: dict | None) -> tuple[str, ...]:
    if row is None:
        return ()
    out = []
    if int(row.get("ShapeshiftMask_0") or 0) or int(row.get("ShapeshiftMask_1") or 0):
        out.append("requires-form")
    if int(row.get("ShapeshiftExclude_0") or 0) or int(row.get("ShapeshiftExclude_1") or 0):
        out.append("excludes-form")
    return tuple(out)


def equipment(row: dict | None) -> bool:
    return row is not None and int(row.get("EquippedItemClass") if row.get("EquippedItemClass") is not None else -1) >= 0


def interrupts(row: dict | None) -> tuple[str, ...]:
    if row is None:
        return ()
    out = []
    if int(row.get("AuraInterruptFlags_0") or 0) or int(row.get("AuraInterruptFlags_1") or 0):
        out.append("aura")
    if int(row.get("ChannelInterruptFlags_0") or 0) or int(row.get("ChannelInterruptFlags_1") or 0):
        out.append("channel")
    return tuple(out)


def dispel(row: dict | None) -> tuple[int, bool]:
    if row is None:
        return (0, False)
    return (int(row.get("DispelType") or 0), bool(int(row.get("Mechanic") or 0)))


def effect_kind(effect_type: int) -> str:
    """Mirrors: SpellInfo.cpp:465-495 (``IsAura`` / ``IsAreaAuraEffect`` / ``IsUnitOwnedAuraEffect``)."""
    if effect_type == APPLY_AURA:
        return "unit"
    if effect_type == APPLY_AURA_ON_PET:
        return "pet"
    if effect_type == PERSISTENT_AREA_AURA:
        return "persistent-area"
    if effect_type in AREA_AURA_EFFECTS:
        return "area:" + effect_name(effect_type).removeprefix("SPELL_EFFECT_APPLY_AREA_AURA_")
    raise FailClosed(f"effect {effect_type} is not an aura-producing effect")


def subtype_class(aura_type: int) -> str:
    if aura_type in PERIODIC_AURAS:
        return "periodic"
    if aura_type in ABSORB_AURAS:
        return "absorb-depletable"
    return "other"


def effect_axes(e: dict, exact: bool = False) -> tuple:
    period = int(e.get("EffectAuraPeriod") or 0)
    t0 = int(e.get("ImplicitTarget_0") or 0)
    row = (effect_kind(int(e["Effect"])), subtype_class(int(e["EffectAura"])),
           "period" if period > 0 else "no-period",
           "caster" if t0 == TARGET_UNIT_CASTER else "none" if t0 == 0 else "other",
           effect_points(e))
    return row + (int(e["EffectAura"]),) if exact else row


# ---------------------------------------------------------------------------
# external surfaces (Track H's single definition)
# ---------------------------------------------------------------------------


def external_axis(flags: dict[str, str] | None) -> tuple[str, ...]:
    """``surface:policy`` strings of the externally touched lifecycle surfaces of one spell.

    The definition is Track H's (:func:`aura_lifecycle.overlays.surface_flags`): the
    surfaces a world overlay, SpellInfo correction, engine spell-id branch or bound
    script touches, each with its policy source.  Absence is not proof of absence
    (H's coverage list)."""
    return tuple(sorted(f"{k}:{v}" for k, v in (flags or {}).items()))


def external_flags(ctx) -> dict[int, dict[str, str]]:
    """Track H's bulk per-spell surface flags; fails closed when the overlay index is unavailable."""
    try:
        from . import overlays
    except ImportError as exc:
        raise FailClosed("aura_lifecycle.overlays (Track H) is required for the external axis") from exc
    return overlays.surface_flags(ctx)


# ---------------------------------------------------------------------------
# per-spell projection
# ---------------------------------------------------------------------------


def spell_record(data, spell: int, *, ranked: bool = False, external: tuple[str, ...] = ()) -> dict[str, Any]:
    """All lifecycle axes of one provider spell (DIFFICULTY_NONE)."""
    effects = data.effects(spell)
    prov = [e for e in effects if is_aura_effect(int(e["Effect"]), int(e["EffectAura"]))]
    if not prov:
        raise FailClosed(f"spell {spell}: no aura-producing effect at DIFFICULTY_NONE")
    misc = data.row("SpellMisc", spell)
    words = attributes(misc)
    opts = data.row("SpellAuraOptions", spell)
    dispel_type, mechanic = dispel(data.row("SpellCategories", spell))
    n = len(prov)
    passive = effective_passive(data, spell) if misc is not None else None
    intr_row = data.row("SpellInterrupts", spell)
    attrs = lifecycle_attrs(words)
    if cannot_be_saved(spell, effects, intr_row):
        attrs = tuple(sorted(attrs + (CU_AURA_CANNOT_BE_SAVED,)))
    axes = {
        "passive": "undetermined" if passive is None else passive,
        "duration": duration_family(data, misc, passive),
        "pvp_duration": bool(misc and int(misc.get("PvPDurationIndex") or 0)),
        "channel": has_attr(words, 1, 0x04 | 0x40),   # SpellInfo.cpp:1889 IsChanneled
        "stack_capacity": bucket(int(opts.get("CumulativeAura") or 0) if opts else 0),
        "proc_charges": bucket(int(opts.get("ProcCharges") or 0) if opts else 0),
        "aura_options": aura_options_shape(opts),
        "restrictions": restrictions(data.row("SpellAuraRestrictions", spell)),
        "shapeshift": shapeshift(data.row("SpellShapeshift", spell)),
        "equipment": equipment(data.row("SpellEquippedItems", spell)),
        "interrupts": interrupts(intr_row),
        "dispel": [dispel_type, mechanic],
        "ranked": ranked,
        "effects": ["1" if n == 1 else "2" if n == 2 else "3+", len(effects) > n],
        "attrs": attrs,
        "external": tuple(external),
    }
    return {
        "spell": spell,
        "axes": axes,
        "effects": sorted(effect_axes(e) for e in prov),
        "effects_exact": sorted(effect_axes(e, exact=True) for e in prov),
        "provider_indices": [int(e["EffectIndex"]) for e in prov],
        "duration_ms": base_duration_ms(data, misc, passive),
        "stack_amount": int(opts.get("CumulativeAura") or 0) if opts else 0,
        "proc_charges": int(opts.get("ProcCharges") or 0) if opts else 0,
    }


def marginals(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per-axis value counts (tuple values joined with '+', empty -> '-')."""
    out: dict[str, Counter] = {k: Counter() for k in SPELL_AXES}
    out.update({"effect." + k: Counter() for k in EFFECT_AXES})
    for r in records:
        for k in SPELL_AXES:
            v = r["axes"][k]
            if k in ("attrs", "external", "restrictions", "shapeshift", "interrupts", "aura_options"):
                for item in (v or ("-",)):
                    out[k][str(item)] += 1
            else:
                out[k][_key(v)] += 1
        for e in r["effects"]:
            for i, k in enumerate(EFFECT_AXES):
                out["effect." + k][str(e[i])] += 1
    return {k: dict(sorted(c.items())) for k, c in out.items()}


def _key(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return "+".join(_key(x) for x in v) or "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


# ---------------------------------------------------------------------------
# build drift (primary snapshot vs ctx.drift)
# ---------------------------------------------------------------------------

#: Lifecycle fields compared across builds, per table (DIFFICULTY_NONE rows).
DRIFT_FIELDS: dict[str, tuple[str, ...]] = {
    "SpellEffect": ("Effect", "EffectAura", "EffectAuraPeriod", "EffectAmplitude", "EffectAttributes",
                    "EffectTriggerSpell", "ImplicitTarget_0", "ImplicitTarget_1", "EffectRadiusIndex_0",
                    "EffectRadiusIndex_1"),
    "SpellMisc": ("DurationIndex", "PvPDurationIndex", "MinDuration", "ActiveIconFileDataID",
                  *(f"Attributes_{i}" for i in range(17))),
    "SpellAuraOptions": ("CumulativeAura", "ProcCharges", "ProcChance", "ProcCategoryRecovery",
                         "SpellProcsPerMinuteID", "ProcTypeMask_0", "ProcTypeMask_1"),
    "SpellCategories": ("DispelType", "Mechanic"),
    "SpellInterrupts": ("AuraInterruptFlags_0", "AuraInterruptFlags_1", "ChannelInterruptFlags_0",
                        "ChannelInterruptFlags_1"),
    "SpellShapeshift": ("ShapeshiftMask_0", "ShapeshiftMask_1", "ShapeshiftExclude_0", "ShapeshiftExclude_1"),
    "SpellEquippedItems": ("EquippedItemClass", "EquippedItemInvTypes", "EquippedItemSubclass"),
    "SpellAuraRestrictions": RESTRICTION_COLUMNS,
}


def _num(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def drift_fields(data, spell: int) -> dict[str, Any]:
    """Flat ``table.column`` -> value map of every lifecycle field of one spell (DIFFICULTY_NONE).

    ``SpellEffect`` fields are keyed ``SpellEffect[i].column``; the duration entry is
    resolved (``SpellDuration(base).Duration`` ...) so a changed duration *row* shows up
    on every spell that references it.  Absent tables are reported by the caller
    (``data.absent``), never compared.
    """
    out: dict[str, Any] = {}
    for e in data.effects(spell):
        i = int(e["EffectIndex"])
        for c in DRIFT_FIELDS["SpellEffect"]:
            if c in e:
                out[f"SpellEffect[{i}].{c}"] = _num(e[c])
    for table, cols in DRIFT_FIELDS.items():
        if table == "SpellEffect":
            continue
        row = data.row(table, spell)
        if row is None:
            continue
        for c in cols:
            if c in row:
                out[f"{table}.{c}"] = _num(row[c])
    misc = data.row("SpellMisc", spell)
    for label, col in (("base", "DurationIndex"), ("pvp", "PvPDurationIndex")):
        idx = int(misc.get(col) or 0) if misc else 0
        entry = data.duration(idx) if idx else None
        if entry is not None:
            for c in ("Duration", "MaxDuration", "DurationPerResource"):
                out[f"SpellDuration({label}).{c}"] = _num(entry[c])
    return out


def attr_bit_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Attribute bits flipped between builds, as Trinity/Core-style names (``+``/``-`` prefix)."""
    from procs.enums import attr_name
    out = []
    for i in range(17):
        k = f"SpellMisc.Attributes_{i}"
        a, b = int(old.get(k) or 0), int(new.get(k) or 0)
        for bit in range(32):
            m = 1 << bit
            if (a ^ b) & m:
                name = attr_name((i, m))
                lab = next((x.label for x in LIFECYCLE_ATTRS if x.word == i and x.bit == m), None)
                out.append(("+" if b & m else "-") + (f"{lab}" if lab else name))
    return out


def diff_fields(old: dict[str, Any], new: dict[str, Any]) -> list[list[Any]]:
    keys = sorted(set(old) | set(new))
    return [[k, old.get(k), new.get(k)] for k in keys if old.get(k) != new.get(k)]


# ---------------------------------------------------------------------------
# whole-census build and candidate rules
# ---------------------------------------------------------------------------


def build(ctx, data=None, flags: dict[int, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Records for every provider spell of ``data`` (default: the primary snapshot).

    ``flags`` defaults to :func:`external_flags` (Track H); pass it to reuse one computation."""
    from . import providers
    data = data if data is not None else ctx.data
    flags = flags if flags is not None else external_flags(ctx)
    cat = ctx.catalog
    return [spell_record(data, s, ranked=cat.is_ranked(s), external=external_axis(flags.get(s)))
            for s in providers.provider_spells(data)]


def derived_predicates(rec: dict[str, Any]) -> dict[str, bool]:
    """Trinity ``SpellInfo`` lifecycle predicates evaluable from the census axes.

    Mirrors: SpellInfo.cpp:1808-1812 (``IsStackableOnOneSlotWithDifferentCasters``),
    SpellInfo.cpp:1803-1806 (``IsMultiSlotAura`` -- passive part only; the three
    hard-coded ids are reported separately), SpellInfo.cpp:1828-1831
    (``IsDeathPersistent``), SpellInfo.cpp:2052-2059 (``IsSingleTarget``),
    SpellInfo.cpp:1798-1801 (``IsPassiveStackableWithRanks``).
    """
    a = rec["axes"]
    attrs = set(a["attrs"])
    kinds = {e[0] for e in rec["effects"]}
    return {
        "stackable_on_one_slot_different_casters": rec["stack_amount"] > 1 and not a["channel"]
        and "DOT_STACKING_RULE" not in attrs,
        "multi_slot_passive": a["passive"] is True,
        "death_persistent": "ALLOW_AURA_WHILE_DEAD" in attrs,
        "single_target": "LIMIT_N" in attrs,
        "passive_stackable_with_ranks_candidate": a["passive"] is True and "unit" not in kinds,
    }


def _has_periodic(rec) -> bool:
    return any(e[1] == "periodic" for e in rec["effects"])


#: Candidate census rules: (id suffix, name, definition, predicate(rec) -> applies, holds(rec) -> bool).
RULES: tuple[tuple[str, str, str, Any, Any], ...] = (
    ("01", "periodic-subtype-has-period",
     "every provider effect whose subtype is in AuraEffect::CalculatePeriodic's list authors EffectAuraPeriod > 0",
     _has_periodic, lambda r: all(e[2] == "period" for e in r["effects"] if e[1] == "periodic")),
    ("02", "nonperiodic-subtype-has-no-period",
     "no provider effect outside the periodic list authors EffectAuraPeriod > 0",
     lambda r: True, lambda r: all(e[2] == "no-period" for e in r["effects"] if e[1] != "periodic")),
    ("03", "passive-is-permanent",
     "a SPELL_ATTR0_PASSIVE provider has a permanent duration (explicit -1 or no DurationEntry)",
     lambda r: r["axes"]["passive"] is True, lambda r: r["axes"]["duration"] in ("permanent", "implicit-permanent")),
    ("04", "charges-imply-proc-flags",
     "SpellAuraOptions.ProcCharges > 0 implies a nonzero ProcTypeMask",
     lambda r: r["proc_charges"] > 0, lambda r: "proc-flags" in r["axes"]["aura_options"]),
    ("05", "dot-stacking-rule-on-periodic",
     "SPELL_ATTR3_DOT_STACKING_RULE only occurs on providers with a periodic-class effect",
     lambda r: "DOT_STACKING_RULE" in r["axes"]["attrs"], _has_periodic),
    ("06", "pandemic-on-periodic",
     "SPELL_ATTR13_PERIODIC_REFRESH_EXTENDS_DURATION only occurs on providers with a periodic-class effect",
     lambda r: "PERIODIC_REFRESH_EXTENDS_DURATION" in r["axes"]["attrs"], _has_periodic),
    ("07", "rolling-on-periodic",
     "SPELL_ATTR10_ROLLING_PERIODIC only occurs on providers with a periodic-class effect",
     lambda r: "ROLLING_PERIODIC" in r["axes"]["attrs"], _has_periodic),
    ("08", "periodic-haste-on-periodic",
     "SPELL_ATTR5_SPELL_HASTE_AFFECTS_PERIODIC / ATTR8_MELEE_HASTE_AFFECTS_PERIODIC only on providers with a periodic-class effect",
     lambda r: bool({"SPELL_HASTE_AFFECTS_PERIODIC", "MELEE_HASTE_AFFECTS_PERIODIC"} & set(r["axes"]["attrs"])),
     _has_periodic),
    ("09", "extra-initial-period-on-periodic",
     "SPELL_ATTR5_EXTRA_INITIAL_PERIOD only on providers with a periodic-class effect",
     lambda r: "EXTRA_INITIAL_PERIOD" in r["axes"]["attrs"], _has_periodic),
    ("10", "channel-is-finite",
     "a channelled provider (IsChanneled) has a finite duration family",
     lambda r: r["axes"]["channel"], lambda r: r["axes"]["duration"].startswith("finite")),
    ("11", "stack-capacity-gt1-not-passive",
     "a provider with CumulativeAura > 1 is not passive",
     lambda r: r["stack_amount"] > 1, lambda r: r["axes"]["passive"] is not True),
    ("12", "haste-duration-on-finite",
     "SPELL_ATTR8_HASTE_AFFECTS_DURATION only on finite-duration providers",
     lambda r: "HASTE_AFFECTS_DURATION" in r["axes"]["attrs"], lambda r: r["axes"]["duration"].startswith("finite")),
)


def evaluate_rules(records: list[dict[str, Any]], pops: dict[str, frozenset[int]],
                   witnesses: int = 8, skew: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    """Each candidate rule's population / counterexample counts per population, with the build-skewed
    share of both (spells newer than pinned Trinity's supported build)."""
    out = []
    for suffix, name, definition, applies, holds in RULES:
        per: dict[str, dict[str, Any]] = {}
        for p, pop in pops.items():
            app = [r for r in records if r["spell"] in pop and applies(r)]
            bad = sorted(r["spell"] for r in app if not holds(r))
            per[p] = {"population": len(app), "counterexamples": len(bad), "witnesses": bad[:witnesses],
                      "population_build_skew": sum(1 for r in app if r["spell"] in skew),
                      "counterexamples_build_skew": sum(1 for s in bad if s in skew)}
        out.append({"suffix": suffix, "name": name, "definition": definition, "per_population": per})
    return out
