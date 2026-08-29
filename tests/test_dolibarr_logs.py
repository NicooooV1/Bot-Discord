"""Tests du cog `dolibarr_logs` — fonctions PURES (sans Loki ni Discord).

POURQUOI : ce flux recopie des lignes de journal dans Discord. Ce qui doit être
verrouillé : le filtre (ce qui est publié / tu), le rendu (préfixes retirés, backticks
neutralisés, longueur bornée, stack traces regroupées), le découpage ≤ 2000 caractères,
le bilan d'afflux et la détection d'incident (ce qui part dans #alertes).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import dolibarr_logs as dl  # noqa: E402

APP = "2026-08-29 07:14:12 NOTICE  127.0.0.1          8284     33 --- Access to GET /index.php - action="
APP_IP = "2026-08-29 07:20:01 ERR     10.3.20.116        9001     33 DoliDBMysqli::query SQL Error `x`"
APACHE = ("[Sat Aug 29 07:10:02.123456 2026] [php:error] [pid 8151] [client 10.3.20.116:51234] "
          "PHP Fatal error:  Uncaught Error: boom in /var/www/x.php:3")
CRON_BAD = ("***** cron_run_jobs.php (24.0.0) pid=8151 - userlogin=Administrateur - x *****\n"
            "TZ server = Europe/Paris\n"
            "***** cron_run_jobs.php end - x *****\n"
            "PHP Fatal error:  Uncaught Error: mysqli object is already closed in "
            "/var/www/dolibarr/htdocs/core/db/mysqli.class.php:361\n"
            "Stack trace:\n#0 a\n#1 b\n#2 c\n#3 {main}\n  thrown in x on line 361")
ACCESS_404 = '10.3.20.116 - - [29/Aug/2026:07:14:12 +0000] "GET /nope HTTP/1.1" 404 8614 "-" "curl"'


class TestPrefixes(unittest.TestCase):
    def test_app(self):
        lvl, ip, msg = dl.strip_app_prefix(APP)
        self.assertEqual((lvl, ip), ("NOTICE", "127.0.0.1"))
        self.assertTrue(msg.startswith("--- Access to GET /index.php"))

    def test_app_inconnue(self):
        self.assertEqual(dl.strip_app_prefix("n'importe quoi"), (None, None, "n'importe quoi"))

    def test_apache(self):
        s = dl.strip_apache_prefix(APACHE)
        self.assertTrue(s.startswith("php:error · client 10.3.20.116 · PHP Fatal"))
        self.assertNotIn("pid 8151", s)


class TestFiltre(unittest.TestCase):
    def test_app_tout_sauf_end_access(self):
        self.assertTrue(dl.wanted("dolibarr", "notice", APP))
        self.assertFalse(dl.wanted("dolibarr", "info", "2026-… INFO 127.0.0.1 1 1 --- End access to /index.php"))

    def test_app_info_mecanique_tue(self):
        base = "2026-08-29 07:14:12 INFO    127.0.0.1          8284     33 "
        self.assertFalse(dl.wanted("dolibarr", "info", base + "box_lastlogin::showBox"))
        self.assertFalse(dl.wanted("dolibarr", "info", base + "DolGraph::draw_chart this->type=pie"))
        self.assertTrue(dl.wanted("dolibarr", "info", base + "This is a new started user session."))
        # le même texte en WARNING passe toujours (le filtre ne vise que INFO)
        self.assertTrue(dl.wanted("dolibarr", "warning", base + "box_lastlogin::showBox"))

    def test_cron_seulement_en_echec(self):
        self.assertTrue(dl.wanted("dolibarr-cron", "err", CRON_BAD))
        self.assertFalse(dl.wanted("dolibarr-cron", "info", "***** cron_run_jobs.php (24.0.0) …"))

    def test_access_seulement_400_plus(self):
        self.assertTrue(dl.wanted("apache-access", "warning", ACCESS_404))
        self.assertTrue(dl.wanted("apache-access", "err", ACCESS_404))
        self.assertFalse(dl.wanted("apache-access", "info", ACCESS_404))

    def test_apache_error_tout(self):
        self.assertTrue(dl.wanted("apache-error", "notice", "[x] [core:notice] …"))


class TestRendu(unittest.TestCase):
    def test_app_ip_locale_masquee_et_backticks(self):
        out = dl.render(1756451652.0, "dolibarr", "notice", APP)
        self.assertIn("**app**", out)
        self.assertNotIn("127.0.0.1", out)
        out2 = dl.render(1756451652.0, "dolibarr", "err", APP_IP)
        self.assertIn("[10.3.20.116]", out2)
        self.assertNotIn("`x`", out2)          # backticks neutralisés
        self.assertIn("'x'", out2)

    def test_cron_fatal_en_tete_puis_bloc(self):
        out = dl.render(1756451652.0, "dolibarr-cron", "err", CRON_BAD)
        first = out.split("\n", 1)[0]
        self.assertIn("PHP Fatal error", first)
        self.assertIn("```", out)
        self.assertIn("⤷ +", out)               # plus de TRACE_LINES lignes de suite

    def test_session_id_masque(self):
        out = dl.render(0, "dolibarr", "info", "2026-08-29 07:14:12 INFO    10.3.20.116 1 1 "
                        "This is a new started user session. _SESSION['dol_login']=X Session id=lunqhanla8u15")
        self.assertNotIn("lunqhanla8u15", out)
        self.assertIn("Session id=…", out)

    def test_longueur_bornee(self):
        out = dl.render(0, "apache-error", "err", "[x] [php:error] " + "A" * 2000)
        self.assertLess(len(out), 500)


class TestLots(unittest.TestCase):
    def test_chunk_ne_coupe_pas_un_bloc(self):
        blocks = ["a" * 1000, "b" * 1000, "c" * 10]
        out = dl.chunk(blocks, limit=1900)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(len(c) <= 1900 for c in out))
        self.assertTrue(out[1].startswith("b"))

    def test_digest(self):
        e = [(0, "dolibarr", "info", "x")] * 3 + [(0, "apache-error", "err", "y")]
        d = dl.overflow_digest(e)
        self.assertIn("4 ligne(s)", d)
        self.assertIn("err 1", d)
        self.assertIn("info 3", d)


class TestIncidents(unittest.TestCase):
    def test_rien(self):
        self.assertIsNone(dl.incidents([(0, "dolibarr", "info", APP),
                                        (0, "apache-access", "warning", ACCESS_404)]))

    def test_cron_et_fatal(self):
        r = dl.incidents([(0, "dolibarr-cron", "err", CRON_BAD),
                          (0, "dolibarr-cron", "err", CRON_BAD),
                          (0, "apache-error", "err", APACHE),
                          (0, "apache-access", "err", ACCESS_404.replace(" 404 ", " 500 "))])
        self.assertIsNotNone(r)
        title, desc = r
        self.assertIn("cron Dolibarr en échec ×2", title)
        self.assertIn("PHP Fatal error (apache)", title)
        self.assertIn("HTTP 5xx", title)
        self.assertEqual(desc.count("•"), 4)


if __name__ == "__main__":
    unittest.main()
