"""Mutable game state."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Agent:
    id: str
    name: str
    policy_name: str
    model: str | None = None
    item: str = "book"
    hp: float = 100.0
    fullness: float = 7.0
    floor: int = 1
    alive: bool = True
    death_day: int | None = None
    death_cause: str | None = None
    killed_by: str | None = None
    notes: str = ""
    # Personal history shown back to the agent: list of (day, [lines])
    log: list[tuple[int, list[str]]] = field(default_factory=list)
    # Stats
    kills: int = 0
    food_from_platform: float = 0.0
    food_from_bodies: float = 0.0
    platform_days: int = 0
    damage_dealt: float = 0.0
    damage_taken: float = 0.0
    attacks_made: int = 0
    invalid_outputs: int = 0
    last_platform_arrival: float | None = None
    wounds: float = 0.0          # HP lost to attacks and not yet healed (the first-aid kit only heals this)
    kit_charges: int = 0
    kit_used: int = 0

    def hearts(self, hp_per_heart: float = 10.0) -> int:
        """Hearts round down, so every lost HP costs strength. You keep your last heart until HP hits 0."""
        if self.hp <= 0:
            return 0
        return max(1, int(math.floor(self.hp / hp_per_heart + 1e-9)))

    def note_event(self, day: int, line: str) -> None:
        if self.log and self.log[-1][0] == day:
            self.log[-1][1].append(line)
        else:
            self.log.append((day, [line]))


@dataclass
class Body:
    victim_id: str
    victim_name: str
    floor: int
    food: float
    died_day: int


@dataclass
class Allocation:
    talk: int = 0
    eat: int = 0
    guard: int = 0
    attack: int = 0
    rest: int = 0
    platform_limit: float | None = None
    use_kit: bool = False

    def to_dict(self) -> dict:
        return {
            "use_kit": self.use_kit,
            "talk": self.talk,
            "eat": self.eat,
            "guard": self.guard,
            "attack": self.attack,
            "rest": self.rest,
            "platform_limit": self.platform_limit,
        }
