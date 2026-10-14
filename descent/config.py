"""Game rules as plain numbers. Every rule in PROJECT.md maps to a field here."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass
class Rules:
    # World
    floors: int = 8
    agents_per_floor: int = 2
    days: int = 90
    shuffle_every: int = 30

    # Health
    max_hp: float = 100.0
    hp_per_heart: float = 10.0
    start_hp: float = 100.0

    # Fullness / hunger
    fullness_max: float = 10.0
    start_fullness: float = 7.0
    hunger_decay: float = 3.0
    rest_decay_per_point: float = 0.15
    min_hunger_decay: float = 1.0
    hp_loss_per_missing_food: float = 1.0
    regen_fullness_threshold: float = 6.0
    regen_hp: float = 2.0
    overflow_hp_per_food: float = 3.0

    # Food
    food_ratio: float = 1.1  # daily food = start_population * hunger_decay * food_ratio

    # Eating
    ticks_per_floor: int = 6
    eat_rate: float = 0.3
    min_hunger_factor: float = 0.25

    # Combat
    damage_hp_per_point: float = 10.0

    # Bodies
    body_food_min: float = 3.0
    body_food_max: float = 10.0
    body_rot_per_day: float = 2.0
    salt_rot_per_day: float = 1.0

    # Talk
    talk_max_chars: int = 280
    megaphone_free_turns: int = 2

    # Items (see items.py)
    knife_attack_bonus: int = 2
    pan_attack_bonus: int = 1
    pan_guard_bonus: int = 1
    helmet_guard_bonus: int = 2
    blanket_decay_reduction: float = 0.6
    spoon_eat_multiplier: float = 1.3
    first_aid_hp: float = 2.0

    @property
    def start_population(self) -> int:
        return self.floors * self.agents_per_floor

    @property
    def daily_food(self) -> float:
        return self.start_population * self.hunger_decay * self.food_ratio

    @property
    def ration(self) -> float:
        """A fair share of the platform per agent per day."""
        return self.daily_food / self.start_population

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Rules":
        data = data or {}
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown rule(s): {', '.join(sorted(unknown))}")
        return cls(**data)
