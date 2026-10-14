"""A policy driven by an LLM."""

from __future__ import annotations

from typing import Any

from ..policies.base import Observation, Policy
from . import prompts
from .clients import LLMClient, LLMError, make_client
from .parse import extract_json

__all__ = ["LLMPolicy", "make_client"]

RETRY_SUFFIX = "\n\nYour previous reply could not be parsed. Reply with ONLY the JSON object, no other text."


class LLMPolicy(Policy):
    def __init__(self, client: LLMClient, temperature: float | None = None) -> None:
        self.client = client
        self.temperature = temperature
        self.name = "llm"
        self.model = client.spec
        self.refusals = 0
        self.parse_failures = 0
        self.calls = 0

    async def _ask(self, obs: Observation, user: str) -> dict[str, Any]:
        system = prompts.system_prompt(obs.me_name, obs.rules)
        for attempt in range(2):
            self.calls += 1
            text = await self.client.complete(system, user + (RETRY_SUFFIX if attempt else ""), self.temperature)
            obj = extract_json(text)
            if obj is not None:
                if obj.get("refusal"):
                    self.refusals += 1
                    raise LLMError("model refused")
                return obj
            self.parse_failures += 1
        raise LLMError(f"unparseable reply: {text[:200]!r}")

    async def choose_item(self, obs: Observation) -> dict[str, Any]:
        return await self._ask(obs, prompts.item_prompt(obs))

    async def morning(self, obs: Observation) -> dict[str, Any]:
        return await self._ask(obs, prompts.morning_prompt(obs))

    async def speak(self, obs: Observation, my_turns_left: int, their_turns_left: int) -> str:
        out = await self._ask(obs, prompts.speak_prompt(obs, my_turns_left, their_turns_left))
        msg = out.get("message")
        if not isinstance(msg, str):
            raise LLMError("no message field")
        return msg

    async def evening(self, obs: Observation) -> dict[str, Any]:
        return await self._ask(obs, prompts.evening_prompt(obs))

    def usage(self) -> dict[str, Any]:
        # Client usage is shared by every agent on the same model; per-agent we report our own counters.
        return {"calls": self.calls, "parse_failures": self.parse_failures, "refusals": self.refusals}
