"""Tests du cog SSO (2026-08-20) : parsing des lignes sqlite d'Authelia et
regroupement des événements en messages d'alerte.

    cd /opt/discord-bot && ./venv/bin/python -m unittest tests.test_sso -v

POURQUOI CES TESTS-LÀ. La veille `sso_watch` transforme des lignes brutes (sqlite
par SSH) en messages #alertes et en curseur persistant : une régression du parseur
ferait soit rater un bannissement (silence sur un événement de sécurité), soit
rejouer tout l'historique dans le salon. Les deux invariants sont ancrés ici, sans
réseau ni SSH : les fonctions testées sont pures.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs.sso import _SEP, _classify_ip, parse_auth_rows, summarize_batch  # noqa: E402


def _row(rid, ts, user, ok, banned, atype, ip):
    return _SEP.join([str(rid), ts, user, ok, banned, atype, ip])


class TestParseAuthRows(unittest.TestCase):
    def test_ligne_complete(self):
        raw = _row(12, "2026-08-19 01:08:05.994604607+02:00", "nico",
                   "1", "0", "TOTP", "192.168.1.254")
        rows = parse_auth_rows(raw)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["id"], 12)
        self.assertEqual(r["time"], "2026-08-19 01:08")   # tronqué à la minute
        self.assertEqual(r["user"], "nico")
        self.assertTrue(r["ok"])
        self.assertFalse(r["banned"])
        self.assertEqual(r["type"], "TOTP")

    def test_ligne_bancale_ignoree_sans_planter(self):
        # une ligne tronquée (coupure SSH en plein transfert) ne doit ni planter
        # ni fabriquer un événement
        raw = "\n".join([
            _row(1, "2026-08-19 01:00:00+02:00", "nico", "1", "0", "1FA", "1.2.3.4"),
            "garbage-sans-separateur",
            _SEP.join(["pas-un-id", "t", "u", "1", "0", "1FA", "ip"]),
            "",
        ])
        rows = parse_auth_rows(raw)
        self.assertEqual([r["id"] for r in rows], [1])

    def test_vide(self):
        self.assertEqual(parse_auth_rows(""), [])
        self.assertEqual(parse_auth_rows(None), [])


class TestSummarizeBatch(unittest.TestCase):
    def test_ban_individuel_echecs_agreges(self):
        rows = parse_auth_rows("\n".join([
            _row(1, "2026-08-20 10:00:00+02:00", "nico", "0", "0", "1FA", "8.8.8.8"),
            _row(2, "2026-08-20 10:00:30+02:00", "nico", "0", "0", "1FA", "8.8.8.8"),
            _row(3, "2026-08-20 10:01:00+02:00", "nico", "0", "1", "1FA", "8.8.8.8"),
        ]))
        msgs = summarize_batch(rows)
        levels = [m[0] for m in msgs]
        self.assertIn("ban", levels)
        self.assertEqual(levels.count("fail"), 1)     # 2 échecs -> UN message agrégé
        fail = next(m for m in msgs if m[0] == "fail")
        self.assertIn("**2** échec(s)", fail[2])

    def test_succes_discret(self):
        rows = parse_auth_rows(
            _row(4, "2026-08-20 11:00:00+02:00", "nico", "1", "0", "TOTP",
                 "192.168.1.254"))
        msgs = summarize_batch(rows)
        self.assertEqual([m[0] for m in msgs], ["ok"])
        self.assertIn("hairpin", msgs[0][2])          # IP maison annotée, pas « externe »

    def test_lot_vide(self):
        self.assertEqual(summarize_batch([]), [])


class TestClassifyIp(unittest.TestCase):
    def test_annotations(self):
        self.assertIn("hairpin", _classify_ip("192.168.1.254"))
        self.assertIn("interne", _classify_ip("172.18.0.1"))
        self.assertIn("externe", _classify_ip("203.0.113.7"))


if __name__ == "__main__":
    unittest.main()
