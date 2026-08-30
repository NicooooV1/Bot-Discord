"""Tests du cog `faq` — sans réseau ni Discord (fakes légers).

POURQUOI : une FAQ est lue par des invités SANS rôle et postable en public par les M/O.
Le tag JS d'origine pinguait @everyone (texte brut sans allowedMentions) : chaque envoi
doit ici porter `AllowedMentions.none()`. On verrouille aussi la normalisation des noms
(« VPN Pierre » = « vpn-pierre »), les refus de contenu (longueur, schémas d'URL), le
compteur d'usages, le refus de `public` pour un non-gestionnaire et l'audit.
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.cogs import faq as F  # noqa: E402


# ---------------------------------------------------------------------------- fakes
class FakeState:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FakeAudit:
    def __init__(self):
        self.rows = []

    def record(self, **kw):
        self.rows.append(kw)


class FakeCfg:
    guild_id = 100
    server_key = "R820"


class FakeBot:
    def __init__(self):
        self.cfg = FakeCfg()
        self.state = FakeState()
        self.audit = FakeAudit()


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, **kw):
        self.sent.append(dict(kw, content=content))

    def is_done(self):
        return bool(self.sent)


class FakeUser:
    def __init__(self, uid=42):
        self.id = uid

    def __str__(self):
        return f"user{self.id}"


class FakeItx:
    def __init__(self, uid=42, guild_id=100):
        self.user = FakeUser(uid)
        self.guild_id = guild_id
        self.guild = None
        self.channel = None
        self.client = None
        self.response = FakeResponse()


def run(coro):
    return asyncio.run(coro)


def _cog():
    return F.Faq(FakeBot())


# ---------------------------------------------------------------------------- purs
class TestNormalisation(unittest.TestCase):
    def test_minuscules_et_tirets(self):
        self.assertEqual(F.normalize_name("VPN Pierre"), "vpn-pierre")
        self.assertEqual(F.normalize_name("  Adresse_Jellyfin. "), "adresse-jellyfin")
        self.assertEqual(F.normalize_name("--a---b--"), "a-b")

    def test_caracteres_interdits_retires(self):
        self.assertEqual(F.normalize_name("wifi (invités) !"), "wifi-invits")

    def test_vide_donne_none(self):
        self.assertIsNone(F.normalize_name("   "))
        self.assertIsNone(F.normalize_name("@@@"))
        self.assertIsNone(F.normalize_name(None))

    def test_borne_longueur(self):
        self.assertLessEqual(len(F.normalize_name("x" * 100)), F.NAME_MAX)


class TestContenu(unittest.TestCase):
    def test_ok_markdown_et_https(self):
        c, why = F.check_content("**Jellyfin** : https://media.nicov1.fr et http://10.3.10.116")
        self.assertIsNone(why)
        self.assertIn("https://", c)

    def test_vide_et_trop_long(self):
        self.assertEqual(F.check_content("  ")[1], "contenu vide")
        self.assertIn("trop long", F.check_content("a" * 1801, 1800)[1])
        self.assertIsNone(F.check_content("a" * 1800, 1800)[1])

    def test_schemas_interdits(self):
        _, why = F.check_content("ouvre ftp://nas/ ou file:///etc/passwd")
        self.assertIn("http(s)", why)
        self.assertIn("ftp", why)
        self.assertIn("file", why)


# ---------------------------------------------------------------------------- commandes
class TestVoir(unittest.TestCase):
    def setUp(self):
        self.cog = _cog()
        self.cog.bot.state.set("faq", {"vpn": {"content": "@everyone <@&1> https://vpn", "uses": 2,
                                               "author": "nico", "author_id": 1}})

    def test_ephemere_mentions_neutralisees_compteur(self):
        itx = FakeItx()
        run(self.cog.voir.callback(self.cog, itx, nom="VPN"))
        s = itx.response.sent[0]
        self.assertTrue(s["ephemeral"])
        self.assertIn("@everyone", s["content"])          # le texte est rendu tel quel…
        am = s["allowed_mentions"]
        self.assertFalse(am.everyone)                     # …mais ne pingue personne
        self.assertEqual(am.roles, False)
        self.assertEqual(am.users, False)
        self.assertEqual(self.cog.bot.state.get("faq")["vpn"]["uses"], 3)

    def test_introuvable(self):
        itx = FakeItx()
        run(self.cog.voir.callback(self.cog, itx, nom="inexistant"))
        self.assertIn("introuvable", itx.response.sent[0]["content"])
        self.assertTrue(itx.response.sent[0]["ephemeral"])

    def test_public_refuse_sans_role_reste_ephemere(self):
        itx = FakeItx()
        with mock.patch.object(F, "is_admin", return_value=False), \
                mock.patch.object(F, "channel_server", return_value="R820"), \
                mock.patch.object(F, "log_refusal"):
            run(self.cog.voir.callback(self.cog, itx, nom="vpn", public=True))
        s = itx.response.sent[0]
        self.assertTrue(s["ephemeral"])
        self.assertIn("réservé", s["content"])
        self.assertEqual(self.cog.bot.state.get("faq")["vpn"]["uses"], 2)  # pas compté

    def test_public_ok_pour_mod(self):
        itx = FakeItx()
        with mock.patch.object(F, "is_admin", return_value=True), \
                mock.patch.object(F, "channel_server", return_value="R820"):
            run(self.cog.voir.callback(self.cog, itx, nom="vpn", public=True))
        s = itx.response.sent[0]
        self.assertFalse(s["ephemeral"])
        self.assertFalse(s["allowed_mentions"].everyone)

    def test_autre_guild_refuse(self):
        itx = FakeItx(guild_id=999)
        run(self.cog.voir.callback(self.cog, itx, nom="vpn"))
        self.assertIn("non autorisé", itx.response.sent[0]["content"])


class TestGestion(unittest.TestCase):
    def setUp(self):
        self.cog = _cog()

    def test_ajout_normalise_et_audit(self):
        itx = FakeItx(uid=7)
        run(self.cog.ajouter.callback(self.cog, itx, nom="Adresse Jellyfin", contenu="https://media.nicov1.fr"))
        d = self.cog.bot.state.get("faq")
        self.assertIn("adresse-jellyfin", d)
        self.assertEqual(d["adresse-jellyfin"]["author_id"], 7)
        self.assertEqual(d["adresse-jellyfin"]["uses"], 0)
        self.assertEqual(self.cog.bot.audit.rows[-1]["action"], "faq_add")
        self.assertEqual(self.cog.bot.audit.rows[-1]["target"], "adresse-jellyfin")

    def test_doublon_refuse(self):
        itx = FakeItx()
        run(self.cog.ajouter.callback(self.cog, itx, nom="a", contenu="x"))
        run(self.cog.ajouter.callback(self.cog, itx, nom="A", contenu="y"))
        self.assertIn("existe déjà", itx.response.sent[1]["content"])
        self.assertEqual(self.cog.bot.state.get("faq")["a"]["content"], "x")

    def test_contenu_refuse_pas_enregistre_ni_audite(self):
        itx = FakeItx()
        run(self.cog.ajouter.callback(self.cog, itx, nom="a", contenu="ftp://x"))
        self.assertIn("Refusé", itx.response.sent[0]["content"])
        self.assertEqual(self.cog.bot.state.get("faq", {}), {})
        self.assertEqual(self.cog.bot.audit.rows, [])

    def test_modifier_garde_usages_et_audit(self):
        itx = FakeItx()
        run(self.cog.ajouter.callback(self.cog, itx, nom="a", contenu="x"))
        self.cog.bot.state.get("faq")["a"]["uses"] = 5
        run(self.cog.modifier.callback(self.cog, itx, nom="a", contenu="y"))
        e = self.cog.bot.state.get("faq")["a"]
        self.assertEqual(e["content"], "y")
        self.assertEqual(e["uses"], 5)
        self.assertEqual(self.cog.bot.audit.rows[-1]["action"], "faq_edit")

    def test_supprimer_et_audit(self):
        itx = FakeItx()
        run(self.cog.ajouter.callback(self.cog, itx, nom="a", contenu="x"))
        run(self.cog.supprimer.callback(self.cog, itx, nom="a"))
        self.assertEqual(self.cog.bot.state.get("faq"), {})
        self.assertEqual(self.cog.bot.audit.rows[-1]["action"], "faq_delete")
        run(self.cog.supprimer.callback(self.cog, itx, nom="a"))
        self.assertIn("introuvable", itx.response.sent[-1]["content"])

    def test_liste_usages_auteur(self):
        itx = FakeItx()
        self.cog.bot.state.set("faq", {"b": {"content": "x", "uses": 1, "author": "nico", "updated": 1},
                                       "a": {"content": "x", "uses": 9, "author": "pierre", "updated": 1}})
        run(self.cog.liste.callback(self.cog, itx))
        desc = itx.response.sent[0]["embed"].description
        self.assertLess(desc.index("`a`"), desc.index("`b`"))  # tri par usages décroissants
        self.assertIn("9 usage(s)", desc)
        self.assertIn("pierre", desc)
        self.assertFalse(itx.response.sent[0]["allowed_mentions"].everyone)

    def test_autocomplete_filtre(self):
        self.cog.bot.state.set("faq", {"vpn-pierre": {}, "jellyfin": {}, "vpn-nico": {}})
        out = run(self.cog._ac_names(FakeItx(), "vpn"))
        self.assertEqual([c.value for c in out], ["vpn-nico", "vpn-pierre"])
        self.assertIsInstance(out[0], discord.app_commands.Choice)


if __name__ == "__main__":
    unittest.main()
