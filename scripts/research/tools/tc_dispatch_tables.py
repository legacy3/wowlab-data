#!/usr/bin/env python3
"""Extract TrinityCore's generic dispatch tables and script hook call sites.

Standard library only.  Reads, from the sibling TrinityCore checkout:

* ``SpellEffectHandlers[]`` (``SpellEffects.cpp``): effect type -> ``Spell::EffectXxx``
* ``AuraEffectHandler[]`` (``SpellAuraEffects.cpp``): aura type -> ``AuraEffect::HandleXxx``
  (with the ``// implemented in`` note for ``HandleNoImmediateEffect`` entries)
* every ``CallScript*Handlers`` call site in ``Spell.cpp``, ``SpellAuras.cpp`` and
  ``SpellAuraEffects.cpp`` with its enclosing function (hook ordering evidence)
* the ``switch (GetAuraType())`` case lists of ``AuraEffect::PeriodicTick``,
  ``AuraEffect::HandleProc`` and ``AuraEffect::CheckEffectProc``

Output: ``docs/research/dummy-corpora/dispatch-tables.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve()
WOWLAB_DATA = HERE.parents[3]
WORKSPACE_PARENT = WOWLAB_DATA.parent
DEFAULT_OUT = WOWLAB_DATA / "docs/research/dummy-corpora/dispatch-tables.json"

HANDLER_ROW = re.compile(r"^\s*&(Spell|AuraEffect)::(\w+),?\s*//\s*(\d+)\s*([A-Z_0-9]*)\s*(.*)$")
CALL_SITE = re.compile(r"\b(CallScript\w+Handlers?|CallScript\w+)\s*\(")
FUNC_DEF = re.compile(r"^(?:[\w:<>*&\s]+?)\s((?:\w+::)+\w+)\s*\(")


def brace_body(text: str, open_index: int) -> str:
    depth = 0
    for pos in range(open_index, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index:pos + 1]
    return text[open_index:]


def handler_table(text: str, array_name: str) -> list[dict]:
    start = text.index(array_name)
    body = brace_body(text, text.index("{", start))
    rows = []
    for line in body.splitlines():
        m = HANDLER_ROW.match(line)
        if not m:
            continue
        rows.append({"value": int(m.group(3)), "name": m.group(4) or None, "handler": m.group(2),
                     "note": m.group(5).strip() or None})
    return rows


def call_sites(path: Path, rel: str) -> list[dict]:
    out = []
    current = ""
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fm = FUNC_DEF.match(line)
        if fm and not line.startswith((" ", "\t")) and "(" in line and ";" not in line:
            current = fm.group(1)
        for m in CALL_SITE.finditer(line):
            if "::CallScript" in line and line.strip().startswith(("bool ", "void ", "int32 ", "SpellCastResult ")):
                continue  # the definition itself
            out.append({"file": rel, "line": no, "in": current, "call": m.group(1),
                        "text": line.strip()[:160]})
    return out


def switch_cases(text: str, func: str) -> list[str]:
    start = text.index(func)
    body = brace_body(text, text.index("{", start))
    return re.findall(r"case\s+(SPELL_AURA_\w+)\s*:", body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tc-root", type=Path, default=WORKSPACE_PARENT / "TrinityCore")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    tc = args.tc_root.resolve()
    files = {
        "effects": "src/server/game/Spells/SpellEffects.cpp",
        "aura_effects": "src/server/game/Spells/Auras/SpellAuraEffects.cpp",
        "spell": "src/server/game/Spells/Spell.cpp",
        "auras": "src/server/game/Spells/Auras/SpellAuras.cpp",
        "script_h": "src/server/game/Spells/SpellScript.h",
    }
    texts = {k: (tc / v).read_text(encoding="utf-8") for k, v in files.items()}
    hooks_spell = re.findall(r"^\s+(SPELL_SCRIPT_HOOK_\w+)", texts["script_h"], re.M)
    hooks_aura = re.findall(r"^\s+(AURA_SCRIPT_HOOK_\w+)", texts["script_h"], re.M)
    hook_lists = re.findall(r"^\s+HookList<(\w+)>\s+(\w+);", texts["script_h"], re.M)
    payload = {
        "provenance": {
            "trinitycore_commit": subprocess.run(["git", "-C", str(tc), "rev-parse", "HEAD"],
                                                 capture_output=True, text=True, check=True).stdout.strip(),
            "files": {v: hashlib.sha256((tc / v).read_bytes()).hexdigest() for v in files.values()},
        },
        "spell_effect_handlers": handler_table(texts["effects"], "SpellEffectHandlers[TOTAL_SPELL_EFFECTS]"),
        "aura_effect_handlers": handler_table(texts["aura_effects"], "AuraEffectHandler[TOTAL_AURAS]"),
        "spell_script_hook_enum": hooks_spell,
        "aura_script_hook_enum": hooks_aura,
        "hook_lists": [{"handler_type": a, "list": b} for a, b in hook_lists],
        "call_sites": (call_sites(tc / files["spell"], files["spell"])
                       + call_sites(tc / files["auras"], files["auras"])
                       + call_sites(tc / files["aura_effects"], files["aura_effects"])),
        "periodic_tick_cases": switch_cases(texts["aura_effects"], "void AuraEffect::PeriodicTick("),
        "handle_proc_cases": switch_cases(texts["aura_effects"], "void AuraEffect::HandleProc("),
        "check_effect_proc_cases": switch_cases(texts["aura_effects"], "bool AuraEffect::CheckEffectProc("),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(payload['spell_effect_handlers'])} effect handlers, "
          f"{len(payload['aura_effect_handlers'])} aura handlers, {len(payload['call_sites'])} call sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
