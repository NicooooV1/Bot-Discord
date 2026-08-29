"""Tests du cog `grafana_alerts` — fonctions PURES, sans réseau ni Discord.

POURQUOI : ce relais remplace le webhook Grafana→Discord. Une erreur de parsing
ferait taire une alerte réelle ; une erreur de diff re-posterait tout à chaque
redémarrage (spam) ou ne posterait jamais le ✅ ; un texte venu de Grafana pourrait
injecter mentions/blocs de code. Les cas ci-dessous verrouillent ces trois points.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import grafana_alerts as ga  # noqa: E402
from bot.core.config import Config  # noqa: E402


def _alert(fp, name, sev="warning", state="active", host="pve", desc="d", starts="2026-08-29T13:40:00.123456789Z"):
    return {"fingerprint": fp, "status": {"state": state},
            "labels": {"alertname": name, "severity": sev, "host": host, "grafana_folder": "PVE Alertes"},
            "annotations": {"description": desc, "summary": "s"},
            "generatorURL": "http://g/x", "startsAt": starts}


PAYLOAD = [
    _alert("aaa", "Invite sans aucune sauvegarde"),
    _alert("bbb", "RAID BBU degradee", sev="critical"),
    _alert("ccc", "Silencieuse", state="suppressed"),        # silence Grafana → ignorée
    {"fingerprint": "", "labels": {"alertname": "sans fp"}},   # ignorée
    "n'importe quoi",                                          # ignorée sans planter
]


class TestParse(unittest.TestCase):
    def test_actives_seulement(self):
        cur = ga.parse_alerts(PAYLOAD)
        self.assertEqual(set(cur), {"aaa", "bbb"})
        self.assertEqual(cur["bbb"]["severity"], "critical")
        self.assertEqual(cur["aaa"]["folder"], "PVE Alertes")

    def test_payload_non_liste(self):
        self.assertEqual(ga.parse_alerts(None), {})
        self.assertEqual(ga.parse_alerts({"error": "x"}), {})


class TestDiff(unittest.TestCase):
    def test_nouvelles_et_resolues(self):
        cur = ga.parse_alerts(PAYLOAD)
        new, res = ga.diff(["aaa", "zzz"], cur)
        self.assertEqual([a["fp"] for a in new], ["bbb"])       # aaa déjà connue
        self.assertEqual(res, ["zzz"])                            # zzz disparue

    def test_critical_en_premier(self):
        cur = ga.parse_alerts(PAYLOAD)
        new, _ = ga.diff([], cur)
        self.assertEqual([a["fp"] for a in new], ["bbb", "aaa"])

    def test_redemarrage_ne_reposte_pas(self):
        cur = ga.parse_alerts(PAYLOAD)
        new, res = ga.diff(list(cur), cur)
        self.assertEqual((new, res), ([], []))

    def test_silence_vaut_resolution(self):
        cur = ga.parse_alerts(PAYLOAD)
        _, res = ga.diff(["ccc"], cur)
        self.assertEqual(res, ["ccc"])


class TestEmbeds(unittest.TestCase):
    def test_nettoyage_mentions_et_code(self):
        a = ga.parse_alerts([_alert("x", "@everyone ```rm```", desc="@here " + "x" * 2000)])["x"]
        emb = ga.firing_embed(a)
        self.assertNotIn("@everyone", emb.title)
        self.assertNotIn("```", emb.title)
        self.assertNotIn("@here", emb.description)
        self.assertLessEqual(len(emb.description), ga.DESC_MAX)

    def test_couleur_et_champs(self):
        cur = ga.parse_alerts(PAYLOAD)
        self.assertEqual(ga.firing_embed(cur["bbb"]).color.value, ga.fmt.RED)
        self.assertEqual(ga.firing_embed(cur["aaa"]).color.value, ga.fmt.ORANGE)
        names = [f.name for f in ga.firing_embed(cur["aaa"]).fields]
        self.assertIn("Hôte", names)
        self.assertIn("Depuis", names)

    def test_since_nanosecondes(self):
        self.assertNotEqual(ga._since("2026-08-29T13:40:00.123456789Z"), "")
        self.assertEqual(ga._since("n'importe quoi"), "")

    def test_resolu(self):
        emb = ga.resolved_embed({"name": "X", "host": "pve"})
        self.assertTrue(emb.title.startswith("✅ Résolu — X"))
        self.assertIn("`pve`", emb.description)


class TestConfig(unittest.TestCase):
    def _cfg(self, **kw):
        base = {"DISCORD_TOKEN": "t", "ALERT_CHANNEL_ID": "111"}
        base.update(kw)
        return Config(env=base)

    def test_defauts_et_repli_salon(self):
        c = self._cfg()
        self.assertFalse(c.grafana_enabled)
        self.assertEqual(c.grafana_alert_channel_id, 111)      # repli sur ALERT_CHANNEL_ID
        self.assertEqual(c.grafana_poll_seconds, 60)

    def test_actif_avec_token(self):
        c = self._cfg(GRAFANA_TOKEN="glsa_x", GRAFANA_ALERT_CHANNEL_ID="222",
                      GRAFANA_POLL_SECONDS="5", GRAFANA_URL="http://g:3000/")
        self.assertTrue(c.grafana_enabled)
        self.assertEqual(c.grafana_alert_channel_id, 222)
        self.assertEqual(c.grafana_poll_seconds, 30)             # plancher 30 s
        self.assertEqual(c.grafana_url, "http://g:3000")


if __name__ == "__main__":
    unittest.main()
