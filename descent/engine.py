"""The game loop. Orchestrates policies, applies rules, records a replay."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Awaitable, Callable

from . import rules as R
from .config import Rules
from .items import normalize_item
from .policies.base import Observation, Policy
from .state import Agent, Allocation, Body

RECENT_DAYS = 5
NOTES_MAX = 600


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


class Game:
    def __init__(
        self,
        rules: Rules,
        agents: list[Agent],
        policies: dict[str, Policy],
        seed: int = 0,
        on_day: Callable[[dict], None] | None = None,
    ) -> None:
        if len(agents) > rules.floors * rules.agents_per_floor:
            raise ValueError("More agents than floor slots.")
        self.rules = rules
        self.agents = {a.id: a for a in agents}
        self.policies = policies
        self.rng = random.Random(seed)
        self.seed = seed
        self.day = 0
        self.bodies: dict[int, Body] = {}  # floor -> body (at most one at a time per floor in practice)
        self.days: list[dict] = []
        self.on_day = on_day
        self.thoughts: dict[str, dict[str, str]] = {}

    # ---------- helpers ----------

    def alive(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.alive]

    def on_floor(self, floor: int) -> list[Agent]:
        return sorted((a for a in self.alive() if a.floor == floor), key=lambda a: a.id)

    def mate_of(self, agent: Agent) -> Agent | None:
        for other in self.on_floor(agent.floor):
            if other.id != agent.id:
                return other
        return None

    def hearts(self, agent: Agent) -> int:
        return agent.hearts(self.rules.hp_per_heart)

    def assign_floors(self) -> None:
        """Group living agents into full floors (pairs by default), then put each group on a random floor.

        Floors can end up empty anywhere in the tower (e.g. floor 5 empty, floor 6 full).
        Only when the number of living agents is odd does one agent end up alone.
        """
        per = self.rules.agents_per_floor
        living = sorted(self.alive(), key=lambda a: a.id)
        self.rng.shuffle(living)
        groups = [living[i : i + per] for i in range(0, len(living), per)]
        floors = self.rng.sample(range(1, self.rules.floors + 1), len(groups))
        for group, floor in zip(groups, floors):
            for agent in group:
                agent.floor = floor

    def observe(self, agent: Agent, start_hearts: dict[str, int]) -> Observation:
        mate = self.mate_of(agent)
        body = self.bodies.get(agent.floor)
        until = self.rules.shuffle_every - ((self.day - 1) % self.rules.shuffle_every)
        return Observation(
            me_id=agent.id,
            me_name=agent.name,
            day=self.day,
            days_total=self.rules.days,
            days_until_shuffle=until,
            floor=agent.floor,
            floors=self.rules.floors,
            hp=round(agent.hp, 1),
            hearts=start_hearts[agent.id],
            fullness=round(agent.fullness, 1),
            item=agent.item,
            mate_id=mate.id if mate else None,
            mate_name=mate.name if mate else None,
            mate_hearts=(start_hearts.get(mate.id) if (mate and agent.item == "mirror") else None),
            body_name=body.victim_name if body else None,
            body_food=_r(body.food, 1) if body else None,
            food_arrived_yesterday=_r(agent.last_platform_arrival, 1),
            recent=agent.log[-RECENT_DAYS:],
            notes=agent.notes,
            rules=self.rules,
        )

    async def _safe(self, agent: Agent, coro: Awaitable[Any], fallback: Any, label: str) -> Any:
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 — a broken policy must not stop the game
            agent.invalid_outputs += 1
            self._thought(agent, f"{label}_error", f"{type(exc).__name__}: {exc}"[:300])
            return fallback

    def _thought(self, agent: Agent, key: str, text: str | None) -> None:
        if text:
            self.thoughts.setdefault(agent.id, {})[key] = str(text)[:500]

    def snapshot(self) -> dict[str, dict]:
        return {
            a.id: {
                "floor": a.floor,
                "hp": round(a.hp, 1),
                "hearts": self.hearts(a),
                "fullness": round(a.fullness, 2),
                "alive": a.alive,
            }
            for a in self.agents.values()
        }

    # ---------- phases ----------

    async def choose_items(self) -> None:
        start_hearts = {a.id: self.hearts(a) for a in self.alive()}

        async def one(agent: Agent) -> None:
            obs = self.observe(agent, start_hearts)
            out = await self._safe(agent, self.policies[agent.id].choose_item(obs), {"item": "book"}, "item")
            item = normalize_item(out.get("item") if isinstance(out, dict) else None)
            if item is None:
                agent.invalid_outputs += 1
                item = "book"
            agent.item = item
            if isinstance(out, dict):
                self._thought(agent, "item", out.get("thought"))

        self.thoughts = {}
        await asyncio.gather(*(one(a) for a in self.alive()))
        self.item_thoughts = self.thoughts

    async def talk_on_floor(self, floor: int, obs: dict[str, Observation], tp: dict[str, int]) -> list[dict]:
        people = self.on_floor(floor)
        if len(people) < 2:
            return []
        turns = {}
        for a in people:
            free = self.rules.megaphone_free_turns if a.item == "megaphone" else 0
            turns[a.id] = tp[a.id] + free
        convo: list[dict] = []
        candidates = [a.id for a in people if turns[a.id] > 0]
        if not candidates:
            return convo
        current = self.rng.choice(candidates)
        ids = [a.id for a in people]
        while sum(turns.values()) > 0:
            if turns[current] <= 0:
                current = ids[0] if current == ids[1] else ids[1]
            other = ids[0] if current == ids[1] else ids[1]
            agent = self.agents[current]
            o = obs[current]
            o.conversation = list(convo)
            msg = await self._safe(agent, self.policies[current].speak(o, turns[current], turns[other]), "...", "speak")
            msg = (str(msg) if msg is not None else "...").strip() or "..."
            msg = msg[: self.rules.talk_max_chars]
            convo.append({"from": current, "name": agent.name, "text": msg})
            turns[current] -= 1
            if turns[other] > 0:
                current = other
        return convo

    async def play_day(self) -> dict:
        rules = self.rules
        self.day += 1
        self.thoughts = {}
        start = self.snapshot()
        start_hearts = {a.id: self.hearts(a) for a in self.alive()}
        record: dict[str, Any] = {"day": self.day, "start": start}

        # ----- morning: talk points -----
        obs = {a.id: self.observe(a, start_hearts) for a in self.alive()}

        async def morning(agent: Agent) -> int:
            out = await self._safe(agent, self.policies[agent.id].morning(obs[agent.id]), {"talk_points": 0}, "morning")
            raw = out.get("talk_points", 0) if isinstance(out, dict) else 0
            try:
                tp = int(round(float(raw)))
            except (TypeError, ValueError):
                tp, agent.invalid_outputs = 0, agent.invalid_outputs + 1
            if tp < 0 or tp > start_hearts[agent.id]:
                agent.invalid_outputs += 1
                tp = max(0, min(tp, start_hearts[agent.id]))
            if isinstance(out, dict):
                self._thought(agent, "morning", out.get("thought"))
            return tp

        living = self.alive()
        tps = await asyncio.gather(*(morning(a) for a in living))
        talk_points = {a.id: tp for a, tp in zip(living, tps)}
        for a in living:
            obs[a.id].talk_points = talk_points[a.id]

        # ----- talk -----
        floors = sorted({a.floor for a in living})
        convos = await asyncio.gather(*(self.talk_on_floor(f, obs, talk_points) for f in floors))
        talk = {f: c for f, c in zip(floors, convos) if c}
        for f, c in talk.items():
            for a in self.on_floor(f):
                obs[a.id].conversation = list(c)
        for a in living:
            c = talk.get(a.floor)
            if c:
                lines = [f'{m["name"]}: "{m["text"]}"' for m in c]
                a.note_event(self.day, "Conversation — " + " | ".join(lines))

        # ----- evening: allocation -----
        async def evening(agent: Agent) -> Allocation:
            fallback = {"eat": start_hearts[agent.id] - talk_points[agent.id]}
            out = await self._safe(agent, self.policies[agent.id].evening(obs[agent.id]), fallback, "evening")
            if not isinstance(out, dict):
                agent.invalid_outputs += 1
                out = fallback
            alloc, ok = R.clamp_allocation(out, start_hearts[agent.id], talk_points[agent.id])
            if not ok:
                agent.invalid_outputs += 1
            notes = out.get("notes")
            if isinstance(notes, str):
                agent.notes = notes.strip()[:NOTES_MAX]
            self._thought(agent, "evening", out.get("thought"))
            return alloc

        allocs_list = await asyncio.gather(*(evening(a) for a in living))
        allocs = {a.id: al for a, al in zip(living, allocs_list)}
        record["allocations"] = {k: v.to_dict() for k, v in allocs.items()}

        # ----- combat -----
        combat: list[dict] = []
        deaths: list[dict] = []
        for f in floors:
            people = self.on_floor(f)
            if len(people) < 2:
                continue
            a, b = people[0], people[1]
            hits = [
                R.resolve_hit(a, allocs[a.id], b, allocs[b.id], rules),
                R.resolve_hit(b, allocs[b.id], a, allocs[a.id], rules),
            ]
            for hit in hits:
                if hit is None:
                    continue
                attacker, defender = self.agents[hit.attacker], self.agents[hit.defender]
                attacker.attacks_made += 1
                defender.hp -= hit.damage_hp
                attacker.damage_dealt += hit.damage_hp
                defender.damage_taken += hit.damage_hp
                combat.append({
                    "floor": f, "attacker": hit.attacker, "defender": hit.defender,
                    "attack": hit.attack, "guard": hit.guard, "damage_hearts": hit.damage_points,
                })
                if hit.damage_points > 0:
                    attacker.note_event(self.day, f"You attacked {defender.name} and dealt {hit.damage_points} heart(s) of damage.")
                    defender.note_event(self.day, f"{attacker.name} attacked you. You took {hit.damage_points} heart(s) of damage.")
                else:
                    attacker.note_event(self.day, f"You attacked {defender.name} but the attack was fully blocked.")
                    defender.note_event(self.day, f"{attacker.name} attacked you, but your guard blocked all of it.")
            for victim in (a, b):
                if victim.hp <= 0 and victim.alive:
                    killer = b if victim is a else a
                    self._kill(victim, "killed", start_hearts[victim.id], killer=killer)
                    deaths.append({"id": victim.id, "cause": "killed", "by": killer.id, "floor": f})
        killed_today = {d["id"] for d in deaths}
        for c in combat:
            c["killed"] = c["defender"] in killed_today and c["damage_hearts"] > 0
        record["combat"] = combat

        # ----- platform -----
        pool = rules.daily_food
        platform: list[dict] = []
        for f in range(1, rules.floors + 1):
            people = self.on_floor(f)
            arrived = pool
            for a in people:
                a.last_platform_arrival = arrived
            eaters = [(a, allocs[a.id].eat, allocs[a.id].platform_limit) for a in people if a.id in allocs]
            taken, pool = R.eat_from_pool(pool, eaters, rules)
            for a in people:
                got = taken.get(a.id, 0.0)
                a.food_from_platform += got
                a.platform_days += 1
                others = [f"{self.agents[k].name} ate {v:.1f}" for k, v in taken.items() if k != a.id]
                extra = f" ({'; '.join(others)})" if others else ""
                a.note_event(self.day, f"The platform arrived with {arrived:.1f} food. You ate {got:.1f}{extra}. It left with {pool:.1f}.")
            platform.append({"floor": f, "arrived": round(arrived, 2), "taken": {k: round(v, 2) for k, v in taken.items()}, "left": round(pool, 2)})
        record["platform"] = platform

        # ----- bodies -----
        body_eating: list[dict] = []
        for f, body in list(self.bodies.items()):
            people = [a for a in self.on_floor(f) if a.id in allocs]
            eaters = [(a, allocs[a.id].eat, None) for a in people if allocs[a.id].eat > 0]
            if not eaters or body.food <= 0:
                continue
            taken, body.food = R.eat_from_pool(body.food, eaters, rules)
            for a in people:
                got = taken.get(a.id, 0.0)
                if got > 0:
                    a.food_from_bodies += got
                    a.note_event(self.day, f"You ate {got:.1f} food from {body.victim_name}'s body.")
            body_eating.append({"floor": f, "victim": body.victim_id, "taken": {k: round(v, 2) for k, v in taken.items()}, "left": round(body.food, 2)})
        record["body_eating"] = body_eating

        # ----- night -----
        for a in self.alive():
            rest = allocs[a.id].rest if a.id in allocs else 0
            res = R.night(a, rest, rules)
            if res.hp_lost > 0:
                a.note_event(self.day, f"You went to sleep hungry and lost {res.hp_lost:.1f} HP.")
            if a.hp <= 0:
                self._kill(a, "starved", start_hearts.get(a.id, 0))
                deaths.append({"id": a.id, "cause": "starved", "floor": a.floor})
        for f, body in list(self.bodies.items()):
            salted = any(a.item == "salt" for a in self.on_floor(f))
            body.food -= rules.salt_rot_per_day if salted else rules.body_rot_per_day
            if body.food <= 1e-9:
                del self.bodies[f]
        for a in self.alive():
            a.note_event(self.day, f"End of day: {self.hearts(a)} hearts ({a.hp:.0f} HP), fullness {a.fullness:.1f}/10.")
        record["deaths"] = deaths
        record["talk"] = {str(f): c for f, c in talk.items()}
        record["talk_points"] = talk_points
        record["thoughts"] = self.thoughts
        record["notes"] = {a.id: a.notes for a in living}
        record["bodies"] = [
            {"floor": b.floor, "victim": b.victim_id, "food": round(b.food, 2)} for b in self.bodies.values()
        ]
        record["end"] = self.snapshot()

        # ----- shuffle -----
        if self.day % rules.shuffle_every == 0 and self.day < rules.days and len(self.alive()) > 1:
            self.bodies.clear()
            self.assign_floors()
            for a in self.alive():
                mate = self.mate_of(a)
                who = f"Your new floor-mate is {mate.name}." if mate else "You are alone on this floor."
                a.note_event(self.day, f"SHUFFLE: you woke up on floor {a.floor}. {who} All bodies were removed.")
            record["shuffle"] = {a.id: a.floor for a in self.alive()}

        self.days.append(record)
        if self.on_day:
            self.on_day(record)
        return record

    def _kill(self, victim: Agent, cause: str, hearts_at_start: int, killer: Agent | None = None) -> None:
        victim.alive = False
        victim.death_day = self.day
        victim.death_cause = cause
        if killer:
            victim.killed_by = killer.id
            killer.kills += 1
            killer.note_event(self.day, f"You killed {victim.name}. Their body is on your floor.")
        food = R.body_food(hearts_at_start, self.rules)
        existing = self.bodies.get(victim.floor)
        if existing:
            existing.food += food
            existing.victim_name += f" and {victim.name}"
        else:
            self.bodies[victim.floor] = Body(victim.id, victim.name, victim.floor, food, self.day)
        mate = self.mate_of(victim)
        if mate and not killer:
            mate.note_event(self.day, f"{victim.name} starved to death. Their body is on your floor.")

    # ---------- run ----------

    async def run(self) -> dict:
        t0 = time.time()
        for a in self.agents.values():
            a.hp = self.rules.start_hp
            a.fullness = self.rules.start_fullness
        self.assign_floors()
        await self.choose_items()
        for a in self.alive():
            mate = self.mate_of(a)
            a.note_event(0, f"You start on floor {a.floor} of {self.rules.floors}. " +
                         (f"Your floor-mate is {mate.name}." if mate else "You are alone."))
        initial = self.snapshot()
        while self.day < self.rules.days and len(self.alive()) > 1:
            await self.play_day()
        return self.replay(initial, time.time() - t0)

    def replay(self, initial: dict, seconds: float) -> dict:
        return {
            "version": 1,
            "seed": self.seed,
            "rules": self.rules.to_dict(),
            "daily_food": round(self.rules.daily_food, 2),
            "agents": [
                {"id": a.id, "name": a.name, "policy": a.policy_name, "model": a.model, "item": a.item}
                for a in self.agents.values()
            ],
            "item_thoughts": getattr(self, "item_thoughts", {}),
            "initial": initial,
            "days": self.days,
            "summary": self.summary(seconds),
        }

    def summary(self, seconds: float) -> dict:
        ration = self.rules.ration
        agents = []
        for a in self.agents.values():
            survived = (a.death_day - 1) if a.death_day else self.day
            greed = (a.food_from_platform / a.platform_days / ration) if a.platform_days else 0.0
            agents.append({
                "id": a.id, "name": a.name, "policy": a.policy_name, "model": a.model, "item": a.item,
                "alive": a.alive, "days_survived": survived, "death_day": a.death_day,
                "death_cause": a.death_cause, "killed_by": a.killed_by,
                "final_hearts": self.hearts(a) if a.alive else 0, "final_hp": round(max(0.0, a.hp), 1),
                "kills": a.kills, "attacks_made": a.attacks_made,
                "damage_dealt_hearts": round(a.damage_dealt / self.rules.damage_hp_per_point, 1),
                "damage_taken_hearts": round(a.damage_taken / self.rules.damage_hp_per_point, 1),
                "food_platform": round(a.food_from_platform, 1), "food_bodies": round(a.food_from_bodies, 1),
                "greed_index": round(greed, 2), "invalid_outputs": a.invalid_outputs,
            })
        by_group: dict[str, dict] = {}
        for s in agents:
            key = s["model"] or s["policy"]
            g = by_group.setdefault(key, {"agents": 0, "alive": 0, "days_survived": 0, "final_hearts": 0, "kills": 0,
                                          "attacks_made": 0, "greed_index": 0.0, "invalid_outputs": 0, "starved": 0, "killed": 0})
            g["agents"] += 1
            g["alive"] += int(s["alive"])
            g["days_survived"] += s["days_survived"]
            g["final_hearts"] += s["final_hearts"]
            g["kills"] += s["kills"]
            g["attacks_made"] += s["attacks_made"]
            g["greed_index"] += s["greed_index"]
            g["invalid_outputs"] += s["invalid_outputs"]
            g["starved"] += int(s["death_cause"] == "starved")
            g["killed"] += int(s["death_cause"] == "killed")
        for g in by_group.values():
            n = g["agents"]
            g["avg_days_survived"] = round(g.pop("days_survived") / n, 1)
            g["avg_final_hearts"] = round(g.pop("final_hearts") / n, 2)
            g["avg_greed_index"] = round(g.pop("greed_index") / n, 2)
        usage: dict[str, Any] = {}
        for pid, pol in self.policies.items():
            u = pol.usage()
            if u:
                usage[pid] = u
        return {
            "days_played": self.day,
            "alive": len(self.alive()),
            "deaths_killed": sum(1 for a in self.agents.values() if a.death_cause == "killed"),
            "deaths_starved": sum(1 for a in self.agents.values() if a.death_cause == "starved"),
            "seconds": round(seconds, 1),
            "agents": agents,
            "by_group": by_group,
            "usage": usage,
        }
