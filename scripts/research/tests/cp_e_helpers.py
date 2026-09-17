"""Shared, lazily built objects for the agent-E character_prep tests (one snapshot load per session)."""

from __future__ import annotations

import copy
import json
import sys
from functools import lru_cache
from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
if str(RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(RESEARCH_ROOT))

from character_prep import CORPORA  # noqa: E402

FIXTURES_DIR = CORPORA / "fixtures"


@lru_cache(maxsize=1)
def compiler():
    from character_prep.compiler import Compiler, Snapshot
    return Compiler(Snapshot())


def fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json")
                  if not p.name.endswith(".compiled.json") and p.name != "index.json")


def load_raw(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def raw_copy(name: str) -> dict:
    return copy.deepcopy(load_raw(name))


@lru_cache(maxsize=None)
def compiled(name: str) -> dict:
    from character_prep.fixture import Fixture
    return compiler().compile(Fixture.from_file(FIXTURES_DIR / f"{name}.json"))


def compile_raw(raw: dict) -> dict:
    from character_prep.fixture import Fixture
    return compiler().compile(Fixture.from_dict(raw))
