"""Item sets and set-bonus thresholds.

Mirrors: ``AddItemsSetItem`` / ``RemoveItemsSetItem`` / ``UpdateItemSetAuras``
(``src/server/game/Entities/Item/Item.cpp``) and
``DB2Manager::GetItemSetSpells``.

The proved chain is::

    equipped item instances
      -> ItemSparse.ItemSet  (canonical, template level -- not a bonus list)
      -> equipped piece count per set
      -> ItemSetSpell.Threshold reached
      -> ItemSetSpell.SpellID granted via Player::ApplyEquipSpell(nullptr, ...)

Two gates are evaluated *after* the threshold is banked, not before:
``ChrSpecID`` and ``TraitSubTreeID``.  In Trinity the bonus is inserted into
``ItemSetEffect::SetBonuses`` regardless, and only the cast is skipped, so a
spec change re-evaluates through ``UpdateItemSetAuras`` without re-counting.
That distinction is preserved here.

Counting note: ``ItemSetEffect::EquippedItems`` is a set of *Item pointers*, so
two distinct equipped instances of the same ItemID each count.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import SourceError
from .enums import ITEM_SET_FLAG_LEGACY_INACTIVE
from .tables import Tables

#: ItemSet.db2 carries membership in ItemID_0..ItemID_16.
ITEM_SET_MEMBER_SLOTS = 17


@dataclass(frozen=True)
class SetSpell:
    item_set_spell_id: int
    item_set_id: int
    threshold: int
    spell_id: int
    chr_spec_id: int
    trait_sub_tree_id: int

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ItemSet:
    item_set_id: int
    name: str
    set_flags: int
    required_skill: int
    required_skill_rank: int
    member_item_ids: tuple[int, ...]
    spells: tuple[SetSpell, ...]

    @property
    def legacy_inactive(self) -> bool:
        """``ITEM_SET_FLAG_LEGACY_INACTIVE`` -- ``AddItemsSetItem`` returns early."""
        return bool(self.set_flags & ITEM_SET_FLAG_LEGACY_INACTIVE)

    @property
    def thresholds(self) -> tuple[int, ...]:
        return tuple(sorted({s.threshold for s in self.spells}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_set_id": self.item_set_id,
            "name": self.name,
            "set_flags": self.set_flags,
            "legacy_inactive": self.legacy_inactive,
            "required_skill": self.required_skill,
            "required_skill_rank": self.required_skill_rank,
            "member_item_ids": list(self.member_item_ids),
            "thresholds": list(self.thresholds),
            "spells": [s.to_dict() for s in self.spells],
        }


@dataclass
class SatisfiedSetBonus:
    item_set_id: int
    item_set_name: str
    equipped_count: int
    threshold: int
    spell_id: int
    item_set_spell_id: int
    chr_spec_id: int
    trait_sub_tree_id: int
    contributing_item_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["contributing_item_ids"] = list(self.contributing_item_ids)
        return out


class SetEngine:
    def __init__(self, tables: Tables) -> None:
        self._item_set = tables("ItemSet").by("ID")
        self._set_spells = tables("ItemSetSpell").group("ItemSetID")

    def get(self, item_set_id: int) -> ItemSet:
        row = self._item_set.get(item_set_id)
        if row is None:
            raise SourceError(
                f"ItemSet {item_set_id} is referenced by ItemSparse.ItemSet but "
                f"has no ItemSet row; AddItemsSetItem logs an error and skips")
        members = tuple(
            int(row[f"ItemID_{i}"]) for i in range(ITEM_SET_MEMBER_SLOTS)
            if int(row[f"ItemID_{i}"]))
        spells = tuple(
            SetSpell(
                item_set_spell_id=int(s["ID"]),
                item_set_id=item_set_id,
                threshold=int(s["Threshold"]),
                spell_id=int(s["SpellID"]),
                chr_spec_id=int(s["ChrSpecID"]),
                trait_sub_tree_id=int(s["TraitSubTreeID"]),
            )
            for s in self._set_spells.get(item_set_id, ()))
        return ItemSet(
            item_set_id=item_set_id,
            name=str(row["Name_lang"]),
            set_flags=int(row["SetFlags"]),
            required_skill=int(row["RequiredSkill"]),
            required_skill_rank=int(row["RequiredSkillRank"]),
            member_item_ids=members,
            spells=spells,
        )

    def has(self, item_set_id: int) -> bool:
        return item_set_id in self._item_set

    def satisfied_bonuses(
        self,
        equipped: Sequence[tuple[int, int]],
        *,
        chr_spec_id: int | None = None,
        trait_sub_tree_id: int | None = None,
    ) -> list[SatisfiedSetBonus]:
        """Count set pieces and report which ``ItemSetSpell`` thresholds are met.

        ``equipped`` is a sequence of ``(item_id, item_set_id)`` -- one entry per
        equipped *instance*, matching Trinity's per-``Item*`` counting.

        When ``chr_spec_id`` / ``trait_sub_tree_id`` are ``None`` every
        threshold-satisfying spell is reported with its gate values; when
        supplied, gated spells are filtered exactly as
        ``AddItemsSetItem``/``UpdateItemSetAuras`` filter the cast.

        This reports *acquisition* only.  Nothing here claims the spell is
        executable.
        """
        counts: Counter[int] = Counter()
        contributors: dict[int, list[int]] = {}
        for item_id, item_set_id in equipped:
            if not item_set_id:
                continue
            counts[item_set_id] += 1
            contributors.setdefault(item_set_id, []).append(item_id)

        out: list[SatisfiedSetBonus] = []
        for item_set_id, count in sorted(counts.items()):
            item_set = self.get(item_set_id)
            if item_set.legacy_inactive:
                continue
            for spell in item_set.spells:
                if spell.threshold > count:
                    continue
                if chr_spec_id is not None and spell.chr_spec_id \
                        and spell.chr_spec_id != chr_spec_id:
                    continue
                if trait_sub_tree_id is not None and spell.trait_sub_tree_id \
                        and spell.trait_sub_tree_id != trait_sub_tree_id:
                    continue
                out.append(SatisfiedSetBonus(
                    item_set_id=item_set_id,
                    item_set_name=item_set.name,
                    equipped_count=count,
                    threshold=spell.threshold,
                    spell_id=spell.spell_id,
                    item_set_spell_id=spell.item_set_spell_id,
                    chr_spec_id=spell.chr_spec_id,
                    trait_sub_tree_id=spell.trait_sub_tree_id,
                    contributing_item_ids=tuple(sorted(contributors[item_set_id])),
                ))
        return out
