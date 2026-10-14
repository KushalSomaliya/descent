import io
import os
import time
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from descent.llm.clients import make_client, retry_hint_seconds


def http_error(code, headers=None, body=""):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("http://x", code, "err", msg, io.BytesIO(body.encode()))


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return self.payload.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OK = '{"content": [{"type": "text", "text": "{\\"ok\\": true}"}], "usage": {"input_tokens": 1, "output_tokens": 1}}'


class RetryTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")

    def test_hints(self):
        self.assertEqual(retry_hint_seconds({"retry-after": "7"}), 7)
        self.assertAlmostEqual(retry_hint_seconds({"retry-after-ms": "1500"}), 1.5)
        self.assertEqual(retry_hint_seconds({"x-ratelimit-reset-requests": "1m30s"}), 90)
        self.assertEqual(retry_hint_seconds({}, '{"error": {"details": [{"retryDelay": "12s"}]}}'), 12)
        self.assertIsNone(retry_hint_seconds({}, "nothing"))

    def test_429_is_waited_out_then_succeeds(self):
        client = make_client("anthropic:claude-haiku-5-5")
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise http_error(429, {"retry-after": "0.01"})
            return FakeResp(OK)

        with mock.patch("urllib.request.urlopen", fake_urlopen), mock.patch("descent.llm.clients.random.uniform", lambda a, b: 0), \
             mock.patch("descent.llm.clients.log", lambda m: None):
            t0 = time.time()
            data = client._post("https://api.anthropic.com/v1/messages", {}, {})
        self.assertEqual(calls["n"], 4)
        self.assertIn("content", data)
        self.assertLess(time.time() - t0, 5)

    def test_gives_up_after_max_wait(self):
        client = make_client("anthropic:claude-haiku-5-5")
        client.rate_limit_max_wait = 0.2
        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: (_ for _ in ()).throw(http_error(429, {"retry-after": "5"}))), \
             mock.patch("descent.llm.clients.log", lambda m: None):
            with self.assertRaises(Exception) as ctx:
                client._post("https://x", {}, {})
        self.assertIn("rate-limited", str(ctx.exception))

    def test_bad_request_not_retried(self):
        client = make_client("anthropic:claude-haiku-5-5")
        calls = {"n": 0}

        def fake(req, timeout=None):
            calls["n"] += 1
            raise http_error(400, body='{"error": "bad"}')

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(Exception):
                client._post("https://x", {}, {})
        self.assertEqual(calls["n"], 1)


if __name__ == "__main__":
    unittest.main()
