import os
import unittest

from descent.llm.clients import AnthropicClient, OpenAICompatClient, make_client
from descent.runner import build_game


class CaptureMixin:
    def _post(self, url, headers, body):
        self.sent = (url, headers, body)
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": '{"ok": true}'}], "usage": {"input_tokens": 10, "output_tokens": 5}}
        return {"choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 40}}


class Anth(CaptureMixin, AnthropicClient):
    pass


class OAI(CaptureMixin, OpenAICompatClient):
    pass


class ClientTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("GEMINI_API_KEY", "test")

    def test_anthropic_effort(self):
        base = make_client("anthropic:claude-sonnet-5-5", effort="low")
        c = Anth(**{k: getattr(base, k) for k in base.__dataclass_fields__})
        c._complete_sync("sys", "user", None)
        url, headers, body = c.sent
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual(body["output_config"], {"effort": "low"})
        self.assertEqual(body["model"], "claude-sonnet-5-5")

    def test_gemini_preset(self):
        base = make_client("gemini:gemini-3.8-flash", effort="low")
        self.assertIn("generativelanguage.googleapis.com", base.base_url)
        self.assertEqual(base.max_tokens, 8192)
        c = OAI(**{k: getattr(base, k) for k in base.__dataclass_fields__})
        text, usage = c._complete_sync("sys", "user", None)
        url, headers, body = c.sent
        self.assertTrue(url.endswith("/openai/chat/completions"))
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertTrue(headers["authorization"].startswith("Bearer "))
        self.assertEqual(usage.output_tokens, 30)  # 5 visible + 25 hidden thinking

    def test_effort_groups_get_separate_labels(self):
        cfg = {"agents": [
            {"policy": "llm", "model": "anthropic:claude-sonnet-5-5", "effort": "low", "count": 2},
            {"policy": "llm", "model": "anthropic:claude-sonnet-5-5", "count": 2},
        ]}
        game = build_game(cfg)
        self.assertEqual(sorted(game.llm_clients), ["anthropic:claude-sonnet-5-5", "anthropic:claude-sonnet-5-5 (low)"])
        models = sorted({a.model for a in game.agents.values()})
        self.assertEqual(models, ["anthropic:claude-sonnet-5-5", "anthropic:claude-sonnet-5-5 (low)"])

    def test_three_models_config_builds(self):
        import json
        from pathlib import Path
        cfg = json.loads((Path(__file__).parent.parent / "configs" / "three_models.json").read_text())
        game = build_game(cfg)
        self.assertEqual(len(game.agents), 18)
        self.assertEqual(game.rules.floors, 9)


if __name__ == "__main__":
    unittest.main()
