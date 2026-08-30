"""Tests du cog `seedclean` — fonctions PURES, sans réseau ni Discord.

POURQUOI CES TESTS : deux curseurs séparés (urgent / bilan) — un mélange ferait perdre le
bilan du jour dès qu'une purge est relayée ; le bilan ne doit partir qu'une fois par jour,
après l'heure, et seulement s'il y a des retraits ; les textes viennent de noms de torrents
(réseau) et ne doivent pas porter de mention.
"""
import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import seedclean as sc  # noqa: E402

EVENTS = [
    {"ts": 100.0, "kind": "tracker", "text": "`open.stealth.si` retiré de « A » (mort 3 relevés consécutifs)"},
    {"ts": 200.0, "kind": "purge", "text": "🗑️ 1 ancienne(s) version(s) supprimée(s), 8.4 Gio libérés :\n• A (8.4 Gio)"},
    {"ts": 300.0, "kind": "tracker", "text": "`tracker.torrent.eu.org` retiré de « B » (mort 3 relevés consécutifs)"},
    {"ts": 400.0, "kind": "warn", "text": "plus de 15 candidats à la purge, arrêt par sécurité — à vérifier (montage tombé ?)"},
]
RAW = json.dumps({"dead_seen": {}, "events": EVENTS})


class TestParse(unittest.TestCase):
    def test_illisible_donne_none(self):
        for raw in ("", "  ", "{pas json", "[]", '{"autre": 1}', '{"events": "x"}'):
            self.assertIsNone(sc.parse_state(raw), raw)

    def test_valide(self):
        ev = sc.parse_state(RAW)
        self.assertEqual(len(ev), 4)
        self.assertEqual(ev[0]["kind"], "tracker")

    def test_entrees_malformees_ignorees(self):
        raw = json.dumps({"events": [{"ts": "pas-un-nombre", "text": "x"}, {"text": "sans ts"},
                                     {"ts": 1.0, "kind": "tracker", "text": "ok"}, "n'importe quoi"]})
        ev = sc.parse_state(raw)
        self.assertEqual([e["text"] for e in ev], ["ok"])


class TestCurseurs(unittest.TestCase):
    def test_urgent_ne_prend_que_purge_et_warn(self):
        u = sc.select_urgent(EVENTS, 0)
        self.assertEqual([e["ts"] for e in u], [200.0, 400.0])

    def test_urgent_respecte_le_curseur(self):
        self.assertEqual([e["ts"] for e in sc.select_urgent(EVENTS, 200.0)], [400.0])
        self.assertEqual(sc.select_urgent(EVENTS, 400.0), [])

    def test_trackers_curseur_independant(self):
        # une purge relayée (curseur urgent avancé) ne consomme PAS les trackers du jour
        self.assertEqual([e["ts"] for e in sc.select_trackers(EVENTS, 0)], [100.0, 300.0])
        self.assertEqual([e["ts"] for e in sc.select_trackers(EVENTS, 100.0)], [300.0])


class TestBilan(unittest.TestCase):
    def test_pas_de_bilan_sans_retrait(self):
        now = dt.datetime(2026, 8, 30, 10, 0)
        self.assertFalse(sc.digest_due(now, None, 9, []))

    def test_pas_avant_l_heure(self):
        now = dt.datetime(2026, 8, 30, 8, 59)
        self.assertFalse(sc.digest_due(now, None, 9, EVENTS[:1]))

    def test_une_fois_par_jour(self):
        now = dt.datetime(2026, 8, 30, 10, 0)
        self.assertTrue(sc.digest_due(now, None, 9, EVENTS[:1]))
        self.assertTrue(sc.digest_due(now, "2026-08-29", 9, EVENTS[:1]))
        self.assertFalse(sc.digest_due(now, "2026-08-30", 9, EVENTS[:1]))

    def test_embed_bilan(self):
        emb = sc.digest_embed(sc.select_trackers(EVENTS, 0))
        self.assertIn("2 tracker(s)", emb.title)
        self.assertIn("open.stealth.si", emb.description)
        self.assertIn("grâce", emb.footer.text)

    def test_embed_urgent(self):
        e = sc.urgent_embed(EVENTS[1])
        self.assertIn("purgées", e.title)
        w = sc.urgent_embed(EVENTS[3])
        self.assertIn("arrêt de sécurité", w.title)

    def test_safe_neutralise_mentions(self):
        s = sc._safe("torrent @everyone " + "x" * 2000)
        self.assertNotIn("@everyone", s)
        self.assertLessEqual(len(s), 900)


if __name__ == "__main__":
    unittest.main()
