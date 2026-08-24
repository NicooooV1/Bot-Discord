"""Invariants des journaux médias (2026-08-24) : le format des lignes Jellyseerr et
les helpers d'enrichissement Jellyfin (timecode) sont purs — on les fige ici."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs.seerr_activity import _fmt_media, _fmt_request  # noqa: E402
from bot.cogs.jellyfin_activity import _hms, _RX_IP  # noqa: E402


class TestFormatSeerr(unittest.TestCase):
    def test_film(self):
        self.assertEqual(_fmt_media("movie", "Zootopie 2", "2025"),
                         "le film **Zootopie 2** (2025)")

    def test_serie_saisons(self):
        self.assertEqual(_fmt_media("tv", "GoT", None, [1, 2]),
                         "la série **GoT** — saisons 1, 2")

    def test_demande_auto(self):
        line = _fmt_request("2026-08-24 15:52", "admin", "movie", "Zootopie 2",
                            "2025", auto=True)
        self.assertIn("**admin** a demandé le film **Zootopie 2** (2025)", line)
        self.assertIn("(auto-approuvée)", line)

    def test_titre_inconnu(self):
        # titre non résolu (Seerr muet) : la ligne sort quand même, sans lever
        self.assertIn("**?**", _fmt_media("movie", None))


class TestTimecode(unittest.TestCase):
    def test_hms(self):
        self.assertEqual(_hms(4930), "1:22:10")
        self.assertEqual(_hms(130), "2:10")
        self.assertEqual(_hms(0), "0:00")

    def test_rx_ip(self):
        m = _RX_IP.search("Adresse IP : 10.3.20.120")
        self.assertEqual(m.group(1), "10.3.20.120")


if __name__ == "__main__":
    unittest.main()
