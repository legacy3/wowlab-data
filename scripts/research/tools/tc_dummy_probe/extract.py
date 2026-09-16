#!/usr/bin/env python3
"""Extract the bounded TrinityCore helpers the Dummy-semantics oracles mirror.

Pulls, verbatim, from the sibling checkout:

* ``CalculatePct``, ``AddPct``, ``ApplyPct``, ``RoundToInterval`` templates (``src/common/Utilities/Util.h``)
* ``CompareValues`` template (``src/common/Utilities/Util.h``)
* ``SpellScriptBase::EffectHook::GetAffectedEffectsMask`` / ``IsEffectAffected`` and the two
  ``EffectBase::CheckEffect`` overloads (``src/server/game/Spells/SpellScript.cpp``), rewritten only in
  their receiver names so they compile against the tiny ``FakeSpellInfo`` in ``probe.cpp``.

Writes ``tc_dummy_bodies.inc`` next to this file with a header naming the commit and hashes.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TC = HERE.parents[4] / "TrinityCore"
UTIL = TC / "src/common/Utilities/Util.h"
SCRIPT = TC / "src/server/game/Spells/SpellScript.cpp"


def grab_template(text: str, name: str) -> str:
    m = re.search(rf"template\s*<[^>]*>\s*\n\s*inline\s+[\w:<> ]+\s+{name}\s*\([^)]*\)\s*\n\{{.*?\n\}}", text, re.S)
    if not m:
        raise SystemExit(f"{name} not found in Util.h")
    return m.group(0)


def grab_function(text: str, signature_start: str) -> str:
    i = text.index(signature_start)
    j = text.index("\n{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    raise SystemExit("unbalanced braces")


def main() -> int:
    util = UTIL.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    commit = subprocess.run(["git", "-C", str(TC), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    parts = [f"// extracted from TrinityCore {commit}",
             f"// Util.h sha256 {hashlib.sha256(util.encode()).hexdigest()}",
             f"// SpellScript.cpp sha256 {hashlib.sha256(script.encode()).hexdigest()}", ""]
    for name in ("CalculatePct", "AddPct", "ApplyPct", "RoundToInterval"):
        parts.append(grab_template(util, name))
        parts.append("")
    cv = re.search(r"template\s*<class T>\s*\n\s*bool CompareValues\(ComparisionType type, T val1, T val2\)\s*\n\{.*?\n\}", util, re.S)
    if not cv:
        raise SystemExit("CompareValues not found")
    parts.append(cv.group(0))
    parts.append("")
    mask = grab_function(script, "uint32 SpellScriptBase::EffectHook::GetAffectedEffectsMask(SpellInfo const* spellInfo) const")
    aff = grab_function(script, "bool SpellScriptBase::EffectHook::IsEffectAffected(SpellInfo const* spellInfo, uint8 effIndex) const")
    chk_spell = grab_function(script, "bool SpellScript::EffectBase::CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const")
    chk_aura = grab_function(script, "bool AuraScript::EffectBase::CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const")
    # receiver rewrite: the probe defines these as free functions over FakeSpellInfo with the same bodies
    mask = mask.replace("uint32 SpellScriptBase::EffectHook::GetAffectedEffectsMask(SpellInfo const* spellInfo) const",
                        "uint32 EffectHook::GetAffectedEffectsMask(SpellInfo const* spellInfo) const")
    aff = aff.replace("bool SpellScriptBase::EffectHook::IsEffectAffected(SpellInfo const* spellInfo, uint8 effIndex) const",
                      "bool EffectHook::IsEffectAffected(SpellInfo const* spellInfo, uint8 effIndex) const")
    chk_spell = chk_spell.replace("bool SpellScript::EffectBase::CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const",
                                  "bool SpellEffectBase::CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const")
    chk_aura = chk_aura.replace("bool AuraScript::EffectBase::CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const",
                                "bool AuraEffectBase::CheckEffect(SpellInfo const* spellInfo, uint8 effIndex) const")
    parts += [mask, "", aff, "", chk_spell, "", chk_aura, ""]
    (HERE / "tc_dummy_bodies.inc").write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {HERE / 'tc_dummy_bodies.inc'} from {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
