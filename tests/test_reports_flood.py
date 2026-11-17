"""Section anti-flood du rapport quotidien (17/11)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs.reports import _flood_lines  # noqa: E402


class TestFlood(unittest.TestCase):
    def test_rien(self):
        self.assertEqual(_flood_lines([]), "✅ aucune coupure")

    def test_coupure_et_rappel(self):
        rows = [
            (1794900000, {}, "flood-guard: COUPURE vault.nicov1.fr : 1600 puis 1700 req/min "
                             "(externes ; top IP 1.2.3.4×3000). Rallumer : rm /etc/caddy/coupure/vault.nicov1.fr  (CT116)"),
            (1794903600, {}, "flood-guard: TOUJOURS COUPÉ vault.nicov1.fr depuis 17/11 03:00. Rallumer : rm x"),
            (1794903600, {}, "flood-guard: TOUJOURS COUPÉ doli.nicov1.fr depuis 15/11 02:00. Rallumer : rm y"),
        ]
        out = _flood_lines(rows)
        self.assertIn("🔴", out)
        self.assertIn("COUPURE vault.nicov1.fr", out)
        self.assertIn("⛔ TOUJOURS COUPÉ doli.nicov1.fr", out)
        self.assertNotIn("TOUJOURS COUPÉ vault", out)   # déjà dit par la ligne de coupure


if __name__ == "__main__":
    unittest.main()
