"""Tiny LLM clients over plain HTTP (stdlib only — no SDKs needed).

Model specs look like "provider:model", e.g.
    anthropic:claude-haiku-5-5
    openai:gpt-6-luna
    openrouter:qwen/qwen3.8-27b
    ollama:qwen3.6
    mock:any            (offline fake model for tests)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "anthropic": {"type": "anthropic", "base_url": "https://api.anthropic.com", "api_key_env": "ANTHROPIC_API_KEY"},
    "openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "openrouter": {"type": "openai", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "gemini": {"type": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "api_key_env": "GEMINI_API_KEY", "max_tokens": 8192},
    "ollama": {"type": "openai", "base_url": "http://localhost:11434/v1", "api_key_env": None},
    "mock": {"type": "mock"},
}

# USD per million tokens (input, output). Override per provider with "price": [in, out].
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    # Gemini paid tier (output includes thinking). Flash price rises on 2027-01-01.
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-4-argon": (2.0, 10.0),
}

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    errors: int = 0
    seconds: float = 0.0

    def add(self, other: "Usage") -> None:
        for k in ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "errors"):
            setattr(self, k, getattr(self, k) + getattr(other, k))
        self.seconds += other.seconds


@dataclass
class LLMClient:
    spec: str
    model: str
    semaphore: asyncio.Semaphore
    base_url: str = ""
    api_key: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)
    price: tuple[float, float] | None = None
    max_tokens: int = 4000
    timeout: float = 180.0
    effort: str | None = None
    retries: int = 6
    usage: Usage = field(default_factory=Usage)

    async def complete(self, system: str, user: str, temperature: float | None = None) -> str:
        async with self.semaphore:
            t0 = time.time()
            try:
                text, u = await asyncio.to_thread(self._complete_sync, system, user, temperature)
            except Exception:
                self.usage.errors += 1
                raise
            u.seconds = time.time() - t0
            self.usage.add(u)
            return text

    def _complete_sync(self, system: str, user: str, temperature: float | None) -> tuple[str, Usage]:
        raise NotImplementedError

    def cost_usd(self) -> float | None:
        price = self.price or PRICES.get(self.model)
        if not price:
            return None
        u = self.usage
        cache_read_rate = 0.1 if isinstance(self, AnthropicClient) else 1.0  # Claude bills cache reads at ~10%
        fresh = u.input_tokens - u.cache_read_tokens
        return (fresh * price[0] + u.cache_read_tokens * price[0] * cache_read_rate + u.output_tokens * price[1]) / 1e6

    # ---- HTTP with retries ----
    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode()
        delay = 2.0
        last: Exception | None = None
        for attempt in range(max(1, self.retries)):
            req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code not in RETRY_STATUS:
                    raise LLMError(f"{self.spec}: HTTP {e.code}: {detail}") from e
                last = LLMError(f"{self.spec}: HTTP {e.code}: {detail}")
                retry_after = e.headers.get("retry-after") if e.headers else None
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else delay
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = LLMError(f"{self.spec}: {e}")
                wait = delay
            time.sleep(min(wait, 60.0) + random.random())
            delay *= 2
        raise last or LLMError(f"{self.spec}: request failed")


class AnthropicClient(LLMClient):
    def _complete_sync(self, system: str, user: str, temperature: float | None) -> tuple[str, Usage]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            **self.extra_body,
        }
        if self.effort:
            body.setdefault("output_config", {})["effort"] = self.effort
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"}
        data = self._post(self.base_url.rstrip("/") + "/v1/messages", headers, body)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {}) or {}
        usage = Usage(
            calls=1,
            input_tokens=int(u.get("input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0) or 0)
            + int(u.get("cache_read_input_tokens", 0) or 0),
            output_tokens=int(u.get("output_tokens", 0)),
            cache_read_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        )
        if data.get("stop_reason") == "refusal" and not text:
            text = '{"refusal": true}'
        return text, usage


class OpenAICompatClient(LLMClient):
    def _complete_sync(self, system: str, user: str, temperature: float | None) -> tuple[str, Usage]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            **self.extra_body,
        }
        if self.effort:
            body["reasoning_effort"] = self.effort
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = self._post(self.base_url.rstrip("/") + "/chat/completions", headers, body)
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        u = data.get("usage", {}) or {}
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        prompt_t, completion_t = int(u.get("prompt_tokens", 0) or 0), int(u.get("completion_tokens", 0) or 0)
        # Some servers (e.g. Gemini) bill thinking tokens but leave them out of completion_tokens.
        hidden = max(0, int(u.get("total_tokens", 0) or 0) - prompt_t - completion_t)
        usage = Usage(calls=1, input_tokens=prompt_t, output_tokens=completion_t + hidden, cache_read_tokens=int(cached))
        return text, usage


class MockClient(LLMClient):
    """Offline fake model. Produces valid, slightly varied JSON so the full pipeline can be tested."""

    def _complete_sync(self, system: str, user: str, temperature: float | None) -> tuple[str, Usage]:
        rng = random.Random(hashlib.sha256((system + user).encode()).hexdigest())
        task = re.search(r"TASK: (\w+)", user)
        kind = task.group(1) if task else ""
        budget_m = re.search(r"You have (\d+) point", user)
        budget = int(budget_m.group(1)) if budget_m else 10
        if kind == "ITEM":
            out = {"item": rng.choice(["helmet", "knife", "blanket", "mirror", "megaphone", "first_aid_kit"]),
                   "thought": "Picking something useful."}
        elif kind == "MORNING":
            out = {"talk_points": 0 if "alone on this floor" in user else rng.randint(0, 2), "thought": "Let's see."}
        elif kind == "SPEAK":
            out = {"message": rng.choice(["Hi. Let's share fairly.", "I won't attack you.", "How many hearts do you have?",
                                          "I brought a book, nothing dangerous.", "The floors below need food too."])}
        else:
            guard = 0 if "alone on this floor" in user else rng.randint(0, budget // 2)
            attack = 0 if guard or "alone on this floor" in user or rng.random() > 0.05 else budget
            eat = budget - guard - attack
            out = {"eat": eat, "guard": guard, "attack": attack, "rest": 0, "platform_limit": None,
                   "notes": "Mock notes.", "thought": "Mock evening."}
        text = json.dumps(out)
        return text, Usage(calls=1, input_tokens=len(system + user) // 4, output_tokens=len(text) // 4)


def make_client(spec: str, providers: dict[str, Any] | None = None, concurrency: int = 8,
                effort: str | None = None, extra_body: dict[str, Any] | None = None) -> LLMClient:
    if ":" not in spec:
        raise ValueError(f"Model spec '{spec}' must look like 'provider:model', e.g. 'anthropic:claude-haiku-5-5'.")
    provider, model = spec.split(":", 1)
    conf = dict(PRESETS.get(provider, {}))
    conf.update((providers or {}).get(provider, {}))
    if not conf:
        raise ValueError(f"Unknown provider '{provider}'. Add it under \"providers\" in the config.")
    kind = conf.get("type")
    key_env = conf.get("api_key_env")
    api_key = os.environ.get(key_env) if key_env else conf.get("api_key")
    if kind in ("anthropic", "openai") and key_env and not api_key:
        raise ValueError(f"{spec}: environment variable {key_env} is not set.")
    common = dict(
        spec=spec, model=model, semaphore=asyncio.Semaphore(max(1, concurrency)),
        base_url=conf.get("base_url", ""), api_key=api_key, extra_body={**conf.get("extra_body", {}), **(extra_body or {})},
        effort=effort,
        price=tuple(conf["price"]) if conf.get("price") else None,
        max_tokens=int(conf.get("max_tokens", 4000)), timeout=float(conf.get("timeout", 180)),
    )
    cls = {"anthropic": AnthropicClient, "openai": OpenAICompatClient, "mock": MockClient}.get(kind)
    if cls is None:
        raise ValueError(f"Provider '{provider}' has unknown type '{kind}'.")
    return cls(**common)
