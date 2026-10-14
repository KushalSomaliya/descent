import os
import tempfile
import unittest
from pathlib import Path

from descent.env import load_dotenv


class DotenvTests(unittest.TestCase):
    def test_load_and_no_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text('# comment\nDESCENT_T1=abc\nexport DESCENT_T2="q u o"\nDESCENT_T3=x # note\nDESCENT_T4=keep\nbad line\n')
            os.environ["DESCENT_T4"] = "shell"
            try:
                loaded = load_dotenv(p)
                self.assertEqual(os.environ["DESCENT_T1"], "abc")
                self.assertEqual(os.environ["DESCENT_T2"], "q u o")
                self.assertEqual(os.environ["DESCENT_T3"], "x")
                self.assertEqual(os.environ["DESCENT_T4"], "shell")
                self.assertNotIn("DESCENT_T4", loaded)
            finally:
                for k in ("DESCENT_T1", "DESCENT_T2", "DESCENT_T3", "DESCENT_T4"):
                    os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
