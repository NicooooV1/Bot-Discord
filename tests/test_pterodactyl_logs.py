"""Tests du cog `pterodactyl_logs` — fonctions PURES (sans Loki ni Discord).

Verrouillé : le niveau réel des lignes Wings (préfixe texte, pas la priorité journald),
le filtre (INFO Wings utiles vs bruit mécanique, Laravel sans debug, HTTP ≥ 400), le
rendu (préfixes retirés, contexte JSON réduit à l'exception, secrets masqués, backticks
neutralisés), le découpage ≤ 2000 et la détection d'incident.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import pterodactyl_logs as pl  # noqa: E402

W_INFO_NOISE = " INFO: [Aug 29 16:34:38.734] starting resource polling for container container_id=b62 environment=docker"
W_INFO_POWER = " INFO: [Aug 29 16:34:22.945] acquired exclusive lock on power actions, processing event... action=start lock_id=ba41 server=b6211642"
W_WARN = " WARN: [Aug 29 16:30:52.811] could not pull image image=ghcr.io/x server=487905b6"
W_ERROR = "ERROR: [Aug 29 16:30:52.811] could create base environment for server... server=487905b6"
W_TOKEN = " INFO: [Aug 29 16:30:52.818] sftp server listening for connections listen=0.0.0.0:2022 public_key=ssh-ed25519 AAAA token=abc"
LARAVEL_ERR = ('[2026-08-29 14:28:39] production.ERROR: Array to string conversion {"exception":"[object] '
               '(ErrorException(code: 0): Array to string conversion at /var/www/pterodactyl/vendor/x.php:163)\n'
               '[stacktrace]\n#0 a\n#1 b\n#2 c\n#3 d\n#4 e\n#5 f\n#6 g\n#7 {main}\n"} ')
LARAVEL_WARN = '[2026-08-29 14:28:39] production.WARNING: Something odd {"user":1}'
LARAVEL_DEBUG = '[2026-08-29 14:28:39] production.DEBUG: verbose'
APACHE = ("[Sat Aug 29 07:10:02.123456 2026] [php:error] [pid 8151] [client 10.3.20.116:51234] "
          "PHP Fatal error:  Uncaught Error: boom in /var/www/x.php:3")
ACCESS_404 = '10.3.20.116 - - [29/Aug/2026:07:14:12 +0000] "GET /nope HTTP/1.1" 404 8614 "-" "curl"'
ACCESS_500 = '10.3.20.116 - - [29/Aug/2026:07:14:12 +0000] "GET /api HTTP/1.1" 500 12 "-" "curl"'


class TestWings(unittest.TestCase):
    def test_niveau_texte_prime(self):
        self.assertEqual(pl.effective_level("wings.service", "info", W_ERROR), "err")
        self.assertEqual(pl.effective_level("wings.service", "info", W_WARN), "warning")
        self.assertEqual(pl.effective_level("wings.service", "info", W_INFO_NOISE), "info")
        self.assertEqual(pl.effective_level("laravel", "err", "x"), "err")

    def test_parse(self):
        lvl, msg = pl.parse_wings(W_INFO_POWER)
        self.assertEqual(lvl, "info")
        self.assertTrue(msg.startswith("acquired exclusive lock"))
        self.assertEqual(pl.parse_wings("bizarre"), ("info", "bizarre"))

    def test_filtre_info(self):
        self.assertFalse(pl.wanted("wings.service", "info", W_INFO_NOISE))
        self.assertTrue(pl.wanted("wings.service", "info", W_INFO_POWER))
        self.assertTrue(pl.wanted("wings.service", "warning", W_WARN))
        self.assertTrue(pl.wanted("wings.service", "err", W_ERROR))

    def test_secret_masque(self):
        out = pl.render(1_700_000_000, "wings.service", "info", W_TOKEN)
        self.assertNotIn("token=abc", out)
        self.assertIn("token=…", out)


class TestLaravel(unittest.TestCase):
    def test_parse_exception(self):
        lvl, head = pl.parse_laravel(LARAVEL_ERR.split("\n")[0])
        self.assertEqual(lvl, "err")
        self.assertTrue(head.startswith("Array to string conversion — ErrorException: Array to string"))
        self.assertNotIn('{"exception"', head)

    def test_parse_contexte_sans_exception(self):
        lvl, head = pl.parse_laravel(LARAVEL_WARN)
        self.assertEqual((lvl, head), ("warning", "Something odd"))

    def test_inconnue(self):
        self.assertEqual(pl.parse_laravel("n'importe quoi"), (None, "n'importe quoi"))

    def test_filtre(self):
        self.assertFalse(pl.wanted("laravel", "debug", LARAVEL_DEBUG))
        self.assertTrue(pl.wanted("laravel", "info", "x"))
        self.assertTrue(pl.wanted("laravel", "err", LARAVEL_ERR))

    def test_render_trace_bornee(self):
        out = pl.render(1_700_000_000, "laravel", "err", LARAVEL_ERR)
        self.assertIn("**panel**", out)
        self.assertIn("```", out)
        self.assertIn("⤷ +", out)               # 8 lignes de trace, TRACE_LINES = 6
        self.assertNotIn("[stacktrace]", out)


class TestApache(unittest.TestCase):
    def test_prefix(self):
        self.assertTrue(pl.strip_apache_prefix(APACHE).startswith("php:error · client 10.3.20.116 · PHP Fatal"))

    def test_filtre_access(self):
        self.assertTrue(pl.wanted("apache-access", "warning", ACCESS_404))
        self.assertTrue(pl.wanted("apache-access", "err", ACCESS_500))
        self.assertFalse(pl.wanted("apache-access", "info", ACCESS_404.replace(" 404 ", " 200 ")))
        self.assertTrue(pl.wanted("apache-error", "info", "x"))


class TestRenduEtDecoupage(unittest.TestCase):
    def test_backticks_et_longueur(self):
        s = pl._safe("a`b" * 300)
        self.assertNotIn("`", s)
        self.assertLessEqual(len(s), pl.LINE_MAX)

    def test_chunk(self):
        blocks = ["x" * 900] * 5
        out = pl.chunk(blocks, limit=1900)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(len(m) <= 1900 for m in out))

    def test_overflow(self):
        d = pl.overflow_digest([(0, "laravel", "err", "a"), (0, "wings.service", "info", "b")])
        self.assertIn("2 ligne(s)", d)
        self.assertIn("err 1", d)


class TestIncidents(unittest.TestCase):
    def test_rien(self):
        self.assertIsNone(pl.incidents([(0, "wings.service", "info", W_INFO_POWER),
                                        (0, "apache-access", "warning", ACCESS_404),
                                        (0, "laravel", "warning", LARAVEL_WARN)]))

    def test_incidents(self):
        title, desc = pl.incidents([(0, "laravel", "err", LARAVEL_ERR),
                                    (0, "wings.service", "err", W_ERROR),
                                    (0, "wings.service", "err", W_ERROR),
                                    (0, "apache-error", "err", APACHE),
                                    (0, "apache-access", "err", ACCESS_500)])
        self.assertTrue(title.startswith("🔴 Pterodactyl :"))
        self.assertIn("Wings : ERROR ×2", title)
        self.assertIn("erreur Laravel (err)", title)
        self.assertIn("PHP Fatal", title)
        self.assertIn("HTTP 5xx", title)
        self.assertIn("could create base environment", desc)


if __name__ == "__main__":
    unittest.main()
