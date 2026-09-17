"""The neutral synthetic-world fixture.

A fixture states only the facts a targeting decision reads.  It is *not* a
Unit/Map graph: there is no grid, no pathing, no faction template lookup.
Every fact the oracle needs and the fixture does not state raises
:class:`targeting.FailClosed` (``World.need``) instead of being defaulted.

JSON layout (``schema: targeting-fixture/1``)::

    {
      "schema": "targeting-fixture/1",
      "name": "chain-heal-deficit-tie",
      "spell": {"id": 1064, "difficulty": 0, "effect": 0},   # effect optional
      "caster": "caster",
      "original_caster": "caster",                            # optional
      "explicit": {"unit": "t1", "dest": [x, y, z], "src": [x, y, z]},
      "actors": [
        {"id": "caster", "kind": "player", "pos": [0, 0, 0], "orientation": 0.0,
         "alive": true, "health": 100, "max_health": 100, "combat_reach": 1.5,
         "bounding_radius": 0.389,
         "owner": null, "creator": null, "summoner": null, "charmer": null,
         "group": {"id": "raid1", "subgroup": 1, "raid": true},
         "auras": [{"spell": 774, "caster": "caster"}],
         "facts": {...}}                                      # free-form, stage specific
      ],
      "relations": [                                          # directed, from -> to
        {"from": "caster", "to": "t1", "friendly": false, "hostile": true,
         "valid_attack": true, "valid_assist": false}
      ],
      "visit_order": ["t1", "t2"],   # candidate enumeration order of the map search
      "los": {"default": "clear", "blocked": [["caster", "t2"]]},
      "rng": {"draws": [3, 0]},      # consumed in order by RNG stages
      "spell_value": {"max_affected_targets": 0, "radius_mod": 1.0},
      "modifiers": {"chain_targets": 0, "chain_jump_distance": 0.0, "radius": 0.0},
      "expect": {"recipients": {"0": ["t1", "t2"]}}           # optional, for tests
    }

``visit_order`` exists because Trinity enumerates area candidates in grid-cell
visit order, which is internal to the map.  The fixture must state it whenever
an ordering-sensitive stage (RandomResize, first-match, stable sort ties) reads
it; the oracle never invents one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import FailClosed

SCHEMA = "targeting-fixture/1"
ACTOR_KINDS = ("player", "creature", "pet", "guardian", "totem", "minion", "vehicle",
               "corpse", "gameobject", "dynamicobject", "areatrigger")


@dataclass
class Actor:
    id: str
    kind: str
    pos: tuple[float, float, float] | None = None
    orientation: float | None = None
    alive: bool | None = None
    health: int | None = None
    max_health: int | None = None
    combat_reach: float | None = None
    bounding_radius: float | None = None
    owner: str | None = None
    creator: str | None = None
    summoner: str | None = None
    charmer: str | None = None
    group: dict[str, Any] | None = None
    auras: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    def need(self, attr: str) -> Any:
        value = getattr(self, attr)
        if value is None:
            raise FailClosed(f"fixture: actor {self.id!r} does not state {attr!r}")
        return value

    def fact(self, key: str) -> Any:
        if key not in self.facts:
            raise FailClosed(f"fixture: actor {self.id!r} does not state fact {key!r}")
        return self.facts[key]

    def has_aura(self, spell: int, caster: str | None = None) -> bool:
        return any(a.get("spell") == spell and (caster is None or a.get("caster") == caster)
                   for a in self.auras)


@dataclass
class World:
    name: str
    spell: dict[str, Any]
    caster: str
    actors: dict[str, Actor]
    relations: dict[tuple[str, str], dict[str, Any]]
    explicit: dict[str, Any] = field(default_factory=dict)
    original_caster: str | None = None
    visit_order: list[str] | None = None
    los: dict[str, Any] | None = None
    rng: dict[str, Any] | None = None
    spell_value: dict[str, Any] = field(default_factory=dict)
    modifiers: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    _draw_pos: int = 0

    # -- construction ------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> World:
        if data.get("schema") != SCHEMA:
            raise FailClosed(f"fixture: schema must be {SCHEMA!r}, got {data.get('schema')!r}")
        actors: dict[str, Actor] = {}
        for a in data.get("actors", []):
            if a.get("kind") not in ACTOR_KINDS:
                raise FailClosed(f"fixture: actor {a.get('id')!r} has unknown kind {a.get('kind')!r}")
            if a["id"] in actors:
                raise FailClosed(f"fixture: duplicate actor id {a['id']!r}")
            pos = a.get("pos")
            actors[a["id"]] = Actor(
                id=a["id"], kind=a["kind"],
                pos=tuple(float(v) for v in pos) if pos is not None else None,
                orientation=a.get("orientation"), alive=a.get("alive"),
                health=a.get("health"), max_health=a.get("max_health"),
                combat_reach=a.get("combat_reach"), bounding_radius=a.get("bounding_radius"),
                owner=a.get("owner"), creator=a.get("creator"), summoner=a.get("summoner"),
                charmer=a.get("charmer"), group=a.get("group"),
                auras=list(a.get("auras", [])), facts=dict(a.get("facts", {})))
        relations: dict[tuple[str, str], dict[str, Any]] = {}
        for r in data.get("relations", []):
            key = (r["from"], r["to"])
            if key in relations:
                raise FailClosed(f"fixture: duplicate relation {key}")
            for end in key:
                if end not in actors:
                    raise FailClosed(f"fixture: relation names unknown actor {end!r}")
            relations[key] = {k: v for k, v in r.items() if k not in ("from", "to")}
        caster = data.get("caster")
        if caster not in actors:
            raise FailClosed(f"fixture: caster {caster!r} is not an actor")
        world = cls(name=data.get("name", ""), spell=dict(data.get("spell", {})), caster=caster,
                    actors=actors, relations=relations, explicit=dict(data.get("explicit", {})),
                    original_caster=data.get("original_caster"), visit_order=data.get("visit_order"),
                    los=data.get("los"), rng=data.get("rng"),
                    spell_value=dict(data.get("spell_value", {})),
                    modifiers=dict(data.get("modifiers", {})), expect=dict(data.get("expect", {})),
                    raw=data)
        unit = world.explicit.get("unit")
        if unit is not None and unit not in actors:
            raise FailClosed(f"fixture: explicit unit {unit!r} is not an actor")
        if world.visit_order is not None:
            unknown = [a for a in world.visit_order if a not in actors]
            if unknown or len(set(world.visit_order)) != len(world.visit_order):
                raise FailClosed(f"fixture: visit_order must list distinct known actors, bad: {unknown}")
        return world

    @classmethod
    def load(cls, path: Path | str) -> World:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- accessors ---------------------------------------------------------
    def actor(self, actor_id: str) -> Actor:
        if actor_id not in self.actors:
            raise FailClosed(f"fixture: unknown actor {actor_id!r}")
        return self.actors[actor_id]

    def relation(self, src: str, dst: str, key: str) -> Any:
        """A directed relation fact; absent => FailClosed (never symmetric by default)."""
        rel = self.relations.get((src, dst))
        if rel is None or key not in rel:
            raise FailClosed(f"fixture: relation {src!r}->{dst!r} does not state {key!r}")
        return rel[key]

    def enumeration(self) -> list[str]:
        """Candidate enumeration order of a map search (``visit_order``)."""
        if self.visit_order is None:
            raise FailClosed("fixture: ordering-sensitive stage needs `visit_order` (grid visit order)")
        return list(self.visit_order)

    def in_los(self, a: str, b: str) -> bool:
        if self.los is None:
            raise FailClosed("fixture: stage needs line-of-sight facts (`los`)")
        blocked = {tuple(p) for p in self.los.get("blocked", [])}
        if (a, b) in blocked or (b, a) in blocked:
            return False
        default = self.los.get("default")
        if default not in ("clear", "blocked"):
            raise FailClosed("fixture: `los.default` must be 'clear' or 'blocked'")
        clear = {tuple(p) for p in self.los.get("clear", [])}
        if (a, b) in clear or (b, a) in clear:
            return True
        return default == "clear"

    def draw(self, what: str) -> int:
        """Consume the next explicit RNG draw (an integer the stage interprets)."""
        draws = (self.rng or {}).get("draws")
        if draws is None or self._draw_pos >= len(draws):
            raise FailClosed(f"fixture: RNG draw #{self._draw_pos} needed for {what} but not supplied")
        value = draws[self._draw_pos]
        self._draw_pos += 1
        return value

    @property
    def draws_consumed(self) -> int:
        return self._draw_pos
