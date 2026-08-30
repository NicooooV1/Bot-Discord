"""Tests du cog `vpn` — fonctions PURES, sans réseau ni Discord.

POURQUOI CES TESTS : le tableau #vpn est la seule vue de Nico sur la bascule R820 ↔ MikroTik.
Un mode mal lu ferait croire à un secours actif (ou l'inverse) ; un handshake absent rendu
comme « déconnecté » violerait la règle « le bot réel dans ses mots » ; un delta de
compteurs négatif après restart de l'interface afficherait un débit absurde ; un nom de
pair venu du réseau pourrait injecter backticks/mentions. Les cas ci-dessous verrouillent
ces points, plus l'edge des événements (rien au 1er relevé, une ligne par transition).
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import vpn as v  # noqa: E402

# Relevé réel `vpn-status` du 2026-08-30 (clés tronquées par le script lui-même).
SAMPLE = {
    "ts": 1788098696, "host": "R820", "mode": "primaire",
    "wg-vpn": {"present": True, "label": "VPN nomade (Pierre + PC Nico)", "listen_port": 39671,
               "pubkey_short": "vkm3cMpW…", "peers": [
        {"name": "pierre-mac", "pubkey_short": "/9Xab0Ua…", "endpoint": None, "allowed_ips": "10.3.99.3/32",
         "tunnel_ip": "10.3.99.3", "handshake_age_s": None, "connected": False, "rx_bytes": 0, "tx_bytes": 0, "keepalive_s": None},
        {"name": "nico-pc2", "pubkey_short": "rcwDfVsU…", "endpoint": "192.168.1.254:57455", "allowed_ips": "10.3.99.4/32",
         "tunnel_ip": "10.3.99.4", "handshake_age_s": 67, "connected": True, "rx_bytes": 8788, "tx_bytes": 77212,
         "keepalive_s": None, "ping": {"loss_pct": 0, "min_ms": 168.2, "avg_ms": 374.5, "max_ms": 578.2}},
        {"name": "nico-pc3", "pubkey_short": "TUBWLWs/…", "endpoint": None, "allowed_ips": "10.3.99.5/32",
         "tunnel_ip": "10.3.99.5", "handshake_age_s": 7200, "connected": False, "rx_bytes": 10, "tx_bytes": 20, "keepalive_s": None},
    ]},
    "wg-avy": {"present": True, "label": "Site-à-site Aveyron", "listen_port": 39672, "pubkey_short": "vkm3cMpW…", "peers": [
        {"name": "Hub Aveyron", "pubkey_short": "n6ymo7fa…", "endpoint": "82.66.8.226:13231",
         "allowed_ips": "10.99.0.0/24,10.0.10.0/24", "tunnel_ip": "10.99.0.1", "handshake_age_s": 65, "connected": True,
         "rx_bytes": 1531533928, "tx_bytes": 16163079852, "keepalive_s": 25,
         "ping": {"loss_pct": 0, "min_ms": 30.8, "avg_ms": 32.1, "max_ms": 33.5}}]},
    "mikrotik": {"reachable": True, "netwatch_status": "up", "dstnat_enabled": True, "dstnat_packets": 2,
                 "route_vpn_via": "R820", "hub_peer_enabled": False, "uptime": "3d3h", "wan_ip": "5.48.38.244",
                 "wg0_input_packets": 2, "wg0_peers": [
                     {"disabled": True, "name": "Hub Aveyron", "endpoint": "82.66.8.226:13231", "rx": "124", "tx": "180", "last_handshake": "16s"},
                     {"disabled": False, "name": "pierre-mac", "allowed": "10.3.99.3/32", "rx": "25.4KiB", "tx": "68.8KiB", "last_handshake": "4m18s"},
                     {"disabled": False, "name": "nico-win", "allowed": "10.3.99.2/32", "rx": "0", "tx": "0"}]},
    "aveyron": {"hub_10.99.0.1": {"loss_pct": 0, "min_ms": 30.7, "avg_ms": 31.0, "max_ms": 31.4},
                "pve_10.0.10.10": {"loss_pct": 0, "min_ms": 31.0, "avg_ms": 31.5, "max_ms": 32.0}},
    "public_dns_nicov1": "5.48.38.244", "dns_matches_wan": True, "duration_s": 4.7,
}


def _secours(d):
    d = copy.deepcopy(d)
    d["mode"] = "secours"
    d["mikrotik"].update({"dstnat_enabled": False, "route_vpn_via": "wg0", "hub_peer_enabled": True, "netwatch_status": "down"})
    return d


class TestParse(unittest.TestCase):
    def test_vide_ou_illisible_donne_none(self):
        for raw in ("", "   ", "{not json", "[]", '{"foo": 1}'):
            self.assertIsNone(v.parse_status(raw), raw)

    def test_json_valide(self):
        import json
        self.assertEqual(v.parse_status(json.dumps(SAMPLE))["mode"], "primaire")


class TestEtatPair(unittest.TestCase):
    def test_jamais_vu_n_est_pas_deconnecte(self):
        emoji, txt = v.peer_state(SAMPLE["wg-vpn"]["peers"][0])
        self.assertEqual(emoji, "⚪")
        self.assertIn("jamais vu", txt)
        self.assertNotIn("déconnect", txt)

    def test_handshake_recent_connecte(self):
        emoji, txt = v.peer_state(SAMPLE["wg-vpn"]["peers"][1])
        self.assertEqual(emoji, "🟢")
        self.assertIn("67 s", txt)

    def test_handshake_ancien_dit_dernier_handshake(self):
        emoji, txt = v.peer_state(SAMPLE["wg-vpn"]["peers"][2])
        self.assertEqual(emoji, "🟠")
        self.assertIn("dernier handshake il y a", txt)

    def test_ligne_pair_sans_handshake_ne_montre_ni_ping_ni_debit(self):
        line = v.peer_line("wg-vpn", SAMPLE["wg-vpn"]["peers"][0], None)
        self.assertNotIn("ping", line)
        self.assertNotIn("débit", line)

    def test_ligne_pair_connecte_complete(self):
        line = v.peer_line("wg-vpn", SAMPLE["wg-vpn"]["peers"][1], (1000.0, 2000.0))
        for s in ("nico-pc2", "10.3.99.4", "192.168.1.254:57455", "ping 374 ms", "min 168", "max 578", "débit"):
            self.assertIn(s, line)

    def test_nom_du_reseau_assaini(self):
        p = dict(SAMPLE["wg-vpn"]["peers"][1], name="x`@everyone\nrm")
        line = v.peer_line("wg-vpn", p, None)
        self.assertNotIn("`@everyone", line)
        self.assertNotIn("\nrm", line)

    def test_mt_recent(self):
        self.assertTrue(v._mt_recent("16s"))
        self.assertTrue(v._mt_recent("2m59s"))
        self.assertFalse(v._mt_recent("4m18s"))
        self.assertFalse(v._mt_recent("22h44m"))


class TestDebits(unittest.TestCase):
    def test_pas_de_precedent(self):
        self.assertEqual(v.rates(None, SAMPLE), {})

    def test_delta_positif(self):
        cur = copy.deepcopy(SAMPLE)
        cur["ts"] += 60
        cur["wg-vpn"]["peers"][1]["rx_bytes"] += 6000
        cur["wg-vpn"]["peers"][1]["tx_bytes"] += 12000
        r = v.rates(SAMPLE, cur)["wg-vpn/nico-pc2"]
        self.assertAlmostEqual(r[0], 100.0)
        self.assertAlmostEqual(r[1], 200.0)

    def test_compteurs_reinitialises_donne_none(self):
        cur = copy.deepcopy(SAMPLE)
        cur["ts"] += 60
        cur["wg-avy"]["peers"][0]["rx_bytes"] = 5  # restart de wg-avy
        self.assertIsNone(v.rates(SAMPLE, cur)["wg-avy/Hub Aveyron"])

    def test_dt_nul_ou_negatif(self):
        self.assertEqual(v.rates(SAMPLE, SAMPLE), {})


class TestAlertes(unittest.TestCase):
    def test_nominal_aucune_alerte(self):
        self.assertEqual(set(v.alerts_from(SAMPLE).values()), {None})

    def test_secours(self):
        self.assertEqual(v.alerts_from(_secours(SAMPLE))["vpn_failover"], "warn")

    def test_hub_sans_handshake_est_critique(self):
        d = copy.deepcopy(SAMPLE)
        d["wg-avy"]["peers"][0].update({"connected": False, "handshake_age_s": 900})
        self.assertEqual(v.alerts_from(d)["vpn_avy_down"], "crit")

    def test_mikrotik_injoignable_et_dns(self):
        d = copy.deepcopy(SAMPLE)
        d["mikrotik"] = {"reachable": False, "error": "SSH MikroTik KO"}
        d["dns_matches_wan"] = False
        a = v.alerts_from(d)
        self.assertEqual(a["vpn_mikrotik"], "warn")
        self.assertEqual(a["vpn_dns_wan"], "warn")

    def test_dns_inconnu_n_alerte_pas(self):
        d = copy.deepcopy(SAMPLE)
        d["dns_matches_wan"] = None
        self.assertIsNone(v.alerts_from(d)["vpn_dns_wan"])


class TestEvenements(unittest.TestCase):
    def test_premier_releve_silencieux(self):
        self.assertEqual(v.events(None, v.snapshot(SAMPLE)), [])
        self.assertEqual(v.events({}, v.snapshot(SAMPLE)), [])

    def test_stable_silencieux(self):
        s = v.snapshot(SAMPLE)
        self.assertEqual(v.events(s, s), [])

    def test_connexion_et_fin(self):
        before = v.snapshot(SAMPLE)
        after_d = copy.deepcopy(SAMPLE)
        after_d["wg-vpn"]["peers"][0].update({"connected": True, "handshake_age_s": 3, "endpoint": "1.2.3.4:5"})
        after_d["wg-vpn"]["peers"][1].update({"connected": False, "handshake_age_s": 400})
        ev = v.events(before, v.snapshot(after_d))
        self.assertEqual(len(ev), 2)
        self.assertTrue(any("pierre-mac" in e and "connecté" in e and "1.2.3.4:5" in e for e in ev))
        self.assertTrue(any("nico-pc2" in e and "plus de handshake" in e for e in ev))

    def test_bascule_et_retour(self):
        p, s = v.snapshot(SAMPLE), v.snapshot(_secours(SAMPLE))
        self.assertTrue(any("relais" in e for e in v.events(p, s)))
        self.assertTrue(any("primaire" in e for e in v.events(s, p)))

    def test_changement_endpoint(self):
        after = copy.deepcopy(SAMPLE)
        after["wg-vpn"]["peers"][1]["endpoint"] = "9.9.9.9:1"
        ev = v.events(v.snapshot(SAMPLE), v.snapshot(after))
        self.assertEqual(len(ev), 1)
        self.assertIn("changé d'endpoint", ev[0])

    def test_nouveau_pair(self):
        after = copy.deepcopy(SAMPLE)
        after["wg-vpn"]["peers"].append(dict(SAMPLE["wg-vpn"]["peers"][0], name="invite"))
        ev = v.events(v.snapshot(SAMPLE), v.snapshot(after))
        self.assertTrue(any("Nouveau pair" in e and "invite" in e for e in ev))


class TestEmbed(unittest.TestCase):
    def test_primaire(self):
        emb = v.build_embed(SAMPLE, {}, poll_s=60)
        self.assertIn("PRIMAIRE", emb.title)
        self.assertEqual(emb.color.value, v.fmt.GREEN)
        names = [f.name for f in emb.fields]
        self.assertEqual(len(names), 3)
        self.assertTrue(any("wg-vpn" in n and "1/3" in n for n in names))
        self.assertTrue(any("wg-avy" in n and "1/1" in n for n in names))
        self.assertTrue(any("MikroTik" in n for n in names))
        self.assertIn("5.48.38.244", emb.description)

    def test_secours_orange(self):
        emb = v.build_embed(_secours(SAMPLE), {}, poll_s=60)
        self.assertIn("SECOURS", emb.title)
        self.assertEqual(emb.color.value, v.fmt.ORANGE)
        self.assertIn("désactivé", emb.fields[2].value)

    def test_lecture_impossible_garde_le_dernier_etat(self):
        emb = v.build_embed(SAMPLE, {}, poll_s=60, stale=(300, "SSH hyperviseur : timeout"))
        self.assertIn("Lecture impossible", emb.description)
        self.assertIn("dernier relevé réussi", emb.description)

    def test_mikrotik_non_lu(self):
        d = copy.deepcopy(SAMPLE)
        d["mikrotik"] = {"reachable": False, "error": "SSH MikroTik KO"}
        d["mode"] = "inconnu"
        emb = v.build_embed(d, {}, poll_s=60)
        self.assertIn("INCONNU", emb.title)
        self.assertIn("non lu", emb.fields[2].value)

    def test_tailles_discord(self):
        emb = v.build_embed(SAMPLE, {}, poll_s=60)
        for f in emb.fields:
            self.assertLessEqual(len(f.value), 1024)
            self.assertLessEqual(len(f.name), 256)
        self.assertLessEqual(len(emb.description), 4096)

    def test_aucune_cle_complete(self):
        emb = v.build_embed(SAMPLE, {}, poll_s=60)
        blob = emb.title + emb.description + "".join(f.name + f.value for f in emb.fields)
        self.assertNotRegex(blob, r"[A-Za-z0-9+/]{40,}=")


if __name__ == "__main__":
    unittest.main()
