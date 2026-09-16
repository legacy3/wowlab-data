"""Effect population: which authored effects have no complete generic consumer.

Effect identity is the ``SpellEffect`` row (``ID``), kept distinct from the
owner ``SpellID``.  The population is built for ``DifficultyID = 0`` rows (what
Trinity keys as ``DIFFICULTY_NONE``); other difficulties are counted separately.

Population classes (each row may carry several):

``dummy-effect``          ``SPELL_EFFECT_DUMMY`` -- ``Spell::EffectDummy`` only handles
                          ``spell_pet_auras`` rows; otherwise nothing.
``script-effect``         ``SPELL_EFFECT_SCRIPT_EFFECT`` -- ``Spell::EffectScriptEffect``
                          is a hardcoded SpellID switch; otherwise nothing.
``dummy-aura``            ``SPELL_AURA_DUMMY`` on any aura-applying effect --
                          ``AuraEffect::HandleAuraDummy`` (pet auras + hardcoded switch),
                          ``HandleProc`` treats it like PROC_TRIGGER_SPELL.
``periodic-dummy``        ``SPELL_AURA_PERIODIC_DUMMY`` -- ``PeriodicTick`` does nothing
                          ("handled via scripts").
``trigger-no-trigger``    a trigger-type effect/aura whose ``EffectTriggerSpell`` is 0
                          (handler logs and returns).
``trigger-missing``       trigger-type with a ``TriggerSpell`` that does not exist.
``unimplemented-effect``  effect type dispatched to ``Spell::EffectNULL``/``EffectUnused``.
``unimplemented-aura``    aura type dispatched to ``AuraEffect::HandleNULL``/``HandleUnused``
                          (client-side or dead), and not covered by another consumer note.

For every effect the record also names the *candidate consumers* the evidence
corpora show for its owner: script bindings (per hook affected mask), engine
hardcoded cases, corrections, ``spell_pet_auras``, ``spell_linked_spell``,
conditions, areatrigger scripts, server-side spell status and the DB2 marker
consumers (``SpellAuraRestrictions.*AuraSpell``).  Consumer *presence* is
recorded here; what each consumer does is the job of :mod:`bindings`,
:mod:`families` and :mod:`hardcoded`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from procs.enums import aura, aura_name, effect, effect_name
from procs.spells import DIFFICULTY_NONE, EffectInfo

from .loaders import Bundle

SPELL_EFFECT_DUMMY = effect("DUMMY")
SPELL_EFFECT_SCRIPT_EFFECT = effect("SCRIPT_EFFECT")
SPELL_EFFECT_CREATE_AREATRIGGER = effect("CREATE_AREATRIGGER")
SPELL_EFFECT_CREATE_AREATRIGGER_2 = effect("CREATE_AREATRIGGER_2")
SPELL_AURA_DUMMY = aura("DUMMY")
SPELL_AURA_PERIODIC_DUMMY = aura("PERIODIC_DUMMY")

#: Effect types whose handler casts ``EffectTriggerSpell`` (SpellEffects.cpp).
TRIGGER_EFFECTS = {effect(n) for n in (
    "TRIGGER_SPELL", "TRIGGER_MISSILE", "FORCE_CAST", "FORCE_CAST_WITH_VALUE",
    "TRIGGER_SPELL_WITH_VALUE", "TRIGGER_MISSILE_SPELL_WITH_VALUE", "TRIGGER_SPELL_2",
    "FORCE_CAST_2", "LEARN_SPELL")} | {73}  # 73 = SPELL_EFFECT_TRIGGER_RITUAL_OF_SUMMONING (SharedDefines.h)
#: Aura types whose handler casts ``EffectTriggerSpell`` (SpellAuraEffects.cpp).
TRIGGER_AURAS = {aura(n) for n in (
    "PERIODIC_TRIGGER_SPELL", "PERIODIC_TRIGGER_SPELL_WITH_VALUE", "PROC_TRIGGER_SPELL",
    "PROC_TRIGGER_SPELL_WITH_VALUE", "LINKED", "LINKED_2", "TRIGGER_SPELL_ON_EXPIRE",
    "TRIGGER_SPELL_ON_POWER_PCT", "TRIGGER_SPELL_ON_POWER_AMOUNT", "TRIGGER_SPELL_ON_HEALTH_PCT",
    "PERIODIC_TRIGGER_SPELL_FROM_CLIENT")}
#: PERIODIC_TRIGGER_SPELL_FROM_CLIENT is intentionally client-driven (PeriodicTick comment).
CLIENT_DRIVEN_AURAS = {aura("PERIODIC_TRIGGER_SPELL_FROM_CLIENT")}
UNIMPLEMENTED_EFFECT_HANDLERS = {"EffectNULL", "EffectUnused"}
UNIMPLEMENTED_AURA_HANDLERS = {"HandleNULL", "HandleUnused"}

CLASSES = ("dummy-effect", "script-effect", "dummy-aura", "periodic-dummy", "trigger-no-trigger",
           "trigger-missing", "unimplemented-effect", "unimplemented-aura")


@dataclass
class EffectRecord:
    row_id: int
    spell_id: int
    difficulty: int
    index: int
    effect: int
    aura: int
    trigger_spell: int
    misc0: int
    misc1: int
    base_points: float
    target_a: int
    target_b: int
    classes: list[str]
    generic_handler: str
    handler_note: str | None
    consumers: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["effect_name"] = effect_name(self.effect)
        d["aura_name"] = aura_name(self.aura) if self.aura else None
        return d


class Population:
    def __init__(self, bundle: Bundle) -> None:
        self.b = bundle
        self.records: dict[int, EffectRecord] = {}
        self.by_spell: dict[int, list[int]] = defaultdict(list)
        self.other_difficulty: Counter = Counter()
        self._build()

    def _classify(self, eff: EffectInfo) -> tuple[list[str], str, str | None]:
        d = self.b.dispatch
        classes: list[str] = []
        handler = d.effect_handler(eff.effect)
        note = None
        if eff.effect == SPELL_EFFECT_DUMMY:
            classes.append("dummy-effect")
        elif eff.effect == SPELL_EFFECT_SCRIPT_EFFECT:
            classes.append("script-effect")
        elif eff.effect in TRIGGER_EFFECTS:
            if not eff.trigger_spell:
                classes.append("trigger-no-trigger")
            elif not self.b.catalog.exists(eff.trigger_spell):
                classes.append("trigger-missing")
        elif handler in UNIMPLEMENTED_EFFECT_HANDLERS and eff.effect != 0 and not eff.is_aura:
            # aura-carrying effects are consumed by Aura creation, not the effect handler table
            classes.append("unimplemented-effect")
        if eff.is_aura:
            arow = d.aura_handler(eff.aura)
            handler = f"{handler}->{arow['handler']}"
            note = arow.get("note")
            if eff.aura == SPELL_AURA_DUMMY:
                classes.append("dummy-aura")
            elif eff.aura == SPELL_AURA_PERIODIC_DUMMY:
                classes.append("periodic-dummy")
            elif eff.aura in TRIGGER_AURAS and eff.aura not in CLIENT_DRIVEN_AURAS:
                if not eff.trigger_spell:
                    classes.append("trigger-no-trigger")
                elif not self.b.catalog.exists(eff.trigger_spell):
                    classes.append("trigger-missing")
            elif arow["handler"] in UNIMPLEMENTED_AURA_HANDLERS and not note:
                classes.append("unimplemented-aura")
        return classes, handler, note

    def _build(self) -> None:
        cat = self.b.catalog
        for (spell, diff), effects in cat.effects.items():
            if spell not in cat.names:
                continue
            for eff in effects.values():
                classes, handler, note = self._classify(eff)
                if not classes:
                    continue
                if diff != DIFFICULTY_NONE:
                    for c in classes:
                        self.other_difficulty[c] += 1
                    continue
                rec = EffectRecord(row_id=eff.row_id, spell_id=spell, difficulty=diff, index=eff.index,
                                   effect=eff.effect, aura=eff.aura, trigger_spell=eff.trigger_spell,
                                   misc0=eff.misc0, misc1=eff.misc1, base_points=eff.base_points,
                                   target_a=eff.target_a, target_b=eff.target_b, classes=classes,
                                   generic_handler=handler, handler_note=note)
                self.records[eff.row_id] = rec
                self.by_spell[spell].append(eff.row_id)
        self.by_spell = dict(self.by_spell)  # plain dict: membership tests must never insert keys

    # ------------------------------------------------------------------
    def counts(self, spells: set[int] | None = None) -> dict[str, Any]:
        recs = [r for r in self.records.values() if spells is None or r.spell_id in spells]
        per_class: Counter = Counter()
        owners: dict[str, set[int]] = defaultdict(set)
        for r in recs:
            for c in r.classes:
                per_class[c] += 1
                owners[c].add(r.spell_id)
        return {
            "effects": len(recs), "owners": len({r.spell_id for r in recs}),
            "by_class": {c: {"effects": per_class[c], "owners": len(owners[c])} for c in CLASSES if per_class[c]},
            "multi_class_effects": sum(1 for r in recs if len(r.classes) > 1),
        }

    def unimplemented_breakdown(self, spells: set[int] | None = None) -> dict[str, Any]:
        eff: Counter = Counter()
        aur: Counter = Counter()
        for r in self.records.values():
            if spells is not None and r.spell_id not in spells:
                continue
            if "unimplemented-effect" in r.classes:
                eff[effect_name(r.effect)] += 1
            if "unimplemented-aura" in r.classes:
                aur[aura_name(r.aura)] += 1
        return {"effects": dict(eff.most_common()), "auras": dict(aur.most_common())}
