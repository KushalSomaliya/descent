import asyncio
import unittest

from descent.llm.clients import MockClient
from descent.runner import run_config


class BrokenMock(MockClient):
    def _complete_sync(self, system, user, temperature):
        raise RuntimeError("quota exhausted")


class GuardTests(unittest.TestCase):
    def test_stops_on_repeated_errors(self):
        import descent.llm.clients as C

        C_orig = dict(C.PRESETS)
        try:
            # route the "mock" provider to a client that always fails
            orig_make = C.make_client

            def broken_make(spec, *a, **k):
                c = orig_make(spec, *a, **k)
                return BrokenMock(**{f: getattr(c, f) for f in c.__dataclass_fields__})

            import descent.llm.policy as P
            P.make_client = broken_make
            cfg = {"seed": 1, "rules": {"days": 20}, "agents": [{"policy": "llm", "model": "mock:x", "count": 4},
                                                              {"policy": "bot:fair", "count": 4}]}
            replay = asyncio.run(run_config(cfg, out_dir=None, verbose=False, check_models=False))
            self.assertIn("in a row", replay["summary"]["stopped"])
            self.assertLess(replay["summary"]["days_played"], 20)
        finally:
            P.make_client = orig_make
            C.PRESETS.clear(); C.PRESETS.update(C_orig)

    def test_budget_cap(self):
        cfg = {"seed": 1, "rules": {"days": 30}, "agents": [{"policy": "llm", "model": "mock:x", "count": 4, "max_cost_usd": 0.0001}],
               "providers": {"mock": {"price": [1.0, 1.0]}}}
        replay = asyncio.run(run_config(cfg, out_dir=None, verbose=False, check_models=False))
        self.assertIn("cap", replay["summary"]["stopped"])
        self.assertEqual(replay["summary"]["days_played"], 1)


if __name__ == "__main__":
    unittest.main()
