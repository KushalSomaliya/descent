import asyncio
import unittest

from descent import rules as R
from descent.config import Rules
from descent.engine import Game
from descent.llm.parse import extract_json
from descent.policies.base import Policy
from descent.runner import build_game
from descent.state import Agent, Allocation


def agent(aid="a", item="book", hp=100.0, fullness=7.0, floor=1):
    return Agent(id=aid, name=aid.upper(), policy_name="test", item=item, hp=hp, fullness=fullness, floor=floor)


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules()

    def test_attack_minus_guard(self):
        a, b = agent("a"), agent("b")
        hit = R.resolve_hit(a, Allocation(attack=10), b, Allocation(guard=5), self.rules)
        self.assertEqual(hit.damage_points, 5)
        self.assertEqual(hit.damage_hp, 50)

    def test_full_guard_blocks(self):
        hit = R.resolve_hit(agent("a"), Allocation(attack=4), agent("b"), Allocation(guard=6), self.rules)
        self.assertEqual(hit.damage_points, 0)

    def test_no_attack_no_hit(self):
        self.assertIsNone(R.resolve_hit(agent("a"), Allocation(eat=10), agent("b"), Allocation(), self.rules))

    def test_item_bonuses_only_when_used(self):
        knife = agent("a", item="knife")
        self.assertEqual(R.attack_strength(Allocation(attack=3), knife.item, self.rules), 5)
        self.assertEqual(R.attack_strength(Allocation(attack=0), knife.item, self.rules), 0)
        self.assertEqual(R.guard_strength(Allocation(guard=1), "helmet", self.rules), 3)
        self.assertEqual(R.guard_strength(Allocation(guard=1), "frying_pan", self.rules), 2)
        self.assertEqual(R.attack_strength(Allocation(attack=1), "frying_pan", self.rules), 2)


class EatingTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules()

    def test_hungry_eats_faster(self):
        self.assertGreater(R.bite_size(5, 1.0, "book", self.rules), R.bite_size(5, 8.0, "book", self.rules))

    def test_more_points_eat_faster(self):
        self.assertGreater(R.bite_size(8, 3.0, "book", self.rules), R.bite_size(2, 3.0, "book", self.rules))

    def test_spoon(self):
        self.assertAlmostEqual(R.bite_size(5, 0, "big_spoon", self.rules), R.bite_size(5, 0, "book", self.rules) * 1.3)

    def test_limit_respected(self):
        a = agent("a", fullness=0)
        taken, left = R.eat_from_pool(50.0, [(a, 10, 2.0)], self.rules)
        self.assertAlmostEqual(taken["a"], 2.0)
        self.assertAlmostEqual(left, 48.0)

    def test_pool_split_when_short(self):
        a, b = agent("a", fullness=0), agent("b", fullness=0)
        taken, left = R.eat_from_pool(0.5, [(a, 10, None), (b, 5, None)], self.rules)
        self.assertAlmostEqual(left, 0.0)
        self.assertAlmostEqual(taken["a"] / taken["b"], 2.0, places=5)

    def test_overflow_heals(self):
        a = agent("a", hp=50, fullness=10)
        R.apply_food(a, 2.0, self.rules)
        self.assertEqual(a.fullness, 10)
        self.assertAlmostEqual(a.hp, 56)


class NightTests(unittest.TestCase):
    def setUp(self):
        self.rules = Rules()

    def test_starvation_takes_about_a_month(self):
        a = agent("a", fullness=0)
        days = 0
        while a.hp > 0:
            R.night(a, 0, self.rules)
            days += 1
        self.assertTrue(30 <= days <= 40, days)

    def test_rest_reduces_decay(self):
        a, b = agent("a", fullness=5), agent("b", fullness=5)
        R.night(a, 0, self.rules)
        R.night(b, 10, self.rules)
        self.assertGreater(b.fullness, a.fullness)

    def test_regen_when_fed(self):
        a = agent("a", hp=50, fullness=10)
        R.night(a, 0, self.rules)
        self.assertEqual(a.hp, 52)

    def test_body_food(self):
        self.assertEqual(R.body_food(10, self.rules), 10)
        self.assertEqual(R.body_food(1, self.rules), 3)


class AllocationTests(unittest.TestCase):
    def test_valid(self):
        al, ok = R.clamp_allocation({"eat": 5, "guard": 3}, 10, 2)
        self.assertTrue(ok)
        self.assertEqual((al.eat, al.guard, al.talk), (5, 3, 2))

    def test_over_budget_scaled(self):
        al, ok = R.clamp_allocation({"eat": 10, "attack": 10}, 10, 0)
        self.assertFalse(ok)
        self.assertEqual(al.eat + al.attack, 10)

    def test_garbage(self):
        al, ok = R.clamp_allocation({"eat": "lots", "guard": -3}, 6, 0)
        self.assertFalse(ok)
        self.assertEqual((al.eat, al.guard), (0, 0))


class ParseTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_and_think(self):
        self.assertEqual(extract_json('<think>hmm {"x":0}</think>\n```json\n{"a": 2}\n```'), {"a": 2})

    def test_embedded(self):
        self.assertEqual(extract_json('Sure! Here you go: {"msg": "a } b", "n": 3} thanks'), {"msg": "a } b", "n": 3})

    def test_none(self):
        self.assertIsNone(extract_json("no json here"))


class Scripted(Policy):
    """Plays a fixed script: talk points, and an evening allocation per day."""

    def __init__(self, talk=0, evening=None, item="book"):
        self.talk, self.ev, self.item = talk, evening or {}, item

    async def choose_item(self, obs):
        return {"item": self.item}

    async def morning(self, obs):
        return {"talk_points": self.talk}

    async def speak(self, obs, mine, theirs):
        return f"{obs.me_name}-{len(obs.conversation)}"

    async def evening(self, obs):
        return self.ev or {"eat": obs.hearts - obs.talk_points}


def two_agent_game(pa: Policy, pb: Policy, **rules):
    r = Rules(floors=1, agents_per_floor=2, days=rules.pop("days", 1), **rules)
    agents = [Agent(id="a", name="A", policy_name="t"), Agent(id="b", name="B", policy_name="t")]
    return Game(r, agents, {"a": pa, "b": pb}, seed=0)


class EngineTests(unittest.TestCase):
    def test_talk_turn_order(self):
        game = two_agent_game(Scripted(talk=3), Scripted(talk=2))
        replay = asyncio.run(game.run())
        talk = replay["days"][0]["talk"]["1"]
        speakers = [m["from"] for m in talk]
        self.assertEqual(len(speakers), 5)
        self.assertEqual(speakers.count("a"), 3)
        self.assertEqual(speakers.count("b"), 2)
        # Alternates while both have turns left; the leftover turns go last.
        for i in range(1, 4):
            self.assertNotEqual(speakers[i], speakers[i - 1])

    def test_one_sided_talk(self):
        game = two_agent_game(Scripted(talk=3), Scripted(talk=0))
        talk = asyncio.run(game.run())["days"][0]["talk"]["1"]
        self.assertEqual([m["from"] for m in talk], ["a", "a", "a"])

    def test_ambush_kills_and_leaves_body(self):
        game = two_agent_game(Scripted(evening={"attack": 10}), Scripted(evening={"eat": 10}), days=2)
        replay = asyncio.run(game.run())
        d1 = replay["days"][0]
        self.assertEqual(d1["deaths"][0]["id"], "b")
        self.assertEqual(d1["deaths"][0]["cause"], "killed")
        # Attacker didn't eat today (all points on attack): body has 10 food minus one night of rot.
        self.assertEqual(d1["bodies"][0]["food"], 8.0)

    def test_mutual_destruction(self):
        game = two_agent_game(Scripted(evening={"attack": 10}), Scripted(evening={"attack": 10}))
        replay = asyncio.run(game.run())
        self.assertEqual(sorted(d["id"] for d in replay["days"][0]["deaths"]), ["a", "b"])

    def test_megaphone_needs_both(self):
        game = two_agent_game(Scripted(talk=0, item="megaphone"), Scripted(talk=0))
        self.assertNotIn("1", asyncio.run(game.run())["days"][0]["talk"])
        game = two_agent_game(Scripted(talk=0, item="megaphone"), Scripted(talk=0, item="megaphone"))
        talk = asyncio.run(game.run())["days"][0]["talk"]["1"]
        self.assertEqual(len(talk), 4)

    def test_first_aid_heals_wounds_only(self):
        rules = Rules()
        a = agent("a", item="first_aid_kit", hp=40)
        a.kit_charges = 3
        a.wounds = 30  # 30 HP from attacks, the other 30 missing HP came from hunger
        healed, why = R.use_first_aid(a, rules)
        self.assertEqual((healed, why, a.hp, a.wounds, a.kit_charges), (20, "healed", 60, 10, 2))
        healed, why = R.use_first_aid(a, rules)
        self.assertEqual((healed, a.hp, a.wounds, a.kit_charges), (10, 70, 0, 1))
        healed, why = R.use_first_aid(a, rules)   # only hunger damage left: nothing happens, bandage kept
        self.assertEqual((healed, why, a.hp, a.kit_charges), (0, "no_wounds", 70, 1))

    def test_first_aid_runs_out(self):
        a = agent("a", item="first_aid_kit", hp=10)
        a.kit_charges, a.wounds = 1, 90
        R.use_first_aid(a, Rules())
        self.assertEqual(R.use_first_aid(a, Rules())[1], "empty")

    def test_kit_in_game_after_ambush(self):
        game = two_agent_game(Scripted(evening={"attack": 5}),
                              Scripted(item="first_aid_kit", evening={"eat": 5, "use_kit": True}), days=1)
        replay = asyncio.run(game.run())
        kit = replay["days"][0]["kit"][0]
        self.assertEqual((kit["result"], kit["healed_hearts"], kit["left"]), ("healed", 2.0, 2))
        # 50 HP of wounds, 20 healed by the bandage, then 2 HP of normal overnight healing.
        self.assertEqual(game.agents["b"].wounds, 28)

    def test_no_passive_kit_healing(self):
        a = agent("a", item="first_aid_kit", hp=50, fullness=0)
        R.night(a, 0, Rules())
        self.assertLess(a.hp, 50)

    def test_shuffle_removes_bodies(self):
        r = Rules(floors=2, agents_per_floor=2, days=3, shuffle_every=2)
        agents = [Agent(id=x, name=x.upper(), policy_name="t") for x in "abcd"]
        killer = Scripted(evening={"attack": 10})
        pols = {"a": killer, "b": Scripted(), "c": Scripted(), "d": Scripted()}
        game = Game(r, agents, pols, seed=1)
        replay = asyncio.run(game.run())
        self.assertIn("shuffle", replay["days"][1])
        # No body carried into day 3 except ones created on day 3.
        day3_bodies = replay["days"][2]["bodies"]
        day3_deaths = {d["id"] for d in replay["days"][2]["deaths"]}
        self.assertTrue(all(b["victim"] in day3_deaths for b in day3_bodies))

    def test_shuffle_pairs_survivors_on_any_floors(self):
        r = Rules(floors=8, agents_per_floor=2)
        for n_alive in (16, 9, 6, 3):
            for seed in range(20):
                agents = [Agent(id=f"x{i}", name=f"X{i}", policy_name="t") for i in range(16)]
                for a in agents[n_alive:]:
                    a.alive = False
                game = Game(r, agents, {a.id: Scripted() for a in agents}, seed=seed)
                game.assign_floors()
                counts: dict[int, int] = {}
                for a in game.alive():
                    counts[a.floor] = counts.get(a.floor, 0) + 1
                self.assertTrue(all(c <= 2 for c in counts.values()))
                self.assertEqual(sum(1 for c in counts.values() if c == 1), n_alive % 2)
                self.assertEqual(len(counts), (n_alive + 1) // 2)

    def test_shuffle_can_leave_gaps(self):
        r = Rules(floors=8, agents_per_floor=2)
        seen_gap = False
        for seed in range(30):
            agents = [Agent(id=f"x{i}", name=f"X{i}", policy_name="t") for i in range(6)]
            game = Game(r, agents, {a.id: Scripted() for a in agents}, seed=seed)
            game.assign_floors()
            used = sorted({a.floor for a in agents})
            if used != [1, 2, 3]:
                seen_gap = True
        self.assertTrue(seen_gap)

    def test_deterministic_with_seed(self):
        cfg = {"seed": 4, "rules": {"days": 20}, "agents": [{"policy": "bot:random", "count": 8}, {"policy": "bot:killer", "count": 8}]}
        r1 = asyncio.run(build_game(cfg).run())["summary"]["agents"]
        r2 = asyncio.run(build_game(cfg).run())["summary"]["agents"]
        self.assertEqual(r1, r2)

    def test_mock_llm_end_to_end(self):
        cfg = {"seed": 2, "rules": {"days": 12}, "agents": [{"policy": "llm", "model": "mock:x", "count": 8}, {"policy": "bot:fair", "count": 8}]}
        replay = asyncio.run(build_game(cfg).run())
        self.assertEqual(replay["summary"]["days_played"], 12)
        llm = [a for a in replay["summary"]["agents"] if a["policy"] == "llm"]
        self.assertTrue(all(a["invalid_outputs"] == 0 for a in llm))


if __name__ == "__main__":
    unittest.main()


class HeartsAndPromptTests(unittest.TestCase):
    def test_hearts_round_down_keep_last(self):
        self.assertEqual(agent(hp=100).hearts(), 10)
        self.assertEqual(agent(hp=99.5).hearts(), 9)
        self.assertEqual(agent(hp=10).hearts(), 1)
        self.assertEqual(agent(hp=3).hearts(), 1)
        self.assertEqual(agent(hp=0).hearts(), 0)

    def test_game_length_hidden(self):
        from descent.llm import prompts
        game = build_game({"seed": 1, "rules": {"days": 77}, "agents": [{"policy": "bot:fair", "count": 2}]})
        a = next(iter(game.agents.values()))
        game.day = 5
        obs = game.observe(a, {x.id: 10 for x in game.agents.values()})
        text = prompts.system_prompt(a.name, game.rules) + prompts.evening_prompt(obs) + prompts.morning_prompt(obs)
        self.assertNotIn("77", text)
        self.assertIn("DAY 5.", text)
