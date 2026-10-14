"""Command line.

    python -m descent run configs/haiku_vs_bots.json
    python -m descent run configs/haiku_vs_bots.json --days 10 --seed 7
    python -m descent tune --games 30 --rule food_ratio=1.0
    python -m descent summary runs/<run_id>/replay.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _rule_overrides(pairs: list[str]) -> dict:
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def print_summary(replay: dict) -> None:
    s = replay["summary"]
    print(f"\nDays played: {s['days_played']}   alive: {s['alive']}   killed: {s['deaths_killed']}   starved: {s['deaths_starved']}")
    print(f"\n{'name':<8}{'model / policy':<34}{'item':<14}{'days':>5}{'hearts':>7}{'kills':>6}{'greed':>7}{'invalid':>8}  fate")
    for a in sorted(s["agents"], key=lambda a: (-a["days_survived"], -a["final_hearts"])):
        fate = "alive" if a["alive"] else f"{a['death_cause']} day {a['death_day']}"
        if a.get("killed_by"):
            killer = next((x["name"] for x in s["agents"] if x["id"] == a["killed_by"]), a["killed_by"])
            fate += f" by {killer}"
        who = a["model"] or a["policy"]
        print(f"{a['name']:<8}{who[:33]:<34}{a['item']:<14}{a['days_survived']:>5}{a['final_hearts']:>7}{a['kills']:>6}{a['greed_index']:>7}{a['invalid_outputs']:>8}  {fate}")
    print("\nBy model / policy:")
    for k, g in sorted(s["by_group"].items(), key=lambda kv: -kv[1]["avg_days_survived"]):
        print(f"  {k:<36} n={g['agents']:<3} alive={g['alive']:<3} avg_days={g['avg_days_survived']:<6} "
              f"avg_hearts={g['avg_final_hearts']:<5} kills={g['kills']:<3} attacks={g['attacks_made']:<4} greed={g['avg_greed_index']}")
    if s.get("models"):
        print("\nAPI usage:")
        for spec, u in s["models"].items():
            cost = f"${u['cost_usd']:.3f}" if u["cost_usd"] is not None else "n/a"
            print(f"  {spec:<36} calls={u['calls']:<6} in={u['input_tokens']:<10} out={u['output_tokens']:<9} errors={u['errors']:<4} cost≈{cost}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="descent", description="DESCENT — a survival game for AI agents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one game from a config file")
    r.add_argument("config")
    r.add_argument("--seed", type=int)
    r.add_argument("--days", type=int, help="override game length")
    r.add_argument("--out", default="runs")
    r.add_argument("--rule", action="append", help="override a rule, e.g. --rule food_ratio=1.0")
    r.add_argument("--quiet", action="store_true")

    t = sub.add_parser("tune", help="run many bot-only games and print economy stats")
    t.add_argument("--games", type=int, default=30)
    t.add_argument("--rule", action="append")
    t.add_argument("--mix", action="append", help="limit to these mixes")

    s = sub.add_parser("summary", help="print the summary of a replay file")
    s.add_argument("replay")

    r.add_argument("--env", help="path to a .env file (default: ./.env)")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        from .env import load_dotenv

        load_dotenv(args.env)
        from .runner import run_config

        cfg = json.loads(Path(args.config).read_text())
        cfg.setdefault("rules", {})
        if args.seed is not None:
            cfg["seed"] = args.seed
        if args.days is not None:
            cfg["rules"]["days"] = args.days
        cfg["rules"].update(_rule_overrides(args.rule))
        try:
            replay = asyncio.run(run_config(cfg, out_dir=args.out, verbose=not args.quiet))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        print_summary(replay)
        print(f"\nReplay saved to {replay['_path']} — open viewer/index.html and load it.")
        return 0

    if args.cmd == "tune":
        from .tune import print_table, tune

        res = asyncio.run(tune(args.games, _rule_overrides(args.rule), args.mix))
        print_table(res)
        return 0

    if args.cmd == "summary":
        print_summary(json.loads(Path(args.replay).read_text()))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
