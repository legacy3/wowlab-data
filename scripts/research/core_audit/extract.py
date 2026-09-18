"""Mechanical inventories read out of the pinned Core tree.

These are the audit's completeness denominators: every extracted RNG call site,
compiler module, mutable state field and catalog row must be covered by a
hand-audited registry record (see ``corpora.coverage``). Extraction fails closed
when an expected anchor is missing so a Core layout change cannot shrink a
denominator silently.
"""

from __future__ import annotations

import hashlib
import re
from functools import cache
from pathlib import Path

from . import CORE, FailClosed

RUNTIME_CRATES = ("combat", "data", "dbc", "engine", "model", "rotation", "sim")

_TEST_PATH = re.compile(r"(/tests/|/benches/|/fixtures/|_tests\.rs$|/tests\.rs$|test_fixture\.rs$|test_support\.rs$)")
_RNG_CALL = re.compile(
    r"\.(occurs|occurs_forced|ordered_outcome|uniform_index|uniform_index_forced)\s*\("
)


def rel(path: Path) -> str:
    return path.relative_to(CORE).as_posix()


@cache
def runtime_sources() -> tuple[Path, ...]:
    files: list[Path] = []
    for crate in RUNTIME_CRATES:
        root = CORE / "crates" / crate / "src"
        if not root.is_dir():
            raise FailClosed(f"missing Core crate source {root}")
        files.extend(p for p in root.rglob("*.rs") if not _TEST_PATH.search(p.as_posix()))
    return tuple(sorted(files))


def _test_line_mask(text: str) -> list[bool]:
    """True for lines inside ``#[cfg(test)]``-gated items (brace matched, strings ignored)."""
    lines = text.splitlines()
    mask = [False] * len(lines)
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("#[cfg(test)]"):
            depth = 0
            opened = False
            start = index
            while index < len(lines):
                code = re.sub(r'"(?:\\.|[^"\\])*"', '""', lines[index].split("//")[0])
                depth += code.count("{") - code.count("}")
                opened = opened or "{" in code
                mask[index] = True
                if opened and depth <= 0:
                    break
                if not opened and code.rstrip().endswith(";") and index > start:
                    break
                index += 1
        index += 1
    return mask


@cache
def _file_lines(path: Path) -> tuple[tuple[str, ...], tuple[bool, ...]]:
    text = path.read_text(encoding="utf-8")
    return tuple(text.splitlines()), tuple(_test_line_mask(text))


def rng_call_sites() -> list[dict]:
    """Every production call into the canonical RNG boundary."""
    sites: list[dict] = []
    for path in runtime_sources():
        if "crates/sim/src/random" in path.as_posix():
            continue
        lines, test = _file_lines(path)
        for number, line in enumerate(lines, 1):
            if test[number - 1] or line.lstrip().startswith("//"):
                continue
            for match in _RNG_CALL.finditer(line):
                sites.append(
                    {
                        "coord": f"{rel(path)}:{number}",
                        "method": match.group(1),
                        "text": line.strip(),
                    }
                )
    if not sites:
        raise FailClosed("no RNG call sites found; extractor is out of date")
    return sites


def compiler_modules() -> list[dict]:
    """Top-level semantic compiler modules under ``crates/combat/src/program``."""
    root = CORE / "crates/combat/src/program"
    modules = []
    for path in sorted(root.glob("*.rs")):
        if _TEST_PATH.search(path.as_posix()) or path.name.endswith("_tests.rs"):
            continue
        modules.append({"module": path.stem, "file": rel(path), "has_submodules": (root / path.stem).is_dir()})
    if len(modules) < 50:
        raise FailClosed(f"only {len(modules)} compiler modules found")
    return modules


def runtime_modules() -> list[dict]:
    root = CORE / "crates/combat/src"
    out = []
    for path in sorted(root.glob("*.rs")):
        if _TEST_PATH.search(path.as_posix()) or path.name.endswith("_tests.rs"):
            continue
        out.append({"module": path.stem, "file": rel(path), "has_submodules": (root / path.stem).is_dir()})
    return out


_FIELD = re.compile(r"^\s{4}(?:pub(?:\([a-z]+\))?\s+)?([a-z_][a-z0-9_]*)\s*:\s*(.+?),\s*$")


def struct_fields(path: str, struct: str) -> list[dict]:
    file = CORE / path
    lines, _ = _file_lines(file)
    header = re.compile(rf"^\s*(?:pub(?:\([a-z]+\))?\s+)?struct {struct}\b.*\{{\s*$")
    for index, line in enumerate(lines):
        if header.match(line):
            fields = []
            for offset in range(index + 1, len(lines)):
                if lines[offset].startswith("}"):
                    break
                match = _FIELD.match(lines[offset])
                if match:
                    fields.append(
                        {
                            "struct": struct,
                            "field": match.group(1),
                            "type": match.group(2),
                            "coord": f"{path}:{offset + 1}",
                        }
                    )
            if not fields:
                raise FailClosed(f"{path}: struct {struct} has no parsed fields")
            return fields
    raise FailClosed(f"{path}: struct {struct} not found")


MUTABLE_ROOTS = (
    ("crates/combat/src/state.rs", "CombatState"),
)


def find_struct(struct: str) -> str:
    pattern = re.compile(rf"^\s*(?:pub(?:\([a-z]+\))?\s+)?struct {struct}\b")
    for path in runtime_sources():
        lines, _ = _file_lines(path)
        if any(pattern.match(line) for line in lines):
            return rel(path)
    raise FailClosed(f"struct {struct} not found in Core runtime sources")


def mutable_fields() -> list[dict]:
    fields = struct_fields("crates/combat/src/state.rs", "CombatState")
    optional = find_struct("OptionalCombatAuthorities")
    fields += struct_fields(optional, "OptionalCombatAuthorities")
    return fields


_ENUM_MEMBER = re.compile(r"^\s{4,}([A-Z][A-Za-z0-9]*)\s*=\s*(\d+)\s*,\s*$")
_ROW = re.compile(
    r"\b(implemented|ignored|disabled|unimplemented)\(\s*([A-Za-z]+)::([A-Za-z0-9]+)\s*,\s*\"((?:\\.|[^\"\\])*)\"\s*,\s*(\"(?:\\.|[^\"\\])*\"|[A-Z_][A-Z0-9_]*|concat!\()"
)

CATALOGS = {
    "SPELL_EFFECT_REQUIREMENTS": ("crates/dbc/src/spell_effect.rs", "SpellEffectKind"),
    "SPELL_ATTRIBUTE_REQUIREMENTS": ("crates/dbc/src/spell_attribute.rs", "SpellAttributeKind"),
    "AURA_SUBTYPE_REQUIREMENTS": ("crates/dbc/src/aura_subtype.rs", "AuraSubtypeKind"),
    "EFFECT_ATTRIBUTE_REQUIREMENTS": ("crates/dbc/src/effect_attribute.rs", "EffectAttributeKind"),
}


def _enum_members(text: str, enum: str) -> dict[str, int]:
    start = text.find(f"enum {enum} {{")
    if start < 0:
        raise FailClosed(f"enum {enum} not found")
    end = text.find("\n    }\n", start)
    members = {}
    for line in text[start:end].splitlines():
        match = _ENUM_MEMBER.match(line)
        if match:
            members[match.group(1)] = int(match.group(2))
    return members


def catalog_rows() -> list[dict]:
    rows: list[dict] = []
    for catalog, (path, enum) in CATALOGS.items():
        text = (CORE / path).read_text(encoding="utf-8")
        members = _enum_members(text, enum)
        start = text.find(f"pub const {catalog}")
        body = re.sub(r"\s+", " ", text[start : text.find("\n];", start)])
        found = 0
        for support, enum_name, variant, name, requirement in _ROW.findall(body):
            if enum_name != enum or variant not in members:
                raise FailClosed(f"{catalog}: row {enum_name}::{variant} not in {enum}")
            rows.append(
                {
                    "catalog": catalog,
                    "raw": members[variant],
                    "variant": variant,
                    "name": name,
                    "support": support,
                    "requirement_sha": hashlib.sha256(requirement.encode()).hexdigest()[:12],
                }
            )
            found += 1
        if found != len(members):
            raise FailClosed(f"{catalog}: parsed {found} rows for {len(members)} enum members")
    path = "crates/dbc/src/implicit_target.rs"
    text = (CORE / path).read_text(encoding="utf-8")
    start = text.find("pub const IMPLICIT_TARGET_SEMANTICS")
    body = text[start : text.find("\n];", start)]
    for raw, match in enumerate(re.finditer(r"target\(([^)]*)\)", body)):
        rows.append(
            {
                "catalog": "IMPLICIT_TARGET_SEMANTICS",
                "raw": raw,
                "variant": "",
                "name": "",
                "support": "descriptive",
                "axes": [axis.strip() for axis in match.group(1).split(",")],
            }
        )
    return rows


def source_hashes(paths: set[str]) -> dict[str, str]:
    out = {}
    for path in sorted(paths):
        file = CORE / path
        if not file.is_file():
            raise FailClosed(f"hash requested for missing Core file {path}")
        out[path] = hashlib.sha256(file.read_bytes()).hexdigest()
    return out
