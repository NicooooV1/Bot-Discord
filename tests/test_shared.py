"""Tests des MODULES PARTAGÉS de la campagne de simplification (2026-08-11).

    cd /opt/discord-bot && ./venv/bin/python -m unittest discover -s tests -v

POURQUOI CES TESTS-LÀ. `tests/test_edmine.py` ancre les invariants du bot ; ce
fichier-ci ancre ceux des quatre modules que TOUS les cogs appellent désormais
(`core.channels`, `core.http`, `core.format`). Une régression ici ne casse pas un
écran : elle se propage à une dizaine de cogs d'un coup.

Chaque test correspond à un défaut RÉELLEMENT vécu, cité dans les docstrings des
modules :

  - `channels.norm` : quatre copies divergentes comparaient « Lock » et « 🔒 Lock R820 »,
    d'où des catégories introuvables — donc des salons créés À LA RACINE, donc publics.
  - `channels.resolve` : une clé inventée (`"super"` au lieu de `"supervision"`) ne
    trouvait jamais rien, EN SILENCE. La clé inconnue doit maintenant crier.
  - `http.request_json` : `None` signifie « appel en échec » et RIEN D'AUTRE. Le
    confondre avec une liste vide rendait `/langues` rassurant alors que Radarr était
    injoignable. Et un `urlopen` sans timeout immobilise un worker du pool partagé.
  - `format.outcome_text` : « lost » n'est PAS un échec (le suivi s'arrête, la tâche
    PVE continue) — l'annoncer comme tel ferait croire à une sauvegarde ratée.

AUCUN ACCÈS RÉSEAU EXTERNE : les tests HTTP parlent à un `http.server` de la stdlib,
lancé dans un thread sur `127.0.0.1` avec le port 0 (attribué par l'OS).
"""
import http.server
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.core import channels  # noqa: E402
from bot.core import format as fmt  # noqa: E402
from bot.core import http as bhttp  # noqa: E402


# --------------------------------------------------------------------------- outils
def faux_categorie(cid, name):
    """`CategoryChannel` minimale : `channels._by_id` exige le VRAI type (isinstance),
    et rien d'autre du modèle Discord n'est lu ici."""
    c = discord.CategoryChannel.__new__(discord.CategoryChannel)
    c.id = cid
    c.name = name
    return c


class FauxEtat:
    """Le sous-ensemble de `core.state.State` que lit `channels.resolve`."""

    def __init__(self, data=None):
        self._d = dict(data or {})

    def get(self, key, default=None):
        return self._d.get(key, default)


class FauxGuild:
    def __init__(self, categories=()):
        self.categories = list(categories)
        self._by_id = {c.id: c for c in self.categories}

    def get_channel(self, cid):
        return self._by_id.get(cid)


class FauxCfg:
    server_key = "R820"


class FauxBot:
    def __init__(self, categories_publiees=None):
        self.state = FauxEtat({"prov": {"categories": dict(categories_publiees or {})}})
        self.cfg = FauxCfg()


# ------------------------------------------------------------------- channels.norm
class TestNormalisationDesNoms(unittest.TestCase):
    """« Lock » et « 🔒 Lock R820 » ne se ressemblent que comparés en alphanumérique
    minuscule. Sans cette normalisation, un emoji suffit à faire créer un DOUBLON de
    catégorie — ou à ne pas retrouver la sienne."""

    def test_emoji_espaces_et_casse_sont_ignores(self):
        self.assertEqual(channels.norm("🔒 Lock R820"), "lockr820")
        self.assertEqual(channels.norm("Lock R820"), channels.norm("🔒  lock  r820"))
        self.assertEqual(channels.norm("📊 Supervision R820"), "supervisionr820")

    def test_lock_seul_ne_vaut_PAS_lock_r820(self):
        """Le repli historique « Lock » est un nom DIFFÉRENT : c'est justement pour cela
        qu'il faut passer par `resolve()` et son id publié, pas par un nom en dur."""
        self.assertNotEqual(channels.norm("Lock"), channels.norm("🔒 Lock R820"))

    def test_idempotence(self):
        for brut in ("🔒 Lock R820", "Supervision", "avy-pve", ""):
            self.assertEqual(channels.norm(channels.norm(brut)), channels.norm(brut))

    def test_valeurs_vides_ne_levent_pas(self):
        self.assertEqual(channels.norm(None), "")
        self.assertEqual(channels.norm(""), "")
        self.assertEqual(channels.norm("   "), "")
        self.assertEqual(channels.norm("🔒"), "")
        self.assertEqual(channels.norm(106), "106")


# ---------------------------------------------------------------- channels.resolve
class TestResolutionDesCategories(unittest.TestCase):
    """`resolve()` est la SOURCE UNIQUE DE VÉRITÉ. Ses trois issues doivent rester
    exactement celles-ci : id publié > nom normalisé > None (et l'appelant renonce)."""

    def test_id_publie_prioritaire_sur_le_nom(self):
        bonne = faux_categorie(11, "🔒 Lock R820")
        homonyme = faux_categorie(22, "Lock R820")
        # l'homonyme est en tête de `guild.categories` : si le nom l'emportait, c'est lui
        # qui serait rendu — or provision publie l'id 11.
        bot = FauxBot({"lock": 11})
        guild = FauxGuild([homonyme, bonne])
        self.assertIs(channels.resolve(bot, guild, "lock", "Lock R820"), bonne)

    def test_repli_sur_le_nom_quand_aucun_id_publie(self):
        cat = faux_categorie(11, "🔒 Lock R820")
        bot = FauxBot({})
        self.assertIs(channels.resolve(bot, FauxGuild([cat]), "lock", "Lock R820"), cat)

    def test_id_publie_perime_retombe_sur_le_nom(self):
        """Catégorie supprimée puis recréée à la main : l'id de l'état ne résout plus."""
        cat = faux_categorie(99, "🔒 Lock R820")
        bot = FauxBot({"lock": 11})
        self.assertIs(channels.resolve(bot, FauxGuild([cat]), "lock", "Lock R820"), cat)

    def test_cle_inconnue_renvoie_None_ET_journalise_une_erreur(self):
        """La faute de frappe `"super"` (au lieu de `"supervision"`) a coûté des salons
        créés à la racine : elle doit être BRUYANTE, pas silencieuse."""
        bot = FauxBot({"supervision": 11})
        guild = FauxGuild([faux_categorie(11, "📊 Supervision R820")])
        with self.assertLogs("discord-bot.channels", level="ERROR") as journal:
            self.assertIsNone(channels.resolve(bot, guild, "super", "Supervision R820"))
        self.assertIn("super", "\n".join(journal.output))

    def test_cles_avy_dynamiques_acceptees(self):
        """Les clés par nœud Aveyron sont générées, donc absentes de KNOWN_KEYS."""
        cat = faux_categorie(31, "📊 Supervision AVY-PVE")
        bot = FauxBot({"avy_sup_pve": 31})
        self.assertIs(channels.resolve(bot, FauxGuild([cat]), "avy_sup_pve"), cat)

    def test_guild_None_renvoie_None(self):
        bot = FauxBot({"lock": 11})
        self.assertIsNone(channels.resolve(bot, None, "lock", "Lock R820"))

    def test_introuvable_renvoie_None_sans_rien_creer(self):
        bot = FauxBot({})
        self.assertIsNone(channels.resolve(bot, FauxGuild([]), "lock", "Lock R820"))

    def test_un_salon_texte_nest_pas_une_categorie(self):
        """`_by_id` filtre sur le type : un id publié pointant sur un salon texte ne doit
        pas devenir une « catégorie » dans laquelle on créerait des salons."""
        faux = discord.TextChannel.__new__(discord.TextChannel)
        faux.id = 11
        faux.name = "lock-r820"
        bot = FauxBot({"lock": 11})
        self.assertIsNone(channels.resolve(bot, FauxGuild([faux]), "lock"))

    def test_lock_et_supervision_derivent_de_server_key(self):
        lock = faux_categorie(11, "🔒 Lock R820")
        sup = faux_categorie(12, "📊 Supervision R820")
        bot = FauxBot({})
        guild = FauxGuild([lock, sup])
        self.assertIs(channels.lock_category(bot, guild), lock)
        self.assertIs(channels.supervision_category(bot, guild), sup)


# ------------------------------------------------------------- serveur HTTP local
class _Handler(http.server.BaseHTTPRequestHandler):
    """Réponses minimales couvrant les quatre issues de `request_json`."""

    protocol_version = "HTTP/1.1"

    def _envoi(self, code, corps=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        if corps:
            self.wfile.write(corps)

    def do_GET(self):  # noqa: N802 — nom imposé par BaseHTTPRequestHandler
        self.server.vues.append(self.path)
        if self.path.startswith("/json"):
            self._envoi(200, json.dumps({"ok": True, "chemin": self.path}).encode())
        elif self.path.startswith("/vide"):
            self._envoi(200, b"")
        elif self.path.startswith("/pasjson"):
            self._envoi(200, b"<html>503 Service Unavailable</html>", "text/html")
        elif self.path.startswith("/erreur"):
            self._envoi(500, b'{"message":"API key invalid"}')
        else:
            self._envoi(404, b"{}")

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        self.server.dernier_corps = self.rfile.read(n) if n else b""
        self.server.vues.append("POST " + self.path)
        self._envoi(200, b'{"recu":true}')

    def log_message(self, *a):  # silence : le journal du test n'a rien à y gagner
        pass


class TestClientHttp(unittest.TestCase):
    """`None` = échec, `{}` = corps vide, `[]` = « rien à signaler ». Confondre les trois
    est le défaut d'origine (`/langues` rassurant alors que Radarr était injoignable)."""

    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.srv.daemon_threads = True
        cls.srv.vues = []
        cls.srv.dernier_corps = None
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        # les échecs attendus sont journalisés en WARNING : inutile de polluer la sortie
        cls._niveau = logging.getLogger("discord-bot.http").level
        logging.getLogger("discord-bot.http").setLevel(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.getLogger("discord-bot.http").setLevel(cls._niveau)
        cls.srv.shutdown()
        cls.srv.server_close()

    # --- request_json
    def test_json_valide_decode(self):
        self.assertEqual(bhttp.request_json(self.base + "/json")["ok"], True)

    def test_corps_vide_donne_dict_vide_PAS_None(self):
        """204/200 sans corps = « rien à dire », surtout pas « appel en échec »."""
        self.assertEqual(bhttp.request_json(self.base + "/vide"), {})

    def test_json_invalide_donne_None(self):
        """Une page HTML d'erreur d'un reverse-proxy ne doit jamais passer pour des
        données : mieux vaut « injoignable » qu'un contenu inventé."""
        self.assertIsNone(bhttp.request_json(self.base + "/pasjson"))

    def test_erreur_http_donne_None(self):
        self.assertIsNone(bhttp.request_json(self.base + "/erreur"))

    def test_le_corps_de_lerreur_est_journalise(self):
        """« API key invalid » dans le corps d'un 500 : le perdre transforme un problème
        de configuration en mystère."""
        # `assertLogs` pose lui-même le niveau du logger le temps du bloc : le
        # silence CRITICAL de setUpClass ne masque donc rien ici.
        with self.assertLogs("discord-bot.http", level="WARNING") as journal:
            bhttp.request_json(self.base + "/erreur", label="radarr /movie")
        sortie = "\n".join(journal.output)
        self.assertIn("radarr /movie", sortie)
        self.assertIn("API key invalid", sortie)

    def test_service_injoignable_donne_None_sans_lever(self):
        with socket.socket() as s:                 # port libre, personne n'écoute
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.assertIsNone(bhttp.request_json("http://127.0.0.1:%d/json" % port))

    def test_timeout_transmis_a_urlopen(self):
        """Un `urlopen` SANS timeout immobilise un worker du pool `to_thread` PARTAGÉ
        (6 sur ce conteneur 2 cœurs) : le timeout doit atteindre l'appel, toujours."""
        with mock.patch("urllib.request.urlopen") as espion:
            espion.side_effect = OSError("coupé")
            bhttp.request_json(self.base + "/json", timeout=3)
        self.assertEqual(espion.call_args.kwargs.get("timeout"), 3)

    def test_timeout_par_defaut_toujours_pose(self):
        with mock.patch("urllib.request.urlopen") as espion:
            espion.side_effect = OSError("coupé")
            bhttp.request_json(self.base + "/json")
        self.assertEqual(espion.call_args.kwargs.get("timeout"), bhttp.DEFAULT_TIMEOUT)

    def test_post_envoie_du_json(self):
        rep = bhttp.request_json(self.base + "/cmd", method="POST", body={"a": 1})
        self.assertEqual(rep, {"recu": True})
        self.assertEqual(json.loads(self.srv.dernier_corps), {"a": 1})

    # --- ApiClient.url
    def test_url_chemins_et_parametres(self):
        c = bhttp.ApiClient("http://10.3.10.120:7878/", {"X-Api-Key": "k"}, label="radarr")
        self.assertEqual(c.base, "http://10.3.10.120:7878")       # slash final retiré
        self.assertEqual(c.url("/api/v3/movie"), "http://10.3.10.120:7878/api/v3/movie")
        self.assertEqual(c.url("api/v3/movie"), "http://10.3.10.120:7878/api/v3/movie")
        self.assertEqual(c.url("/api/v3/movie", {"term": "un film"}),
                         "http://10.3.10.120:7878/api/v3/movie?term=un+film")
        # un chemin portant déjà une query enchaîne avec « & », jamais un second « ? »
        self.assertEqual(c.url("/api?a=1", {"b": 2}), "http://10.3.10.120:7878/api?a=1&b=2")
        self.assertEqual(c.url("/api/v3/movie", None), "http://10.3.10.120:7878/api/v3/movie")

    def test_url_encode_les_caracteres_speciaux(self):
        c = bhttp.ApiClient("http://x")
        self.assertEqual(c.url("/s", {"q": "a&b=c"}), "http://x/s?q=a%26b%3Dc")

    def test_client_get_rend_None_en_cas_dechec(self):
        c = bhttp.ApiClient(self.base, label="faux")
        self.assertIsNone(c.get("/erreur"))
        self.assertEqual(c.get("/vide"), {})

    # --- load_service_apis / client_for
    def test_fichier_absent_donne_dict_vide_sans_lever(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "servarr-apis.json")
            # absence DITE : sans ce fichier, /langues et le débit qBittorrent sont muets
            # POUR TOUJOURS — ce qui se lit comme une seedbox à 0 o/s.
            with self.assertLogs("discord-bot.http", level="WARNING"):
                self.assertEqual(bhttp.load_service_apis(chemin), {})

    def test_json_casse_donne_dict_vide(self):
        """Le bot doit DÉMARRER même si ce fichier est corrompu."""
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "servarr-apis.json")
            with open(chemin, "w") as f:
                f.write("{ pas du json")
            self.assertEqual(bhttp.load_service_apis(chemin), {})

    def test_fichier_valide_charge(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "servarr-apis.json")
            with open(chemin, "w") as f:
                json.dump({"radarr": {"url": "http://x:7878", "key": "abc"}}, f)
            apis = bhttp.load_service_apis(chemin)
            self.assertEqual(apis["radarr"]["url"], "http://x:7878")
            cli = bhttp.client_for(apis, "radarr")
            self.assertEqual(cli.headers.get("X-Api-Key"), "abc")
            self.assertEqual(cli.label, "radarr")
            # service non configuré : None, et l'appelant doit le dire à l'utilisateur
            self.assertIsNone(bhttp.client_for(apis, "sonarr"))
            self.assertIsNone(bhttp.client_for({}, "radarr"))


# ------------------------------------------------------------ format.outcome_text
class TestVerdictDeTache(unittest.TestCase):
    """« lost » n'est PAS un échec : NOTRE suivi s'est arrêté, la tâche continue côté
    PVE. Le rendre comme une erreur ferait croire à une sauvegarde ratée."""

    def test_ok(self):
        self.assertTrue(fmt.outcome_text("OK").startswith("✅"))
        self.assertIn("sauvegarde terminée",
                      fmt.outcome_text("OK", done_label="sauvegarde terminée"))

    def test_running(self):
        t = fmt.outcome_text("running")
        self.assertTrue(t.startswith("⏳"))
        self.assertIn("en cours", t)

    def test_lost_dit_que_la_tache_continue(self):
        t = fmt.outcome_text("lost")
        self.assertNotIn("échec", t.lower())
        self.assertIn("continue", t)
        self.assertIn("/tasks", t)          # où l'utilisateur va chercher le vrai sort

    def test_autre_verdict_rendu_tel_quel(self):
        self.assertEqual(fmt.outcome_text("stopped: exit code 1"),
                         "⚠️ stopped: exit code 1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
