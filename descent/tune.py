"""Run many bot-only games to check the economy is tense. No API calls.

    python -m descent tune --games 50
    python -m descent tune --games 50 --rule eat_rate=0.3 --rule food_ratio=1.0
"""

from __future__ import annotations

import asyncio
from statistics import mean
from typing import Any

from .runner import build_game

MIXES: dict[str, list[dict[str, Any]]] = {
    "all_fair": [{"policy": "bot:fair", "count": 16}],
    "all_greedy": [{"policy": "bot:greedy", "count": 16}],
    "all_paranoid": [{"policy": "bot:paranoid", "count": 16}],
    "mixed": [
        {"policy": "bot:fair", "count": 4}, {"policy": "bot:greedy", "count": 4},
        {"policy": "bot:paranoid", "count": 4}, {"policy": "bot:killer", "count": 2},
        {"policy": "bot:random", "count": 2},
    ],
    "fair_vs_killers": [{"policy": "bot:fair", "count": 12}, {"policy": "bot:killer", "count": 4}],
}


async def _one(mix: list[dict], rules: dict, seed: int) -> dict:
    game = build_game({"seed": seed, "rules": rules, "agents": mix})
    replay = await game.run()
    s = replay["summary"]
    # Starvation pressure by floor in the first cycle (before any shuffle).
    first_cycle = replay["days"][: replay["rules"]["shuffle_every"]]
    initial = replay["initial"]
    bottom = [aid for aid, st in initial.items() if st["floor"] == replay["rules"]["floors"]]
    top = [aid for aid, st in initial.items() if st["floor"] == 1]
    def end_hp(ids):
        if not first_cycle:
            return 0.0
        last = first_cycle[-1]["end"]
        return mean(last[i]["hp"] if last[i]["alive"] else 0.0 for i in ids) if ids else 0.0
    platform_left = mean(d["platform"][-1]["left"] for d in replay["days"]) if replay["days"] else 0.0
    return {
        "alive": s["alive"],
        "starved": s["deaths_starved"],
        "killed": s["deaths_killed"],
        "greed": mean(a["greed_index"] for a in s["agents"]),
        "max_greed": max(a["greed_index"] for a in s["agents"]),
        "top_hp_d30": end_hp(top),
        "bottom_hp_d30": end_hp(bottom),
        "food_left_bottom": platform_left,
    }


async def tune(games: int = 30, rules: dict | None = None, mixes: list[str] | None = None) -> dict[str, dict]:
    rules = rules or {}
    out: dict[str, dict] = {}
    for name in mixes or list(MIXES):
        results = await asyncio.gather(*(_one(MIXES[name], rules, seed) for seed in range(games)))
        agg = {k: round(mean(r[k] for r in results), 2) for k in results[0]}
        out[name] = agg
    return out


def print_table(res: dict[str, dict]) -> None:
    cols = ["alive", "starved", "killed", "greed", "max_greed", "top_hp_d30", "bottom_hp_d30", "food_left_bottom"]
    print(f"{'mix':<18}" + "".join(f"{c:>16}" for c in cols))
    for name, agg in res.items():
        print(f"{name:<18}" + "".join(f"{agg[c]:>16}" for c in cols))
