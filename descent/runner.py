"""Build a game from a JSON config and run it."""

from __future__ import annotations

import asyncio
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .config import Rules
from .engine import Game
from .policies.base import Policy
from .policies.bots import make_bot
from .state import Agent

NAMES = [
    "Ava", "Bram", "Cyra", "Dax", "Elin", "Fenn", "Gia", "Hugo", "Iris", "Jun", "Kira", "Leo",
    "Mara", "Nico", "Oona", "Pax", "Quin", "Rhea", "Soren", "Tali", "Uma", "Vik", "Wren", "Yara",
]


def build_game(cfg: dict[str, Any], on_day=None) -> Game:
    seed = int(cfg.get("seed", 0))
    rules = Rules.from_dict(cfg.get("rules"))
    rng = random.Random(seed)
    names = NAMES[:]
    rng.shuffle(names)

    llm_clients: dict[str, Any] = {}
    agents: list[Agent] = []
    policies: dict[str, Policy] = {}
    idx = 0
    for group in cfg.get("agents", []):
        count = int(group.get("count", 1))
        policy_key = group.get("policy", "llm")
        for _ in range(count):
            idx += 1
            aid = f"a{idx:02d}"
            name = group.get("name") if count == 1 and group.get("name") else names[(idx - 1) % len(names)]
            if policy_key.startswith("bot:"):
                pol: Policy = make_bot(policy_key[4:], seed=rng.randrange(1 << 30))
                model = None
            elif policy_key == "llm":
                from .llm.policy import LLMPolicy, make_client

                spec = group["model"]
                effort = group.get("effort")
                label = group.get("label") or (f"{spec} ({effort})" if effort else spec)
                if label not in llm_clients:
                    llm_clients[label] = make_client(spec, cfg.get("providers", {}), int(cfg.get("concurrency", 16)),
                                                     effort=effort, extra_body=group.get("extra_body"))
                    if group.get("max_cost_usd") is not None:
                        llm_clients[label].max_cost_usd = float(group["max_cost_usd"])
                pol = LLMPolicy(llm_clients[label], temperature=group.get("temperature"))
                pol.model = label
                model = label
            else:
                raise ValueError(f"Unknown policy '{policy_key}'. Use 'llm' or 'bot:<name>'.")
            agents.append(Agent(id=aid, name=name, policy_name=pol.name, model=model))
            policies[aid] = pol
    if not agents:
        raise ValueError("Config has no agents.")
    game = Game(rules, agents, policies, seed=seed, on_day=on_day)
    game.llm_clients = llm_clients  # type: ignore[attr-defined]
    return game


def model_usage(game: Game) -> dict[str, dict]:
    models = {}
    for label, client in getattr(game, "llm_clients", {}).items():
        u = client.usage
        cost = client.cost_usd()
        models[label] = {"calls": u.calls, "errors": u.errors, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                         "cache_read_tokens": u.cache_read_tokens, "cost_usd": None if cost is None else round(cost, 4)}
    return models


async def preflight(game: Game) -> None:
    """One tiny call per model so a bad key or model name fails in seconds, not 90 days later."""
    from .llm.parse import extract_json

    async def check(label: str, client) -> str | None:
        saved, client.retries = client.retries, 2
        try:
            text = await client.complete("Reply with a single JSON object and nothing else.", 'Reply with {"ok": true}')
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            return msg if msg.startswith(client.spec) else f"{label}: {msg}"
        finally:
            client.retries = saved
        return None if extract_json(text) is not None else f"{label}: replied without JSON: {text[:120]!r}"

    clients = getattr(game, "llm_clients", {})
    errors = [e for e in await asyncio.gather(*(check(k, c) for k, c in clients.items())) if e]
    if errors:
        raise ValueError("Model check failed:\n  " + "\n  ".join(errors))


async def run_config(cfg: dict[str, Any], out_dir: str | Path | None = "runs", verbose: bool = True,
                     check_models: bool = True) -> dict:
    game_ref: dict[str, Game] = {}
    path: Path | None = None
    if out_dir is not None:
        run_id = cfg.get("run_id") or time.strftime("%Y%m%d-%H%M%S") + f"-s{int(cfg.get('seed', 0))}"
        path = Path(out_dir) / run_id

    def save(replay: dict) -> None:
        if path is None:
            return
        path.mkdir(parents=True, exist_ok=True)
        tmp = path / "replay.json.tmp"
        tmp.write_text(json.dumps(replay, indent=1))
        tmp.replace(path / "replay.json")

    def snapshot_replay(game: Game) -> dict:
        replay = game.replay(game.initial, time.time() - game.t0)
        replay["config"] = {k: v for k, v in cfg.items() if k != "providers"}
        replay["summary"]["models"] = model_usage(game)
        return replay

    max_errors = int(cfg.get("stop_after_consecutive_errors", 6))
    budget = cfg.get("budget_usd")

    def check_limits(game: Game) -> None:
        clients = getattr(game, "llm_clients", {})
        for label, c in clients.items():
            if c.consecutive_errors >= max_errors:
                game.stop_reason = (f"{label} failed {c.consecutive_errors} calls in a row "
                                    f"(credit or rate limit?). Days after this would be fallback moves, so the run stopped.")
                return
            cost = c.cost_usd()
            if c.max_cost_usd is not None and cost is not None and cost >= c.max_cost_usd:
                game.stop_reason = f"{label} reached its ${c.max_cost_usd:g} cap (${cost:.2f})."
                return
        if budget is not None:
            total = sum(c.cost_usd() or 0 for c in clients.values())
            if total >= float(budget):
                game.stop_reason = f"Total spend reached the ${float(budget):g} budget (${total:.2f})."

    def progress(day: dict) -> None:
        game = game_ref["g"]
        check_limits(game)
        save(snapshot_replay(game))  # a crash or Ctrl-C still leaves a watchable replay
        if not verbose:
            return
        alive = sum(1 for s in day["end"].values() if s["alive"])
        bits = [f"day {day['day']:>3}", f"alive {alive:>2}"]
        for d in day["deaths"]:
            bits.append(f"† {game.agents[d['id']].name} ({d['cause']})")
        if day["combat"]:
            bits.append(f"{len(day['combat'])} attack(s)")
        if "shuffle" in day:
            bits.append("SHUFFLE")
        costs = [c.cost_usd() for c in getattr(game, "llm_clients", {}).values()]
        if any(c is not None for c in costs):
            bits.append(f"${sum(c or 0 for c in costs):.2f} so far")
        print("  ".join(bits), flush=True)
        if game.stop_reason:
            print(f"STOPPED after day {day['day']}: {game.stop_reason}", flush=True)

    # Plenty of threads so every agent's call can be in flight at once (HTTP waits, not CPU).
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=64))
    game = build_game(cfg, on_day=progress)
    game_ref["g"] = game
    if check_models and getattr(game, "llm_clients", None):
        if verbose:
            print("Checking models: " + ", ".join(game.llm_clients), flush=True)
        await preflight(game)
    replay = await game.run()
    replay["config"] = {k: v for k, v in cfg.items() if k != "providers"}
    replay["summary"]["models"] = model_usage(game)
    save(replay)
    if path is not None:
        replay["_path"] = str(path / "replay.json")
    return replay
