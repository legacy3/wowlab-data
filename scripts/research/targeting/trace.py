"""Explain/evaluate stage records.

Every oracle stage appends one :class:`Stage`: what it read, what it produced,
the Trinity line it mirrors, and (when relevant) the RNG draws it consumed and
a ``defect`` note for a reproduced likely-Trinity defect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Stage:
    name: str
    mirrors: str
    inputs: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    notes: list[str] = field(default_factory=list)
    draws: list[int] = field(default_factory=list)
    defect: str | None = None
    evidence: str = "trinity-consumer"


@dataclass
class Trace:
    stages: list[Stage] = field(default_factory=list)

    def add(self, name: str, mirrors: str, output: Any = None, **kw: Any) -> Stage:
        stage = Stage(name=name, mirrors=mirrors, output=output, **kw)
        self.stages.append(stage)
        return stage

    def to_json(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in asdict(s).items() if v not in (None, [], {})} for s in self.stages]
