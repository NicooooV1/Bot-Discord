"""Cloisonnement PAR SERVEUR + capacités de l'Owner (audit 2026-08-29).

Ce que l'audit a trouvé : `admin_check()` consultait un rôle GLOBAL (ADMIN_ROLE_IDS =
union des M/O de R820 + AVY-NAS + AVY-LLM) et aucune commande ne rattachait sa cible à
un serveur — `/ctctl stop xxx-avy` passait avec un simple « M R820 », et un « M AVY-NAS »
pouvait couper Vaultwarden. Chaque test ci-dessous verrouille une des fermetures.

    cd /opt/discord-bot && ./venv/bin/python -m unittest discover -s tests -v
"""
import asyncio
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from bot.core import channels, permissions, srvperms  # noqa: E402
from bot.core.config import Config  # noqa: E402

# rôles : R820 G/M/O = 1/2/3 ; AVY-NAS = 4/5/6 ; AVY-LLM = 7/8/9
ENV = {"DISCORD_TOKEN": "x", "GUILD_ID": "100", "ADMIN_IDS": "999",
       "ADMIN_ROLE_IDS": "2,3,5,6,8,9",          # l'union fautive de la prod
       "GESTION_SERVERS": "R820:1:2:3,AVY-NAS:4:5:6,AVY-LLM:7:8:9",
       "NODE_SERVER_KEY": "R820"}


class FauxState:
    def __init__(self, d=None):
        self.d = dict(d or {})

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FauxAudit:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


class FauxPve:
    enabled = True

    def __init__(self, gm=None):
        self._gm = gm or {}

    @staticmethod
    def avy_server_key(node):
        return f"AVY-{str(node).upper()}"

    def is_avy_name(self, name):
        return str(name).endswith("-avy")

    def guest_map(self):
        return self._gm


class FauxBot:
    def __init__(self, cfg, cats=None, state=None, gm=None):
        self.cfg = cfg
        self.state = state or FauxState({"prov": {"categories": cats or {}}})
        self.pve = FauxPve(gm)
        self.audit = FauxAudit()
        self.twofa = None


class FauxRole:
    def __init__(self, rid):
        self.id = rid

    def is_default(self):
        return False


class FauxUser:
    def __init__(self, uid, roles=()):
        self.id = uid
        self.roles = [FauxRole(r) for r in roles]

    def __str__(self):
        return f"user{self.id}"


class FauxCat:
    def __init__(self, cid, name):
        self.id = cid
        self.name = name


class FauxChannel:
    def __init__(self, cid, cat):
        self.id = cid
        self.category = cat
        self.parent = None


class FauxGuild:
    owner_id = 1


class Itx:
    """Interaction minimale : user, guild, salon (catégorie), client, command."""

    def __init__(self, bot, uid=42, roles=(), channel=None, owner_id=1):
        self.user = FauxUser(uid, roles)
        self.guild = FauxGuild()
        self.guild.owner_id = owner_id
        self.guild_id = 100
        self.channel = channel
        self.channel_id = getattr(channel, "id", 0)
        self.client = bot
        self.command = None
        self.data = {}


def cfg():
    return Config(env=dict(ENV))


CATS = {"supervision": 10, "containers": 11, "lock": 12,
        "avy_sup_nas": 20, "avy_gest_nas": 21, "avy_lock_nas": 22,
        "avy_gest_llm": 31}
C_R820 = FauxCat(11, "Gestion R820")
C_LOCK_R820 = FauxCat(12, "🔒 Lock R820")
C_NAS = FauxCat(21, "Gestion AVY-NAS")
C_LOCK_NAS = FauxCat(22, "🔒 Lock AVY-NAS")
C_LLM = FauxCat(31, "Gestion AVY-LLM")


def run(coro):
    return asyncio.run(coro)


async def _pred(check_decorator, itx):
    """Exécute le prédicat posé par admin_check()/read_check() sur une commande factice."""
    async def cmd(i):
        return True
    decorated = check_decorator(cmd)
    pred = decorated.__discord_app_commands_checks__[0]
    return await pred(itx)


# ============================================================ is_admin / tier_of
class TestIsAdminParServeur(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.bot = FauxBot(self.cfg, CATS)

    def test_sans_serveur_veut_dire_R820_pas_le_role_global(self):
        """Le cœur de l'audit : un M AVY-NAS (dans ADMIN_ROLE_IDS) n'est PAS admin R820."""
        nas = Itx(self.bot, roles=(5,))
        self.assertFalse(permissions.is_admin(self.cfg, nas))
        self.assertFalse(permissions.is_admin(self.cfg, nas, server="R820"))
        self.assertTrue(permissions.is_admin(self.cfg, nas, server="AVY-NAS"))
        r820 = Itx(self.bot, roles=(2,))
        self.assertTrue(permissions.is_admin(self.cfg, r820))
        self.assertFalse(permissions.is_admin(self.cfg, r820, server="AVY-NAS"))
        self.assertFalse(permissions.is_admin(self.cfg, r820, server="AVY-LLM"))

    def test_aveyron_est_plusieurs_serveurs_independants(self):
        """« Aveyron » = AVY-NAS, AVY-LLM… séparés : O AVY-NAS n'est rien sur AVY-LLM."""
        o_nas = Itx(self.bot, roles=(6,))
        self.assertEqual(permissions.tier_of(self.cfg, o_nas, "AVY-NAS"), "O")
        self.assertIsNone(permissions.tier_of(self.cfg, o_nas, "AVY-LLM"))
        self.assertIsNone(permissions.tier_of(self.cfg, o_nas, "R820"))

    def test_tier_of_et_break_glass(self):
        self.assertEqual(permissions.tier_of(self.cfg, Itx(self.bot, roles=(1,)), "R820"), "G")
        self.assertEqual(permissions.tier_of(self.cfg, Itx(self.bot, roles=(2,)), "R820"), "M")
        self.assertEqual(permissions.tier_of(self.cfg, Itx(self.bot, roles=(3,)), "R820"), "O")
        self.assertEqual(permissions.tier_of(self.cfg, Itx(self.bot, uid=1), "AVY-LLM"), "O")
        self.assertEqual(permissions.tier_of(self.cfg, Itx(self.bot, uid=999), "AVY-LLM"), "O")
        self.assertIsNone(permissions.tier_of(self.cfg, Itx(self.bot, roles=(2,)), "AVY-INCONNU"))

    def test_legacy_sans_gestion_servers(self):
        """Instance sans GESTION_SERVERS : ADMIN_ROLE_IDS reste le M du primaire — et
        uniquement du primaire."""
        c = Config(env={"DISCORD_TOKEN": "x", "GUILD_ID": "100", "ADMIN_ROLE_IDS": "500"})
        b = FauxBot(c)
        self.assertTrue(permissions.is_admin(c, Itx(b, roles=(500,))))
        self.assertFalse(permissions.is_admin(c, Itx(b, roles=(500,)), server="AVY-NAS"))

    def test_can_read_par_serveur_et_G_ouvert_par_owner(self):
        g = Itx(self.bot, roles=(1,))                     # G R820
        self.assertFalse(permissions.can_read(self.cfg, g))
        srvperms.set_caps(self.bot.state, "R820", "G", {"read"})
        self.assertTrue(permissions.can_read(self.cfg, g))
        self.assertFalse(permissions.can_read(self.cfg, g, server="AVY-NAS"))
        m_nas = Itx(self.bot, roles=(5,))
        self.assertTrue(permissions.can_read(self.cfg, m_nas, server="AVY-NAS"))
        self.assertFalse(permissions.can_read(self.cfg, m_nas))   # pas de lecture R820


# ============================================================ admin_check / read_check
class TestPortesParSalon(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg()
        self.bot = FauxBot(self.cfg, CATS)

    def _chk(self, deco, itx):
        try:
            return run(_pred(deco, itx))
        except app_commands.CheckFailure as e:
            return str(e)

    def test_scope_channel_exige_le_role_du_serveur_du_salon(self):
        deco = permissions.admin_check(require_admin_channel=False, scope="channel")
        m_r820 = Itx(self.bot, roles=(2,), channel=FauxChannel(500, C_NAS))
        self.assertIn("AVY-NAS", self._chk(deco, m_r820))          # refus explicite
        m_nas = Itx(self.bot, roles=(5,), channel=FauxChannel(500, C_NAS))
        self.assertIs(self._chk(deco, m_nas), True)
        m_nas_r820 = Itx(self.bot, roles=(5,), channel=FauxChannel(501, C_R820))
        self.assertIn("R820", self._chk(deco, m_nas_r820))

    def test_scope_primary_refuse_hors_des_salons_R820(self):
        """/docker, /dns, /sso… décrivent le R820 : refusés depuis un salon AVY-NAS,
        même à un M R820 (on ne mélange rien), et à un M AVY-NAS partout."""
        deco = permissions.admin_check(require_admin_channel=False)
        self.assertIs(self._chk(deco, Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_R820))), True)
        self.assertIs(self._chk(deco, Itx(self.bot, roles=(2,), channel=None)), True)  # #général
        self.assertIn("ne se mélangent pas",
                      self._chk(deco, Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_NAS))))
        self.assertIn("R820", self._chk(deco, Itx(self.bot, roles=(5,), channel=None)))

    def test_salon_admin_est_la_categorie_Lock_du_serveur_vise(self):
        deco = permissions.admin_check(scope="channel")
        # M R820 depuis un salon de Gestion R820 : refusé (pas dans Lock)
        self.assertIn("Lock R820",
                      self._chk(deco, Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_R820))))
        # depuis 🔒 Lock R820 : OK
        self.assertIs(self._chk(deco, Itx(self.bot, roles=(2,), channel=FauxChannel(2, C_LOCK_R820))), True)
        # M AVY-NAS depuis 🔒 Lock AVY-NAS : OK ; depuis Lock R820 : refusé
        self.assertIs(self._chk(deco, Itx(self.bot, roles=(5,), channel=FauxChannel(3, C_LOCK_NAS))), True)
        self.assertIn("AVY-NAS", self._chk(deco, Itx(self.bot, roles=(2,), channel=FauxChannel(3, C_LOCK_NAS))))

    def test_capacite_exigee_par_la_porte(self):
        deco = permissions.admin_check(require_admin_channel=False, cap="services")
        m = Itx(self.bot, roles=(2,), channel=None)
        self.assertIs(self._chk(deco, m), True)
        srvperms.set_caps(self.bot.state, "R820", "M", set(srvperms.CAPS) - {"services"})
        self.assertIn("Panneaux services", self._chk(deco, m))
        # l'Owner n'est jamais restreint
        self.assertIs(self._chk(deco, Itx(self.bot, roles=(3,), channel=None)), True)

    def test_read_check_scope_channel(self):
        deco = permissions.read_check(scope="channel")
        self.assertIs(self._chk(deco, Itx(self.bot, roles=(5,), channel=FauxChannel(1, C_NAS))), True)
        self.assertIn("AVY-NAS", self._chk(deco, Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_NAS))))

    def test_qualname_reconnu_par_help(self):
        """/help repère les commandes admin au __qualname__ du prédicat : ne pas le casser."""
        async def cmd(i):
            return True
        pred = permissions.admin_check()(cmd).__discord_app_commands_checks__[0]
        self.assertIn("admin_check", pred.__qualname__)


# ============================================================ refus journalisés
class TestRefusJournalises(unittest.TestCase):
    def test_check_failure_audite(self):
        c = cfg()
        bot = FauxBot(c, CATS)
        itx = Itx(bot, roles=(5,), channel=None)
        permissions.log_refusal(itx, "check", "Réservé aux gestionnaires de R820")
        self.assertEqual(len(bot.audit.rows), 1)
        self.assertEqual(bot.audit.rows[0]["action"], "refus")
        self.assertIn("R820", bot.audit.rows[0]["result"])


# ============================================================ srvperms
class TestSrvPerms(unittest.TestCase):
    def setUp(self):
        self.st = FauxState()

    def test_defauts(self):
        self.assertTrue(srvperms.cap_allowed(self.st, "R820", "M", "stop"))
        self.assertFalse(srvperms.cap_allowed(self.st, "R820", "M", "node_terminal"))
        self.assertFalse(srvperms.cap_allowed(self.st, "R820", "G", "stop"))
        self.assertTrue(srvperms.cap_allowed(self.st, "R820", "O", "node_terminal"))
        self.assertFalse(srvperms.cap_allowed(self.st, "R820", "M", "inconnue"))   # fail-closed

    def test_ecarts_seuls_persistes_et_independants_par_serveur(self):
        allowed = {c for c, v in srvperms.effective_caps(self.st, "AVY-NAS", "M").items() if v}
        srvperms.set_caps(self.st, "AVY-NAS", "M", allowed - {"stop", "terminal"})
        self.assertFalse(srvperms.cap_allowed(self.st, "AVY-NAS", "M", "stop"))
        self.assertTrue(srvperms.cap_allowed(self.st, "AVY-NAS", "M", "start"))
        self.assertTrue(srvperms.cap_allowed(self.st, "R820", "M", "stop"))       # autre serveur intact
        self.assertTrue(srvperms.cap_allowed(self.st, "AVY-LLM", "M", "stop"))
        caps = self.st.d[srvperms.STATE_KEY]["AVY-NAS"]["M"]["caps"]
        self.assertEqual(caps, {"stop": False, "terminal": False})
        srvperms.reset(self.st, "AVY-NAS", "M")
        self.assertNotIn("AVY-NAS", self.st.d[srvperms.STATE_KEY])

    def test_salons_masques(self):
        srvperms.set_hidden(self.st, "R820", "G", {11, "12"})
        self.assertEqual(srvperms.hidden_channels(self.st, "R820", "G"), {11, 12})
        self.assertEqual(srvperms.hidden_channels(self.st, "R820", "M"), set())
        self.assertEqual(srvperms.hidden_channels(self.st, "AVY-NAS", "G"), set())

    def test_tier_O_non_reglable(self):
        with self.assertRaises(ValueError):
            srvperms.set_caps(self.st, "R820", "O", set())


# ============================================================ channels helpers
class TestChannelsParServeur(unittest.TestCase):
    def setUp(self):
        self.bot = FauxBot(cfg(), CATS)

    def test_lock_server_of_channel(self):
        f = channels.lock_server_of_channel
        self.assertEqual(f(self.bot, FauxChannel(1, C_LOCK_R820)), "R820")
        self.assertEqual(f(self.bot, FauxChannel(1, C_LOCK_NAS)), "AVY-NAS")
        self.assertIsNone(f(self.bot, FauxChannel(1, C_R820)))
        self.assertIsNone(f(self.bot, None))
        # repli sur le NOM quand provision n'a pas publié l'id
        self.assertEqual(f(self.bot, FauxChannel(1, FauxCat(99, "🔒 Lock AVY-LLM"))), "AVY-LLM")


# ============================================================ guard_target / autocomplete
class TestGardeDeCible(unittest.TestCase):
    def setUp(self):
        from bot.core import ui
        self.ui = ui
        gm = {"caddy": {"vmid": 100, "type": "lxc", "node": "pve"},
              "k8s-avy": {"vmid": 1000200, "type": "lxc", "node": "nas"},
              "orphelin-avy": {"vmid": 1000300, "type": "lxc"}}
        self.bot = FauxBot(cfg(), CATS, gm=gm)

    def test_cible_hors_serveur_du_salon_refusee(self):
        itx = Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_LOCK_R820))
        self.assertIsNone(run(self.ui.guard_target(self.bot, itx, "caddy")))
        self.assertIn("AVY-NAS", run(self.ui.guard_target(self.bot, itx, "k8s-avy")))
        itx_nas = Itx(self.bot, roles=(5,), channel=FauxChannel(1, C_LOCK_NAS))
        self.assertIsNone(run(self.ui.guard_target(self.bot, itx_nas, "k8s-avy")))
        self.assertIn("R820", run(self.ui.guard_target(self.bot, itx_nas, "caddy")))

    def test_avy_sans_noeud_refuse_jamais_rattache_au_R820(self):
        itx = Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_LOCK_R820))
        self.assertIn("non résolu", run(self.ui.guard_target(self.bot, itx, "orphelin-avy")))

    def test_autocomplete_vide_sans_droit_de_lecture(self):
        sans_role = Itx(self.bot, roles=(), channel=FauxChannel(1, C_R820))
        self.assertEqual(run(self.ui.ct_autocomplete(sans_role, "")), [])
        m_nas_dans_r820 = Itx(self.bot, roles=(5,), channel=FauxChannel(1, C_R820))
        self.assertEqual(run(self.ui.ct_autocomplete(m_nas_dans_r820, "")), [])
        m_r820 = Itx(self.bot, roles=(2,), channel=FauxChannel(1, C_R820))
        self.assertEqual([c.value for c in run(self.ui.ct_autocomplete(m_r820, ""))], ["caddy"])


# ============================================================ provision_perms
class FauxGuildRoles:
    def __init__(self, ids):
        self._roles = {i: FauxRole(i) for i in ids}
        self.default_role = FauxRole(0)
        self.me = FauxRole(-1)

    def get_role(self, rid):
        return self._roles.get(rid)

    def get_member(self, uid):
        return None


class TestOverwritesParServeur(unittest.TestCase):
    def setUp(self):
        from bot.cogs import provision_perms as pp
        self.pp = pp
        self.cfg = cfg()
        self.guild = FauxGuildRoles([1, 2, 3, 4, 5, 6, 7, 8, 9])

    def test_categories_R820_ne_voient_que_les_roles_R820(self):
        """Avant : tout ADMIN_ROLE_IDS (donc M/O AVY-NAS/LLM) voyait Gestion R820."""
        ow = self.pp.managed_overwrites(self.guild, self.cfg)
        ids = {t.id for t in ow}
        self.assertTrue({1, 2, 3} <= ids)
        self.assertFalse(ids & {4, 5, 6, 7, 8, 9})
        self.assertFalse(ow[self.guild.get_role(1)].use_application_commands)   # G : pas de commandes

    def test_G_ouvert_par_owner_recupere_les_commandes(self):
        st = FauxState()
        srvperms.set_caps(st, "R820", "G", {"read"})
        ow = self.pp.managed_overwrites(self.guild, self.cfg, st)
        self.assertTrue(ow[self.guild.get_role(1)].use_application_commands)
        ow2 = self.pp.srv_overwrites_fn(self.cfg, "AVY-NAS", st)(self.guild)
        self.assertFalse(ow2[self.guild.get_role(4)].use_application_commands)

    def test_for_channel_masque_le_salon_au_tier(self):
        st = FauxState()
        srvperms.set_hidden(st, "R820", "M", {555})
        base = self.pp.managed_overwrites(self.guild, self.cfg, st)
        same = self.pp.for_channel(base, self.guild, self.cfg, st, "R820", 444)
        self.assertIs(same, base)
        hid = self.pp.for_channel(base, self.guild, self.cfg, st, "R820", 555)
        self.assertFalse(hid[self.guild.get_role(2)].view_channel)    # M masqué
        self.assertTrue(hid[self.guild.get_role(3)].view_channel)     # O intact
        self.assertTrue(base[self.guild.get_role(2)].view_channel)    # base non mutée
        self.assertIsNone(self.pp.for_channel(None, self.guild, self.cfg, st, "R820", 555))


# ============================================================ /gestion
class TestGestion(unittest.TestCase):
    def test_niveau_obligatoire(self):
        from bot.cogs.gestion import Gestion
        params = Gestion.add.callback._callback.__signature__.parameters \
            if hasattr(Gestion.add.callback, "_callback") else \
            inspect.signature(Gestion.add.callback).parameters
        p = params["niveau"]
        self.assertIs(p.default, inspect.Parameter.empty, "niveau doit être OBLIGATOIRE")

    def test_perms_existe(self):
        from bot.cogs.gestion import Gestion
        self.assertTrue(hasattr(Gestion, "perms"))


if __name__ == "__main__":
    unittest.main()
