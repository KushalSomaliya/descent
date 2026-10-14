"""Build a game from a JSON config and run it."""

from __future__ import annotations

import json
import random
import time
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
                if spec not in llm_clients:
                    llm_clients[spec] = make_client(spec, cfg.get("providers", {}), int(cfg.get("concurrency", 8)))
                pol = LLMPolicy(llm_clients[spec], temperature=group.get("temperature"))
                model = spec
            else:
                raise ValueError(f"Unknown policy '{policy_key}'. Use 'llm' or 'bot:<name>'.")
            agents.append(Agent(id=aid, name=name, policy_name=pol.name, model=model))
            policies[aid] = pol
    if not agents:
        raise ValueError("Config has no agents.")
    game = Game(rules, agents, policies, seed=seed, on_day=on_day)
    game.llm_clients = llm_clients  # type: ignore[attr-defined]
    return game


async def run_config(cfg: dict[str, Any], out_dir: str | Path | None = "runs", verbose: bool = True) -> dict:
    def progress(day: dict) -> None:
        if not verbose:
            return
        alive = sum(1 for s in day["end"].values() if s["alive"])
        bits = [f"day {day['day']:>3}", f"alive {alive:>2}"]
        for d in day["deaths"]:
            bits.append(f"† {d['id']} ({d['cause']})")
        if day["combat"]:
            bits.append(f"{len(day['combat'])} attack(s)")
        if "shuffle" in day:
            bits.append("SHUFFLE")
        print("  ".join(bits), flush=True)

    game = build_game(cfg, on_day=progress)
    replay = await game.run()
    replay["config"] = {k: v for k, v in cfg.items() if k != "providers"}
    models = {}
    for spec, client in getattr(game, "llm_clients", {}).items():
        u = client.usage
        cost = client.cost_usd()
        models[spec] = {"calls": u.calls, "errors": u.errors, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                        "cache_read_tokens": u.cache_read_tokens, "cost_usd": None if cost is None else round(cost, 4)}
    replay["summary"]["models"] = models
    if out_dir is not None:
        run_id = cfg.get("run_id") or time.strftime("%Y%m%d-%H%M%S") + f"-s{game.seed}"
        path = Path(out_dir) / run_id
        path.mkdir(parents=True, exist_ok=True)
        (path / "replay.json").write_text(json.dumps(replay, indent=1))
        replay["_path"] = str(path / "replay.json")
    return replay
