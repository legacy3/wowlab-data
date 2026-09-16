"""Corpus loaders: DB2 snapshot catalog, world overlay, script index, dispatch tables.

Nothing here interprets behaviour.  Each loader exposes typed accessors over
one evidence corpus and records provenance so the report can pin it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from procs.enums import aura_name, effect_name
from procs.source import Source
from procs.spells import DIFFICULTY_NONE, EffectInfo, SpellCatalog, SpellInfo
from procs.trinity import TrinityOverlay

from . import CORPORA, PROC_CORPORA, SourceError

SERVER_OVERLAY = CORPORA / "trinity-server-overlay.json"
SCRIPT_INDEX = CORPORA / "script-index.json"
DISPATCH_TABLES = CORPORA / "dispatch-tables.json"
BUILD_SKEW = CORPORA / "build-skew.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SourceError(f"missing corpus {path}; regenerate with the tool named in its docstring")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# world overlay
# ---------------------------------------------------------------------------

class Table:
    def __init__(self, name: str, payload: dict[str, Any]) -> None:
        self.name = name
        self.columns: list[str] = payload["columns"]
        self.key: list[str] = payload["key"]
        self.row_count_total: int = payload["row_count_total"]
        self._idx = {c: i for i, c in enumerate(self.columns)}
        self.rows: list[list[Any]] = payload["rows"]

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def col(self, row: list[Any], name: str) -> Any:
        return row[self._idx[name]]


#: SpellLinkedType -- SpellMgr.h
SPELL_LINK_CAST, SPELL_LINK_HIT, SPELL_LINK_AURA, SPELL_LINK_REMOVE = 0, 1, 2, 3
SPELL_LINK_NAMES = {0: "SPELL_LINK_CAST", 1: "SPELL_LINK_HIT", 2: "SPELL_LINK_AURA", 3: "SPELL_LINK_REMOVE"}


class ServerOverlay:
    """``trinity-server-overlay.json`` with Trinity's own loader normalisations applied."""

    def __init__(self, path: Path = SERVER_OVERLAY) -> None:
        data = _load(path)
        self.path = path
        self.provenance = data["provenance"]
        self.schemas = data["schemas"]
        self.unparsed = data["unparsed"]
        self.tables = {name: Table(name, t) for name, t in data["tables"].items()}
        self._linked: dict[tuple[int, int], list[int]] | None = None
        self._scripts_by_spell: dict[int, list[str]] | None = None

    def table(self, name: str) -> Table:
        if name not in self.tables:
            raise SourceError(f"world table {name} not in overlay")
        return self.tables[name]

    # -- spell_linked_spell: SpellMgr::LoadSpellLinked ---------------------
    def linked(self, catalog: SpellCatalog) -> dict[tuple[int, int], list[int]]:
        """Mirrors: ``SpellMgr::LoadSpellLinked`` (trigger<0 => type=REMOVE; skip missing spells / self loops)."""
        if self._linked is not None:
            return self._linked
        out: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.linked_errors: list[str] = []
        for row in self.table("spell_linked_spell").dicts():
            trigger, effect, typ = int(row["spell_trigger"]), int(row["spell_effect"]), int(row["type"])
            if not catalog.exists(abs(trigger)):
                self.linked_errors.append(f"trigger {trigger} does not exist")
                continue
            if not catalog.exists(abs(effect)):
                self.linked_errors.append(f"effect {effect} does not exist")
                continue
            if typ < SPELL_LINK_CAST or typ > SPELL_LINK_REMOVE:
                self.linked_errors.append(f"{trigger}->{effect}: invalid type {typ}")
                continue
            if trigger < 0:
                trigger = -trigger
                typ = SPELL_LINK_REMOVE
            if typ != SPELL_LINK_AURA and trigger == effect:
                self.linked_errors.append(f"{trigger}: triggers itself")
                continue
            out[(typ, trigger)].append(effect)
        self._linked = dict(out)
        return self._linked

    def linked_for(self, catalog: SpellCatalog, spell_id: int) -> dict[str, list[int]]:
        linked = self.linked(catalog)
        return {SPELL_LINK_NAMES[t]: v for (t, s), v in linked.items() if s == spell_id}

    # -- spell_script_names: ObjectMgr::LoadSpellScriptNames --------------
    def script_names(self, catalog: SpellCatalog) -> dict[int, list[str]]:
        """Mirrors: ``ObjectMgr::LoadSpellScriptNames`` (negative id = all ranks from the first rank)."""
        if self._scripts_by_spell is not None:
            return self._scripts_by_spell
        out: dict[int, list[str]] = defaultdict(list)
        self.script_binding_errors: list[str] = []
        for row in self.table("spell_script_names").dicts():
            spell_id, name = int(row["spell_id"]), str(row["ScriptName"])
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
        self._scripts_by_spell = dict(out)
        return self._scripts_by_spell

    # -- other tables --------------------------------------------------------
    def rows_by(self, table: str, column: str) -> dict[Any, list[dict[str, Any]]]:
        out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for row in self.table(table).dicts():
            out[row[column]].append(row)
        return out

    def conditions_by_source(self) -> dict[int, dict[int, list[dict[str, Any]]]]:
        """source type -> SourceEntry -> rows (spell sources only; references keyed under negative types)."""
        out: dict[int, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for row in self.table("conditions").dicts():
            out[int(row["SourceTypeOrReferenceId"])][int(row["SourceEntry"])].append(row)
        return out

    def serverside_spells(self) -> dict[tuple[int, int], dict[str, Any]]:
        out = {}
        effects = defaultdict(list)
        for row in self.table("serverside_spell_effect").dicts():
            effects[(int(row["SpellID"]), int(row["DifficultyID"]))].append(row)
        for row in self.table("serverside_spell").dicts():
            key = (int(row["Id"]), int(row["DifficultyID"]))
            row["_effects"] = sorted(effects.get(key, []), key=lambda e: int(e["EffectIndex"]))
            out[key] = row
        return out


# ---------------------------------------------------------------------------
# script index
# ---------------------------------------------------------------------------

SPELL_SCRIPT_BASES = {"SpellScript"}
AURA_SCRIPT_BASES = {"AuraScript"}


@dataclass
class ScriptClass:
    key: str
    name: str
    file: str
    line: int
    end_line: int
    bases: list[str]
    hooks: list[dict[str, Any]]
    validate: dict[str, Any] | None
    methods: dict[str, dict[str, Any]]
    fields: list[str]
    ctor_params: str | None
    ctor_name: str | None
    nested: list[dict[str, Any]]
    loader_returns: dict[str, str] = field(default_factory=dict)
    helper_class: bool = False
    resolved_bases: list[str] = field(default_factory=list)   # transitive base chain (same-file classes), set by ScriptIndex

    @property
    def kind(self) -> str:
        chain = self.resolved_bases or self.bases
        if "SpellScript" in chain:
            return "SpellScript"
        if "AuraScript" in chain:
            return "AuraScript"
        if "AreaTriggerAI" in chain:
            return "AreaTriggerAI"
        if "SpellScriptLoader" in chain:
            return "SpellScriptLoader"
        return self.bases[0] if self.bases else "?"


class ScriptIndex:
    def __init__(self, path: Path = SCRIPT_INDEX) -> None:
        data = _load(path)
        self.path = path
        self.provenance = data["provenance"]
        self.file_hashes: dict[str, str] = data["file_hashes"]
        self.constants: dict[str, dict[str, int]] = data["constants"]
        self.helpers: dict[str, dict[str, Any]] = data.get("helpers", {})
        self.engine: dict[str, Any] = data["engine"]
        self.registrations: list[dict[str, Any]] = data["registrations"]
        self.classes: dict[str, ScriptClass] = {}
        self.by_name: dict[str, list[ScriptClass]] = defaultdict(list)
        for key, c in data["classes"].items():
            self._add_class(key, c)
        # transitive base chains + inheritance of hooks/methods/fields from same-file base classes
        for sc in list(self.classes.values()):
            self._inherit(sc)
        # registration name -> classes
        self.registered: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for reg in self.registrations:
            if reg.get("name"):
                self.registered[reg["name"]].append(reg)
        # old-style: classes with a constructor name literal (CreatureScript("name"), SpellScriptLoader("name"))
        self.ctor_named: dict[str, list[ScriptClass]] = defaultdict(list)
        for c in self.classes.values():
            if c.ctor_name:
                self.ctor_named[c.ctor_name].append(c)

    def _add_class(self, key: str, c: dict[str, Any]) -> None:
        sc = ScriptClass(key=key, name=c["name"], file=c["file"], line=c["line"], end_line=c["end_line"],
                         bases=c["bases"], hooks=list(c["hooks"]), validate=c["validate"], methods=dict(c["methods"]),
                         fields=list(c["fields"]), ctor_params=c["ctor_params"], ctor_name=c["ctor_name"],
                         nested=c.get("nested", []), loader_returns=c.get("loader_returns", {}),
                         helper_class=bool(c.get("helper_class")))
        self.classes[key] = sc
        self.by_name[sc.name].append(sc)
        for n in c.get("nested", []):
            self._add_class(f"{n['name']}@{n['file']}:{n['line']}", n)

    def _inherit(self, sc: ScriptClass, depth: int = 0) -> None:
        """Mirrors C++ inheritance for the structural facts: a derived script gets its
        same-file base class's ``Register`` hooks, methods (unless overridden) and fields.
        ``resolved_bases`` is the transitive chain so ``kind`` sees SpellScript/AuraScript."""
        if sc.resolved_bases or depth > 6:
            return
        chain = list(sc.bases)
        for b in sc.bases:
            base = self.resolve_class(b, sc.file)
            if base is None or base is sc:
                continue
            self._inherit(base, depth + 1)
            chain.extend(x for x in base.resolved_bases if x not in chain)
            for hook in base.hooks:
                if hook not in sc.hooks:
                    sc.hooks.append(dict(hook, inherited_from=base.name))
            for mname, facts in base.methods.items():
                sc.methods.setdefault(mname, facts)
            sc.fields.extend(f for f in base.fields if f not in sc.fields)
            if sc.validate is None and base.validate is not None:
                sc.validate = base.validate
        sc.resolved_bases = chain

    def resolve_class(self, name: str, prefer_file: str | None = None) -> ScriptClass | None:
        base = name.split("<")[0].strip()
        cands = self.by_name.get(base, [])
        if prefer_file:
            same = [c for c in cands if c.file == prefer_file]
            if same:
                cands = same
        return cands[0] if cands else None

    def resolve_script_name(self, name: str) -> dict[str, Any]:
        """A ``spell_script_names.ScriptName`` -> the SpellScript/AuraScript classes it instantiates.

        Mirrors the registration macros in ``ScriptMgr.h`` (``RegisterSpellScript``
        family) and the legacy ``SpellScriptLoader::GetSpellScript/GetAuraScript``
        pattern.  Returns ``resolved=False`` when no registration exists.
        """
        regs = self.registered.get(name, [])
        classes: list[ScriptClass] = []
        ctor_args: list[str] = []
        files: set[str] = set()
        loader_style: set[str] = set()
        for reg in regs:
            for cname in reg.get("classes", []):
                if cname == "void":
                    continue
                sc = self.resolve_class(cname, reg["file"])
                if sc is None:
                    continue
                if sc.kind == "SpellScriptLoader":
                    loader_style.add("SpellScriptLoader")
                    for inner in sc.loader_returns.values():
                        isc = self.resolve_class(inner, sc.file)
                        if isc is not None:
                            classes.append(isc)
                            files.add(isc.file)
                else:
                    classes.append(sc)
                    files.add(sc.file)
            if reg.get("ctor_args"):
                ctor_args = reg["ctor_args"]
            loader_style.add(reg["macro"])
        if not regs:
            for sc in self.ctor_named.get(name, []):
                if sc.kind == "SpellScriptLoader":
                    loader_style.add("SpellScriptLoader")
                    for inner in sc.loader_returns.values():
                        isc = self.resolve_class(inner, sc.file)
                        if isc is not None:
                            classes.append(isc)
                            files.add(isc.file)
        spell_like = [c for c in classes if c.kind in ("SpellScript", "AuraScript")]
        return {"name": name, "resolved": bool(spell_like), "classes": spell_like,
                "ctor_args": ctor_args, "files": sorted(files), "registration": sorted(loader_style),
                "registration_count": len(regs)}

    def merged_facts(self, sc: ScriptClass, handler: str, depth: int = 3) -> dict[str, Any]:
        """Handler facts plus the facts of same-class methods and same-file free
        helpers it calls (transitively, ``depth`` levels).  Structural: a call
        to ``HandleProc`` inside ``OnProcArms`` pulls ``HandleProc``'s calls in.
        Merged facts carry ``via`` = the helper chain, so the report can tell
        direct from delegated actions."""
        own = sc.methods.get(handler)
        file_helpers = {k.split("@")[0].split("::")[-1]: v for k, v in self.helpers.items() if v.get("file") == sc.file}
        if own is None:
            # handler bound to a namespace-level function (e.g. DivineImageHelpers::Trigger)
            own = file_helpers.get(handler)
        if own is None and handler in ("PreventHitDefaultEffect", "PreventDefaultAction", "PreventHitEffect"):
            # handler bound directly to the base-class prevention method
            own = {"calls": [{"callee": handler, "cat": "prevent", "recv": "", "args": [], "line": None}],
                   "other_calls": {}, "refs": {}, "tokens": [], "synthesized": True}
        if own is None:
            return {}
        merged = {"calls": list(own.get("calls", [])), "other_calls": dict(own.get("other_calls", {})),
                  "refs": dict(own.get("refs", {})), "tokens": list(own.get("tokens", [])),
                  "case_ints": list(own.get("case_ints", [])), "cmp_ints": list(own.get("cmp_ints", [])),
                  "literals": list(own.get("literals", [])), "lambdas": own.get("lambdas", 0),
                  "loops": own.get("loops", 0), "line": own.get("line"), "params": own.get("params"),
                  "static": own.get("static"), "via": []}
        seen = {handler}
        frontier = [(handler, 0)]
        merged["cross_class"] = []
        merged["cross_script"] = []
        # nested helper classes (e.g. BasicEvent subclasses): their Execute/operator() run later
        nested_methods: dict[str, dict[str, Any]] = {}
        for n in sc.nested:
            for mname in ("Execute", "operator()", "Run", "Call"):
                if mname in n.get("methods", {}):
                    nested_methods[n["name"]] = n["methods"][mname]
        merged["nested_events"] = []
        merged["helper_arg_ints"] = []
        while frontier:
            name, d = frontier.pop()
            if d >= depth:
                continue
            facts = sc.methods.get(name) if name != handler else own
            if facts is None:
                facts = file_helpers.get(name)
            if facts is None:
                continue
            for nt in facts.get("news", []):
                base = nt.split("<")[0].split("::")[-1]
                target = nested_methods.get(base)
                if target is None or base in seen:
                    continue
                seen.add(base)
                merged["nested_events"].append(base)
                merged["via"].append(f"new {base}")
                merged["calls"].extend(target.get("calls", []))
                merged["refs"].update(target.get("refs", {}))
                merged["tokens"] = sorted(set(merged["tokens"]) | set(target.get("tokens", [])))
                merged["lambdas"] += target.get("lambdas", 0)
                merged["loops"] += target.get("loops", 0)
                merged["delayed_by_event_class"] = True
            for c in facts.get("calls", []):
                # ints passed to same-class / same-file helpers parameterise the helper (e.g. HandleBuff(SPELL_A, SPELL_B))
                if c.get("cat") == "other" and (c["callee"] in sc.methods or c["callee"] in file_helpers):
                    merged["helper_arg_ints"].extend(v for v in c.get("ints", []) if v >= 100)
            callees = list(facts.get("other_calls", {}))
            # helper calls that carry integer arguments are recorded under "calls" (cat other), not other_calls
            callees.extend(c["callee"] for c in facts.get("calls", []) if c.get("cat") == "other")
            # static calls on another class in the same file (Other::Method(...)): recv names the class
            other_class_targets: dict[str, dict[str, Any]] = {}
            for c in facts.get("calls", []):
                recv = c.get("recv", "")
                if recv and recv != sc.name and "(" not in recv and "->" not in recv and "." not in recv:
                    oc = self.resolve_class(recv, sc.file)
                    if oc is not None and c["callee"] in oc.methods:
                        other_class_targets[c["callee"]] = oc.methods[c["callee"]]
                        if oc.name not in merged["cross_class"]:
                            merged["cross_class"].append(oc.name)
            # GetScript<X>() cross-script state: methods of X invoked through the returned pointer
            import re as _re
            for name_ in list(facts.get("other_calls", {})) + [c["callee"] for c in facts.get("calls", [])]:
                m = _re.match(r"GetScript<([\w:]+)>", name_)
                if m:
                    xc = self.resolve_class(m.group(1), sc.file)
                    if xc is not None and xc.name not in merged["cross_script"]:
                        merged["cross_script"].append(xc.name)
                        for cal in callees:
                            if cal in xc.methods and cal not in sc.methods:
                                other_class_targets[cal] = xc.methods[cal]
            for callee in callees:
                if callee in seen:
                    continue
                target = sc.methods.get(callee) or file_helpers.get(callee) or other_class_targets.get(callee)
                if target is None:
                    continue
                seen.add(callee)
                merged["via"].append(callee)
                merged["calls"].extend(target.get("calls", []))
                for k, v in target.get("other_calls", {}).items():
                    merged["other_calls"][k] = merged["other_calls"].get(k, 0) + v
                merged["refs"].update(target.get("refs", {}))
                merged["tokens"] = sorted(set(merged["tokens"]) | set(target.get("tokens", [])))
                merged["case_ints"].extend(target.get("case_ints", []))
                merged["cmp_ints"].extend(target.get("cmp_ints", []))
                merged["literals"] = sorted(set(merged["literals"]) | set(target.get("literals", [])))
                merged["lambdas"] += target.get("lambdas", 0)
                merged["loops"] += target.get("loops", 0)
                frontier.append((callee, d + 1))
        return merged

    def engine_functions(self) -> dict[str, dict[str, Any]]:
        """``file::function`` -> facts for every engine file indexed."""
        out = {}
        for rel, data in self.engine.items():
            if data.get("missing"):
                continue
            for fname, facts in data["functions"].items():
                out[f"{rel}::{fname}"] = facts
        return out


# ---------------------------------------------------------------------------
# dispatch tables
# ---------------------------------------------------------------------------

class Dispatch:
    def __init__(self, path: Path = DISPATCH_TABLES) -> None:
        data = _load(path)
        self.path = path
        self.provenance = data["provenance"]
        self.effect_handlers: dict[int, dict[str, Any]] = {r["value"]: r for r in data["spell_effect_handlers"]}
        self.aura_handlers: dict[int, dict[str, Any]] = {r["value"]: r for r in data["aura_effect_handlers"]}
        self.spell_hook_enum: list[str] = data["spell_script_hook_enum"]
        self.aura_hook_enum: list[str] = data["aura_script_hook_enum"]
        self.hook_lists: list[dict[str, str]] = data["hook_lists"]
        self.call_sites: list[dict[str, Any]] = data["call_sites"]
        self.periodic_tick_cases: list[str] = data["periodic_tick_cases"]
        self.handle_proc_cases: list[str] = data["handle_proc_cases"]
        self.check_effect_proc_cases: list[str] = data["check_effect_proc_cases"]

    def effect_handler(self, effect_type: int) -> str:
        row = self.effect_handlers.get(effect_type)
        return row["handler"] if row else "?"

    def aura_handler(self, aura_type: int) -> dict[str, Any]:
        return self.aura_handlers.get(aura_type, {"handler": "?", "note": None})


class BuildSkew:
    def __init__(self, path: Path = BUILD_SKEW) -> None:
        data = _load(path)
        self.provenance = data["provenance"]
        self.added: set[int] = set(data["added"])
        self.removed: set[int] = set(data["removed"])

    def is_newer_than_trinity(self, spell_id: int) -> bool:
        return spell_id in self.added


# ---------------------------------------------------------------------------
# bundle
# ---------------------------------------------------------------------------

class Bundle:
    """Everything the package needs, loaded once."""

    def __init__(self, tables_root: Path | None = None) -> None:
        self.source = Source(tables_root) if tables_root else Source()
        self.proc_overlay = TrinityOverlay(PROC_CORPORA / "trinity-world-overlay.json")
        self.catalog = SpellCatalog(self.source, self.proc_overlay.custom_attributes)
        self.proc_overlay.bind_ranks(self.catalog)
        self.world = ServerOverlay()
        self.index = ScriptIndex()
        self.dispatch = Dispatch()
        self.skew = BuildSkew()
        self.script_names = self.world.script_names(self.catalog)

    def info(self, spell_id: int, difficulty: int = DIFFICULTY_NONE) -> SpellInfo | None:
        return self.catalog.get(spell_id, difficulty)

    def name(self, spell_id: int) -> str:
        return self.catalog.names.get(spell_id, "")

    @staticmethod
    def effect_label(eff: EffectInfo) -> str:
        label = effect_name(eff.effect)
        if eff.is_aura:
            label += f"/{aura_name(eff.aura)}"
        return label
