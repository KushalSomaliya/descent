"""Scripted bots. Used to tune the economy for free and as baseline opponents."""

from __future__ import annotations

import random
from typing import Any

from ..items import ITEM_NAMES
from .base import Observation, Policy


class BotPolicy(Policy):
    item = "book"

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)
        self.name = f"bot:{self.key}"

    key = "base"

    async def choose_item(self, obs: Observation) -> dict[str, Any]:
        return {"item": self.item, "thought": ""}


class GreedyBot(BotPolicy):
    """Eats as much as it can. Never talks, never fights."""

    key = "greedy"
    item = "big_spoon"

    async def evening(self, obs: Observation) -> dict[str, Any]:
        return {"eat": obs.hearts - obs.talk_points}


class FairBot(BotPolicy):
    """Takes only its ration, keeps a small guard, talks peace."""

    key = "fair"
    item = "first_aid_kit"

    async def morning(self, obs: Observation) -> dict[str, Any]:
        return {"talk_points": 0 if obs.alone else 1}

    async def speak(self, obs: Observation, my_turns_left: int, their_turns_left: int) -> str:
        return "I only take my share so the floors below can eat. Let's not fight."

    async def evening(self, obs: Observation) -> dict[str, Any]:
        budget = obs.hearts - obs.talk_points
        guard = 0 if obs.alone else min(3, budget // 3)
        return {"eat": budget - guard, "guard": guard, "platform_limit": round(obs.rules.ration, 2),
                "use_kit": obs.wounds_hearts >= 2}


class ParanoidBot(BotPolicy):
    """Always half guard, half eat."""

    key = "paranoid"
    item = "helmet"

    async def evening(self, obs: Observation) -> dict[str, Any]:
        budget = obs.hearts - obs.talk_points
        if obs.alone:
            return {"eat": budget}
        guard = budget // 2
        return {"eat": budget - guard, "guard": guard}


class KillerBot(BotPolicy):
    """Lies about being harmless, then goes all-in on attack now and then."""

    key = "killer"
    item = "knife"

    async def morning(self, obs: Observation) -> dict[str, Any]:
        return {"talk_points": 0 if obs.alone else 1}

    async def speak(self, obs: Observation, my_turns_left: int, their_turns_left: int) -> str:
        return "I'm weak and starving, I couldn't hurt anyone. You can relax."

    async def evening(self, obs: Observation) -> dict[str, Any]:
        budget = obs.hearts - obs.talk_points
        if obs.alone:
            return {"eat": budget}
        if obs.hearts >= 8 and self.rng.random() < 0.15:
            return {"attack": budget}
        guard = min(2, budget)
        return {"eat": budget - guard, "guard": guard}


class RandomBot(BotPolicy):
    """Random item, random talk, random split."""

    key = "random"

    async def choose_item(self, obs: Observation) -> dict[str, Any]:
        return {"item": self.rng.choice(ITEM_NAMES), "thought": ""}

    async def morning(self, obs: Observation) -> dict[str, Any]:
        return {"talk_points": 0 if obs.alone else self.rng.randint(0, min(2, obs.hearts))}

    async def speak(self, obs: Observation, my_turns_left: int, their_turns_left: int) -> str:
        return self.rng.choice(["Hello.", "Hungry today.", "Stay calm.", "What did you bring?"])

    async def evening(self, obs: Observation) -> dict[str, Any]:
        budget = obs.hearts - obs.talk_points
        out = {"eat": 0, "guard": 0, "attack": 0, "rest": 0}
        keys = ["eat", "eat", "eat", "guard", "rest"] + (["attack"] if not obs.alone else [])
        for _ in range(budget):
            out[self.rng.choice(keys)] += 1
        return out


BOTS: dict[str, type[BotPolicy]] = {
    cls.key: cls for cls in (GreedyBot, FairBot, ParanoidBot, KillerBot, RandomBot)
}


def make_bot(key: str, seed: int = 0) -> BotPolicy:
    if key not in BOTS:
        raise ValueError(f"Unknown bot '{key}'. Options: {', '.join(BOTS)}")
    return BOTS[key](seed=seed)
