# DESCENT

A survival game for AI agents, inspired by vertical-prison stories.

Sixteen agents live two to a floor in an eight-floor tower. Once a day a platform of food comes down from the top. There's roughly enough for everyone **if** they share. Each agent has 10 hearts, which are both its health and its daily action budget, and splits them between **talking, eating, guarding, attacking and resting**. Killing your floor-mate is possible but hard: you have to catch them with their guard down. Every 30 days everyone is shuffled to a random floor.

The question: what do different models actually do when food is scarce, nobody can see anyone else's health, and betrayal is on the table?

Full rules: [PROJECT.md](PROJECT.md).

## Quick start

Python 3.10+ and nothing else. No packages to install.

```bash
# A bot-only game (free, instant)
python -m descent run configs/bots.json

# Claude Haiku 5.5 agents vs scripted bots
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
python -m descent run configs/haiku_vs_bots.json

# Try a short game first to check cost and behaviour
python -m descent run configs/all_haiku.json --days 5
```

API keys are read from `.env` in the folder you run from (git-ignored). Keys already exported in your shell take priority. Use `--env path/to/.env` to point somewhere else.

Then open `viewer/index.html` in a browser and load `runs/<run_id>/replay.json` (or drop the file on the page).

## Models

Agents are configured as `provider:model`:

| Provider | Example | Key |
|---|---|---|
| `anthropic` | `anthropic:claude-haiku-5-5` | `ANTHROPIC_API_KEY` |
| `gemini` | `gemini:gemini-3.8-flash` | `GEMINI_API_KEY` |
| `openai` | `openai:<model>` | `OPENAI_API_KEY` |
| `openrouter` | `openrouter:qwen/qwen3.8-27b` | `OPENROUTER_API_KEY` |
| `ollama` | `ollama:gemma4` (local) | none |
| `mock` | `mock:anything` (offline fake, for tests) | none |

Set reasoning effort per group with `"effort"` (`low`, `medium`, `high`; Claude also takes `xhigh`, `max`). It's sent as `output_config.effort` to Claude and `reasoning_effort` to OpenAI-compatible servers such as Gemini. Groups with different effort show up as separate entries in the results, e.g. `claude-sonnet-5-5 (low)`:

```json
{"policy": "llm", "model": "anthropic:claude-sonnet-5-5", "effort": "low", "count": 6}
```

Any other OpenAI-compatible server can be added under `"providers"` in a config:

```json
"providers": {
  "lmstudio": {"type": "openai", "base_url": "http://localhost:1234/v1", "api_key_env": null},
  "anthropic": {"extra_body": {"thinking": {"type": "disabled"}}}
}
```

Scripted bots: `bot:fair`, `bot:greedy`, `bot:paranoid`, `bot:killer`, `bot:random`.

## Commands

```bash
python -m descent run <config> [--seed N] [--days N] [--rule food_ratio=1.0]
python -m descent run <config> --repeat 5                  # 5 games, seeds N..N+4, one combined leaderboard
python -m descent tune --games 50 [--rule eat_rate=0.25]   # economy stats from bot games
python -m descent summary runs/<run_id>/replay.json
python -m unittest discover -s tests                       # tests
```

## The three-model run

`configs/three_models.json` puts 6 Haiku 5.5, 6 Gemini 3.8 Flash and 6 Sonnet 5.5 (low effort) agents in a 9-floor tower for 90 days:

```bash
python -m descent run configs/three_models.json
```

Before day 1 it makes one tiny call per model, so a wrong key or model name fails in seconds. The replay is saved after every day, so if the run stops halfway you can still watch what happened. The running cost prints on each day's line.

## Comparing models

One game is mostly luck: who starts on floor 1, who gets paired with whom. Use `--repeat` to play the same setup with several seeds and get one leaderboard per model (survival rate, average days survived, hearts, kills, greed). Each game's replay and a `results.json` are saved in one folder. Spending limits apply per game.

## Spending limits

- `"budget_usd": 5` at the top of a config stops the run once total spend reaches $5.
- `"max_cost_usd": 5` on an agent group caps that one model.
- If a model fails 6 calls in a row (credit ran out, rate limit), the run stops instead of continuing with fallback moves. Change the count with `"stop_after_consecutive_errors"`.

A stopped run still saves its replay up to the last full day, and the summary says why it stopped.

## Cost

Each agent makes about 3–4 calls a day (morning, its messages, evening). A full 16-agent, 90-day game is roughly 5,000 calls. With Haiku 5.5 that's in the low single-digit dollars; check the "API usage" section printed after each run. Start with `--days 5`.

## What gets measured

Per agent and per model: days survived, final hearts, kills, attacks made, food taken versus a fair ration ("greed index"), food eaten from bodies, and invalid outputs. Every message, allocation, private thought and note is in the replay.

## Layout

```
descent/
  config.py      all rule numbers
  rules.py       pure rule functions (combat, eating, night)
  engine.py      the game loop and replay recorder
  policies/      bot policies and the policy interface
  llm/           stdlib HTTP clients, prompts, JSON parsing, LLM policy
  tune.py        economy tuning with bots
viewer/index.html  replay viewer (single file)
configs/           example games
tests/             unit + end-to-end tests
```
