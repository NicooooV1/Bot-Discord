"""Tests du cog `rappel` — parseur de durée, logique pure et scheduler sur faux bot.

POURQUOI CES TESTS : un rappel est une promesse faite à un humain. Un parseur laxiste
(« 1j30 » lu comme 1 j 30 min ?) fausserait l'heure ; un envoi marqué APRÈS coup ferait
des doublons au redémarrage ; un salon supprimé perdrait le rappel en silence ; une
récurrence rejouée après une coupure secteur enverrait N messages d'un coup ; une
mention non bornée ferait pinger @everyone depuis un texte libre. Aucun réseau ici.
"""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.cogs import rappel as R  # noqa: E402


# --------------------------------------------------------------------------- fakes
class FauxState:
    def __init__(self):
        self.d = {}
        self.writes = 0

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v
        self.writes += 1


class FauxAudit:
    def __init__(self):
        self.lines = []

    def record(self, **kw):
        self.lines.append(kw)


class FauxUser:
    def __init__(self, uid, refuse=False):
        self.id = uid
        self.sent = []
        self.refuse = refuse

    async def send(self, content=None, **kw):
        if self.refuse:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="dm"), "DM fermés")
        self.sent.append((content, kw))


class FauxChannel:
    def __init__(self, cid, refuse=False):
        self.id = cid
        self.sent = []
        self.refuse = refuse

    async def send(self, content=None, **kw):
        if self.refuse:
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")
        self.sent.append((content, kw))


class FauxBot:
    def __init__(self):
        self.state = FauxState()
        self.audit = FauxAudit()
        self.cfg = SimpleNamespace(guild_id=100, server_key="R820", admin_ids=[],
                                   admin_role_ids=[], read_role_ids=[], gestion_servers={})
        self.intents = SimpleNamespace(members=False, message_content=False)
        self.channels = {}
        self.users = {}

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "absent")

    def get_user(self, uid):
        return self.users.get(uid)

    async def fetch_user(self, uid):
        raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "absent")


def _cog(bot=None):
    bot = bot or FauxBot()
    return R.Rappel(bot)


def _r(rid, auteur=7, echeance=100.0, mode="salon", salon=555, repeter=None, envoye=False,
       envoye_a=None, message="boire de l'eau"):
    return {"id": rid, "auteur_id": auteur, "guild_id": 100, "mode": mode, "salon_id": salon,
            "message": message, "cree_a": 0.0, "echeance": echeance, "repeter": repeter,
            "envoye": envoye, "envoye_a": envoye_a}


class Horloge:
    """Contexte : fige R._now."""

    def __init__(self, t):
        self.t = t

    def __enter__(self):
        self._old = R._now
        R._now = lambda: self.t
        return self

    def __exit__(self, *a):
        R._now = self._old


# --------------------------------------------------------------------------- durées
class TestParseDelai(unittest.TestCase):
    def test_formes_acceptees(self):
        cas = {
            "1min": 60, "90min": 5400, "2h": 7200, "1d12h": 129600, "1j12h": 129600,
            "1j12h30min": 131400, "2 jours": 172800, "1h30": 5400, "1 h 30": 5400,
            "45": 2700, "1 semaine": 604800, "2w": 1209600, "1 jour et 2 heures": 93600,
            "30 secondes": 30, "1J": 86400, "3 Heures, 15 Minutes": 11700,
            "365j": 365 * 86400,
        }
        for raw, attendu in cas.items():
            self.assertEqual(R.parse_delai(raw), attendu, raw)

    def test_refus(self):
        for raw in ("", "   ", "abc", "1x", "1j30", "0", "0min", "1h ab", "h1", "-5min",
                    "1.5h", "1 année"):
            with self.assertRaises(ValueError, msg=raw):
                R.parse_delai(raw)

    def test_fmt_delai(self):
        self.assertEqual(R.fmt_delai(131400), "1 j 12 h 30 min")
        self.assertEqual(R.fmt_delai(7200), "2 h")
        self.assertEqual(R.fmt_delai(45), "45 s")
        self.assertEqual(R.fmt_delai(0), "0 s")

    def test_bornes_du_cog(self):
        self.assertEqual(R.MIN_DELAI, 60)
        self.assertEqual(R.MAX_DELAI, 365 * 86400)
        self.assertLess(R.parse_delai("59s"), R.MIN_DELAI)
        self.assertGreater(R.parse_delai("366j"), R.MAX_DELAI)


# --------------------------------------------------------------------------- pure
class TestLogiquePure(unittest.TestCase):
    def test_echeants_tries_et_bornes(self):
        rappels = {str(i): _r(str(i), echeance=100 - i) for i in range(30)}
        rappels["x"] = _r("x", echeance=500)             # futur
        rappels["y"] = _r("y", echeance=1, envoye=True)  # déjà parti
        ids = R.echeants(rappels, now=100.0, lot=20)
        self.assertEqual(len(ids), 20)
        self.assertEqual(ids[0], "29")                   # le plus ancien d'abord
        self.assertNotIn("x", ids)
        self.assertNotIn("y", ids)

    def test_prochaine_echeance_saute_les_occurrences_manquees(self):
        # rappel horaire, bot éteint 7 h : UNE seule prochaine échéance, dans le futur
        nxt = R.prochaine_echeance(1000.0, 3600, now=1000.0 + 7 * 3600 + 5)
        self.assertGreater(nxt, 1000.0 + 7 * 3600 + 5)
        self.assertEqual(nxt, 1000.0 + 8 * 3600)

    def test_a_purger(self):
        now = 10 * 86400.0
        rappels = {"a": _r("a", envoye=True, envoye_a=now - 8 * 86400),
                   "b": _r("b", envoye=True, envoye_a=now - 6 * 86400),
                   "c": _r("c", envoye=False, echeance=1)}
        self.assertEqual(R.a_purger(rappels, now, 7), ["a"])

    def test_mentions_pour_seulement_l_auteur(self):
        am = R.mentions_pour(42)
        self.assertFalse(am.everyone)
        self.assertFalse(am.roles)
        self.assertEqual([u.id for u in am.users], [42])


# --------------------------------------------------------------------------- scheduler
class TestScheduler(unittest.TestCase):
    def test_envoi_salon_marque_avant_envoi(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100)}

        with Horloge(110.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 1)
        content, kw = ch.sent[0]
        self.assertIn("<@7>", content)
        self.assertIn("boire de l'eau", content)
        self.assertEqual([u.id for u in kw["allowed_mentions"].users], [7])
        self.assertFalse(kw["allowed_mentions"].everyone)
        r = cog._rappels["1"]
        self.assertTrue(r["envoye"])
        self.assertEqual(r["envoye_a"], 110.0)
        self.assertGreaterEqual(bot.state.writes, 1)
        # le marquage a été persisté : l'état du bot contient envoye=True
        self.assertTrue(bot.state.d["rappels"]["1"]["envoye"])
        self.assertEqual(bot.audit.lines[-1]["action"], "rappel-envoi")

    def test_marquage_meme_si_envoi_explose(self):
        """Le rappel est marqué AVANT l'envoi : un salon qui lève ne le rejoue pas."""
        bot = FauxBot()
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100)}
        bot.users[7] = FauxUser(7, refuse=True)          # DM refusé aussi
        with Horloge(110.0):
            asyncio.run(cog.tick())
        self.assertTrue(cog._rappels["1"]["envoye"])
        self.assertIn("erreur", cog._rappels["1"])
        # second tick : rien ne repart
        with Horloge(120.0):
            asyncio.run(cog.tick())
        self.assertEqual(bot.audit.lines[-1]["result"], "échec salon+DM")
        self.assertEqual(sum(1 for l in bot.audit.lines if l["action"] == "rappel-envoi"), 1)

    def test_pas_de_doublon_au_second_tick(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100)}
        with Horloge(110.0):
            asyncio.run(cog.tick())
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 1)

    def test_salon_disparu_repli_dm(self):
        bot = FauxBot()                                   # aucun salon connu
        user = FauxUser(7)
        bot.users[7] = user
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100, salon=999)}
        with Horloge(110.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(user.sent), 1)
        self.assertIn("indisponible", user.sent[0][0])
        self.assertIn("boire de l'eau", user.sent[0][0])
        self.assertTrue(cog._rappels["1"]["envoye"])
        self.assertIn("ok dm", bot.audit.lines[-1]["result"])

    def test_salon_refuse_ecriture_repli_dm(self):
        bot = FauxBot()
        bot.channels[555] = FauxChannel(555, refuse=True)
        user = FauxUser(7)
        bot.users[7] = user
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100)}
        with Horloge(110.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(user.sent), 1)

    def test_mode_dm_direct(self):
        bot = FauxBot()
        user = FauxUser(7)
        bot.users[7] = user
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100, mode="dm", salon=None)}
        with Horloge(110.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(user.sent), 1)
        self.assertFalse(user.sent[0][0].startswith("(le salon"))

    def test_recurrence_reprogramme_meme_id(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100, repeter=3600)}
        with Horloge(110.0):
            asyncio.run(cog.tick())
        r = cog._rappels["1"]
        self.assertFalse(r["envoye"])                    # toujours actif
        self.assertEqual(r["echeance"], 3700.0)          # +1 h
        self.assertEqual(r["envois"], 1)
        self.assertEqual(len(ch.sent), 1)
        self.assertIn("🔁", ch.sent[0][0])
        # pas encore l'heure : rien
        with Horloge(3000.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 1)
        # échéance suivante : repart, et l'échéance d'après est calculée
        with Horloge(3700.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 2)
        self.assertEqual(cog._rappels["1"]["echeance"], 7300.0)

    def test_recurrence_apres_longue_coupure_un_seul_envoi(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._rappels = {"1": _r("1", echeance=100, repeter=3600)}
        with Horloge(100 + 10 * 3600 + 1.0):
            asyncio.run(cog.tick())
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 1)
        self.assertIn("prévu", ch.sent[0][0])            # dit qu'il est en retard
        self.assertGreater(cog._rappels["1"]["echeance"], 100 + 10 * 3600 + 1.0)

    def test_lot_maximum_20_par_tick(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._rappels = {str(i): _r(str(i), echeance=i) for i in range(35)}
        with Horloge(1000.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 20)
        with Horloge(1015.0):
            asyncio.run(cog.tick())
        self.assertEqual(len(ch.sent), 35)

    def test_purge_des_envoyes_de_plus_de_7_jours(self):
        bot = FauxBot()
        cog = _cog(bot)
        now = 30 * 86400.0
        cog._rappels = {"a": _r("a", envoye=True, envoye_a=now - 8 * 86400),
                        "b": _r("b", envoye=True, envoye_a=now - 1 * 86400),
                        "c": _r("c", echeance=now + 100)}
        with Horloge(now):
            asyncio.run(cog.tick())
        self.assertEqual(set(cog._rappels), {"b", "c"})
        self.assertEqual(set(bot.state.d["rappels"]), {"b", "c"})

    def test_etat_recharge_au_demarrage(self):
        bot = FauxBot()
        bot.state.d["rappels"] = {"9": _r("9", echeance=5)}
        cog = _cog(bot)
        self.assertIn("9", cog._rappels)
        self.assertEqual(cog._nouvel_id(), "10")

    def test_texte_nettoye(self):
        self.assertEqual(R._clean("a`b\n\n  c"), "a'b c")
        self.assertEqual(len(R._clean("x" * 900)), R.MESSAGE_MAX)


if __name__ == "__main__":
    unittest.main()
