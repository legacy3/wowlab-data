"""Read access to the TrinityCore world-database proc overlay.

The overlay (``docs/research/procs-corpora/trinity-world-overlay.json``) is
produced by ``tools/tdb_proc_overlay.py`` from the TDB dump the checkout pins
plus every world update in the checkout.  It is *Trinity's* data, not
Blizzard's: rows in it are server-author decisions, and this package reports
them as such (``origin: trinity-world-db``), never as source facts.

Mirrors:
* ``SpellMgr::LoadSpellProcs`` (row decoding; merge happens in
  :mod:`procs.definition`);
* ``SpellMgr::LoadSpellEnchantProcData``;
* ``ObjectMgr::LoadSpellScriptNames`` (negative id = all ranks);
* ``ConditionMgr`` source type 24 (presence only -- conditions are evaluated
  against live actor state and are not ported);
* ``ObjectMgr::LoadItemTemplateAddon`` (``SpellPPMChance`` only).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import SourceError
from .source import OVERLAY_PATH


@dataclass(frozen=True)
class SpellProcRow:
    """One ``spell_proc`` row, decoded exactly as ``LoadSpellProcs`` reads it."""

    spell_id: int
    all_ranks: bool
    school_mask: int
    family_name: int
    family_mask: int
    proc_flags: int
    spell_type_mask: int
    spell_phase_mask: int
    hit_mask: int
    attributes_mask: int
    disable_effects_mask: int
    procs_per_minute: float
    chance: float
    cooldown: int
    charges: int

    @classmethod
    def from_row(cls, columns: list[str], row: list[Any]) -> SpellProcRow:
        r = dict(zip(columns, row))
        spell_id = int(r["SpellId"])
        return cls(
            spell_id=abs(spell_id), all_ranks=spell_id < 0,
            # fields[1].GetUInt8() etc.: the column types already bound the values.
            school_mask=int(r["SchoolMask"]) & 0xFF,
            family_name=int(r["SpellFamilyName"]) & 0xFFFF,
            family_mask=sum((int(r[f"SpellFamilyMask{i}"]) & 0xFFFFFFFF) << (32 * i) for i in range(4)),
            proc_flags=(int(r["ProcFlags"]) & 0xFFFFFFFF) | ((int(r["ProcFlags2"]) & 0xFFFFFFFF) << 32),
            spell_type_mask=int(r["SpellTypeMask"]),
            spell_phase_mask=int(r["SpellPhaseMask"]),
            hit_mask=int(r["HitMask"]),
            attributes_mask=int(r["AttributesMask"]),
            disable_effects_mask=int(r["DisableEffectsMask"]),
            procs_per_minute=float(r["ProcsPerMinute"]),
            chance=float(r["Chance"]),
            cooldown=int(r["Cooldown"]),
            charges=int(r["Charges"]) & 0xFF,
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class TrinityOverlay:
    def __init__(self, path: Path | str = OVERLAY_PATH) -> None:
        path = Path(path)
        if not path.exists():
            raise SourceError(
                f"Trinity world overlay not found at {path}; regenerate with "
                "tools/tdb_proc_overlay.py --tdb <pinned TDB dump>")
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8"))
        self.provenance: dict[str, Any] = data["provenance"]
        self.unparsed: list[dict[str, Any]] = data["unparsed"]
        self.spell_proc: dict[int, SpellProcRow] = {}
        for row in data["spell_proc"]:
            decoded = SpellProcRow.from_row(data["spell_proc_columns"], row)
            self.spell_proc[decoded.spell_id] = decoded
        cols = data["spell_enchant_proc_data_columns"]
        self.enchant_proc: dict[int, dict[str, Any]] = {
            int(r[0]): dict(zip(cols, r)) for r in data["spell_enchant_proc_data"]}
        self.custom_attributes: dict[int, int] = {int(a): int(b) for a, b in data["spell_custom_attr"]}
        self.item_spell_ppm: dict[int, float] = {int(a): float(b) for a, b in data["item_template_addon_spell_ppm"]}
        self._script_rows: list[tuple[int, str]] = [(int(a), b) for a, b in data["spell_script_names"]]
        self.scripts: dict[str, dict[str, Any]] = data["scripts"]
        self.conditions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        ccols = data["spell_proc_conditions_columns"]
        for r in data["spell_proc_conditions"]:
            row = dict(zip(ccols, r))
            self.conditions[int(row["SourceEntry"])].append(row)
        self.corrections: dict[int, list[str]] = {int(k): v for k, v in data["spell_info_corrections"].items()}
        self._script_names: dict[int, list[str]] | None = None

    def bind_ranks(self, catalog) -> None:
        """Resolve ``spell_script_names`` against a catalog (negative id = all ranks)."""
        out: dict[int, list[str]] = defaultdict(list)
        self.script_binding_errors: list[str] = []
        for spell_id, name in self._script_rows:
            all_ranks = spell_id < 0
            spell_id = abs(spell_id)
            if not catalog.exists(spell_id):
                self.script_binding_errors.append(f"{name}: spell {spell_id} does not exist")
                continue
            if all_ranks:
                if catalog.first_rank(spell_id) != spell_id:
                    self.script_binding_errors.append(f"{name}: {spell_id} is not a first rank")
                    continue
                current: int | None = spell_id
                while current is not None:
                    out[current].append(name)
                    current = catalog.next_rank(current)
            else:
                out[spell_id].append(name)
        self._script_names = dict(out)

    def script_names(self, spell_id: int) -> list[str]:
        if self._script_names is None:
            raise SourceError("TrinityOverlay.bind_ranks(catalog) must run first")
        return self._script_names.get(spell_id, [])

    def script_hooks(self, spell_id: int) -> dict[str, Any]:
        """Union of the proc hooks installed by every script bound to ``spell_id``."""
        names = self.script_names(spell_id)
        hooks: set[str] = set()
        effect_auras: set[str] = set()
        prevents = False
        unresolved = []
        files: set[str] = set()
        for name in names:
            info = self.scripts.get(name)
            if info is None:
                unresolved.append(name)
                continue
            hooks.update(info["hooks"])
            effect_auras.update(info["effect_proc_auras"])
            prevents |= info["prevents_default"]
            files.update(info["files"])
            if not info["resolved"]:
                unresolved.append(name)
        return {
            "script_names": names,
            "proc_hooks": sorted(hooks),
            "effect_proc_bindings": sorted(effect_auras),
            "any_prevent_default_action": prevents,
            "unresolved_script_names": unresolved,
            "files": sorted(files),
        }

    def correction_members(self, spell_id: int) -> list[str]:
        return self.corrections.get(spell_id, [])
