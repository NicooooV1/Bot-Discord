"""Rappels « toujours en cours » espacés (17/11) : une erreur qui dure ne doit plus
reposter toutes les 10 min pendant des heures (panne NAS : 17 h de rappels)."""
import os
import sys
import unittest
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.logstream import LiveLogStream  # noqa: E402


class TestBackoff(unittest.TestCase):
    def test_rappels_espaces(self):
        s = LiveLogStream.__new__(LiveLogStream)
        s.cooldown = 600
        s.recent = OrderedDict()
        s._bump = lambda *a, **k: None
        g = {"host": "pbs", "sev": 3, "count": 1, "msg": "EHOSTDOWN"}
        posts = []
        for minute in range(0, 17 * 60):          # même erreur chaque minute pendant 17 h
            out = s._cooldown_pass({"k": dict(g)}, minute * 60.0)
            posts += [minute for _ in out]
        # ancien comportement : ~102 messages ; avec backoff 10/20/40/80/160/240 min : une dizaine
        self.assertLess(len(posts), 15, posts)
        self.assertEqual(posts[0], 0)
        gaps = [b - a for a, b in zip(posts, posts[1:])]
        self.assertEqual(gaps[:4], [10, 20, 40, 80])
        self.assertLessEqual(max(gaps), 240)


if __name__ == "__main__":
    unittest.main()
