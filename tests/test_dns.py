"""Tests du cog `dns` (AdGuard Home) — les fonctions PURES, sans réseau ni Discord.

POURQUOI CES TESTS : le flux DNS est la première source « haut débit » du bot (des
centaines de lignes par heure recopiées dans Discord). Une erreur de curseur rejouerait
tout le journal ; une mauvaise lecture de `reason` ferait passer un blocage pour une
résolution normale (ou l'inverse) ; un domaine venu du réseau pourrait injecter des
backticks/mentions. Les cas ci-dessous verrouillent exactement ces trois points, plus
le découpage en lots ≤ 2000 caractères et l'edge des alertes de pic.
"""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import dns  # noqa: E402


def _row(t, client="10.3.20.120", name="example.com", reason="NotFilteredNotFound",
         rule="", cached=False, elapsed="0.5", qtype="A"):
    return {"time": t, "client": client, "question": {"name": name, "type": qtype},
            "reason": reason, "rule": rule, "cached": cached, "elapsedMs": elapsed,
            "upstream": "8.8.8.8:53", "status": "NOERROR"}


PAYLOAD = {"data": [
    # AdGuard renvoie du plus récent au plus ancien, horodatages en NANOsecondes
    _row("2026-08-28T17:59:31.022823354Z", name="ads.example.net",
         reason="FilteredBlackList", rule="||ads.example.net^"),
    _row("2026-08-28T17:59:30.5Z", cached=True),
    _row("2026-08-28T17:59:30Z"),
    {"time": "n'importe quoi", "client": "x"},           # ignorée en le disant
], "oldest": "2026-08-28T17:59:24Z"}


class TestParse(unittest.TestCase):
    def test_tri_ancien_vers_recent_et_nanosecondes(self):
        rows = dns.parse_querylog(PAYLOAD)
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["name"] for r in rows],
                         ["example.com", "example.com", "ads.example.net"])
        self.assertEqual(rows[-1]["time"].microsecond, 22823)
        self.assertIsNotNone(rows[0]["time"].tzinfo)

    def test_bloque_seulement_pour_les_raisons_de_blocage(self):
        rows = dns.parse_querylog(PAYLOAD)
        self.assertEqual([r["blocked"] for r in rows], [False, False, True])
        # un rewrite n'est PAS un blocage
        rw = dns.parse_querylog({"data": [_row("2026-08-28T17:59:30Z", reason="Rewrite")]})
        self.assertFalse(rw[0]["blocked"])

    def test_curseur(self):
        rows = dns.parse_querylog(PAYLOAD)
        cur = dns._parse_ts("2026-08-28T17:59:30.5Z")
        fresh = dns.newer_than(rows, cur)
        self.assertEqual([r["name"] for r in fresh], ["ads.example.net"])
        self.assertEqual(len(dns.newer_than(rows, None)), 3)

    def test_elapsed_illisible(self):
        rows = dns.parse_querylog({"data": [_row("2026-08-28T17:59:30Z", elapsed="abc")]})
        self.assertEqual(rows[0]["elapsed_ms"], 0.0)


class TestRendu(unittest.TestCase):
    def test_feed_compacte_les_repetitions(self):
        rows = dns.parse_querylog({"data": [
            _row("2026-08-28T17:59:32Z"), _row("2026-08-28T17:59:31Z"),
            _row("2026-08-28T17:59:30Z"), _row("2026-08-28T17:59:29Z", name="autre.org")]})
        lines = dns.feed_lines(rows)
        self.assertEqual(len(lines), 2)
        self.assertIn("×3", lines[1])
        self.assertIn("autre.org", lines[0])

    def test_blocked_summary_regroupe_et_donne_la_regle(self):
        rows = dns.parse_querylog({"data": [
            _row("2026-08-28T17:59:31Z", name="ads.example.net", reason="FilteredBlackList",
                 rule="||ads.example.net^"),
            _row("2026-08-28T17:59:30Z", name="ads.example.net", reason="FilteredBlackList",
                 rule="||ads.example.net^"),
            _row("2026-08-28T17:59:29Z")]})
        lines = dns.blocked_summary(rows)
        self.assertEqual(len(lines), 1)
        self.assertIn("×2", lines[0])
        self.assertIn("||ads.example.net^", lines[0])
        self.assertIn("10.3.20.120", lines[0])

    def test_domaine_hostile_neutralise(self):
        rows = dns.parse_querylog({"data": [
            _row("2026-08-28T17:59:31Z", name="evil`@everyone`.com",
                 reason="FilteredBlackList", rule="`x`")]})
        for ln in dns.blocked_summary(rows) + dns.feed_lines(rows):
            self.assertNotIn("`x`", ln)
            self.assertNotIn("`@everyone`", ln)

    def test_chunks_sous_2000(self):
        lines = [f"ligne {i} " + "x" * 120 for i in range(60)]
        chunks = dns.chunk_lines(lines)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 2000)
            self.assertTrue(c.startswith("```") and c.endswith("```"))
        self.assertEqual(sum(c.count("ligne ") for c in chunks), 60)

    def test_chunk_ligne_trop_longue_tronquee(self):
        chunks = dns.chunk_lines(["y" * 5000], fence=False)
        self.assertEqual(len(chunks), 1)
        self.assertLessEqual(len(chunks[0]), 1900)


class TestPics(unittest.TestCase):
    def test_nominal(self):
        rows = dns.parse_querylog({"data": [_row("2026-08-28T17:59:30Z")]})
        levels = {k: lvl for k, lvl, _, _ in dns.detect_spikes(rows, 30, 600)}
        self.assertEqual(levels, {"dns_client_blocked_spike": None, "dns_volume_spike": None})

    def test_rafale_bloquee_un_client(self):
        data = [_row(f"2026-08-28T17:59:{i:02d}Z", name=f"t{i}.tracker.io",
                     reason="FilteredBlackList") for i in range(35)]
        rows = dns.parse_querylog({"data": data})
        out = {k: (lvl, desc) for k, lvl, _, desc in dns.detect_spikes(rows, 30, 600)}
        self.assertEqual(out["dns_client_blocked_spike"][0], "warn")
        self.assertIn("probable", out["dns_client_blocked_spike"][1].lower())
        self.assertIn("35", out["dns_client_blocked_spike"][1])
        self.assertIsNone(out["dns_volume_spike"][0])

    def test_volume(self):
        data = [_row(f"2026-08-28T17:{i // 60:02d}:{i % 60:02d}Z") for i in range(60)]
        rows = dns.parse_querylog({"data": data})
        out = {k: lvl for k, lvl, _, _ in dns.detect_spikes(rows, 30, 50)}
        self.assertEqual(out["dns_volume_spike"], "warn")


class TestCompteursEtRegles(unittest.TestCase):
    def test_compteurs_heure(self):
        hc = dns.HourCounters()
        hc.add(dns.parse_querylog(PAYLOAD))
        self.assertEqual((hc.total, hc.blocked), (3, 1))
        self.assertEqual(hc.blocked_domains["ads.example.net"], 1)
        self.assertEqual(hc.clients["10.3.20.120"], 3)

    def test_regles_utilisateur(self):
        rules, ch = dns.user_rules_toggle([], "bad.com", True)
        self.assertEqual((rules, ch), (["||bad.com^"], True))
        rules, ch = dns.user_rules_toggle(rules, "bad.com", True)
        self.assertFalse(ch)
        rules, ch = dns.user_rules_toggle(rules, "bad.com", False)
        self.assertEqual((rules, ch), ([], True))
        rules, ch = dns.user_rules_toggle(["  "], "bad.com", False)
        self.assertFalse(ch)

    def test_validation_domaine(self):
        self.assertEqual(dns.valid_domain(" Tracker.Example.COM. "), "tracker.example.com")
        for bad in ("", "nodots", "a b.com", "x/y.com", "`.com", "-.com", "a." + "b" * 64 + ".c"):
            self.assertIsNone(dns.valid_domain(bad), bad)


if __name__ == "__main__":
    unittest.main()
