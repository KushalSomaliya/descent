"""What an agent sees, and the interface every policy (bot or LLM) implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Rules


@dataclass
class Observation:
    me_id: str
    me_name: str
    day: int
    days_total: int
    days_until_shuffle: int
    floor: int
    floors: int
    hp: float
    hearts: int
    fullness: float
    item: str
    mate_id: str | None
    mate_name: str | None
    kit_charges: int
    wounds_hearts: float
    body_name: str | None
    body_food: float | None
    food_arrived_yesterday: float | None
    recent: list[tuple[int, list[str]]]
    notes: str
    rules: Rules
    # Filled in during the day:
    talk_points: int = 0
    conversation: list[dict[str, str]] = field(default_factory=list)

    @property
    def alone(self) -> bool:
        return self.mate_id is None


class Policy:
    """Base policy. Subclasses override the async hooks they care about."""

    name = "base"
    model: str | None = None

    async def choose_item(self, obs: Observation) -> dict[str, Any]:
        """Return {"item": str, "thought": str}."""
        return {"item": "book", "thought": ""}

    async def morning(self, obs: Observation) -> dict[str, Any]:
        """Return {"talk_points": int, "thought": str}."""
        return {"talk_points": 0, "thought": ""}

    async def speak(self, obs: Observation, my_turns_left: int, their_turns_left: int) -> str:
        """Return one message. obs.conversation holds what's been said so far today."""
        return "..."

    async def evening(self, obs: Observation) -> dict[str, Any]:
        """Return {"eat", "guard", "attack", "rest", "platform_limit", "notes", "thought"}."""
        budget = obs.hearts - obs.talk_points
        return {"eat": budget, "guard": 0, "attack": 0, "rest": 0, "platform_limit": None}

    def usage(self) -> dict[str, Any]:
        return {}
