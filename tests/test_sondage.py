"""Tests du cog `sondage` — logique pure (options, votes, rendu) et clôture sur faux bot.

POURQUOI CES TESTS : un vote perdu ou compté deux fois décrédibilise le sondage ; un
sondage « anonyme » qui laisse fuir les votants trahit une promesse ; une clôture
programmée qui se rejoue au redémarrage double l'audit et réécrit le message ; des
boutons non ré-enregistrés au redémarrage rendent le sondage muet (« interaction
échouée »). Aucun réseau, aucune API Discord réelle : les vues sont construites sous
`asyncio.run` parce que `discord.ui.View` exige une boucle en cours.
"""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.cogs import sondage as S  # noqa: E402
from bot.core.gates import GatedView  # noqa: E402


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


class FauxMessage:
    def __init__(self, mid, fail=False):
        self.id = mid
        self.edits = []
        self.fail = fail

    async def edit(self, **kw):
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")
        self.edits.append(kw)


class FauxChannel:
    def __init__(self, cid):
        self.id = cid
        self.messages = {}

    async def fetch_message(self, mid):
        m = self.messages.get(mid)
        if m is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "absent")
        return m


class FauxBot:
    def __init__(self):
        self.state = FauxState()
        self.audit = FauxAudit()
        self.cfg = SimpleNamespace(guild_id=100, server_key="R820", admin_ids=[],
                                   admin_role_ids=[], read_role_ids=[], gestion_servers={})
        self.intents = SimpleNamespace(members=False, message_content=False)
        self.channels = {}
        self.views = []

    def get_channel(self, cid):
        return self.channels.get(cid)

    async def fetch_channel(self, cid):
        raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "absent")

    def add_view(self, view, *, message_id=None):
        self.views.append((view, message_id))


def _s(sid="1", options=("Oui", "Non", "Sans avis"), multiple=False, anonyme=False,
       fin=None, ferme=False, votes=None, message_id=900, salon=555):
    return {"id": sid, "guild_id": 100, "salon_id": salon, "message_id": message_id,
            "createur_id": 7, "question": "Pizza ce soir ?", "options": list(options),
            "multiple": multiple, "anonyme": anonyme, "cree_a": 0.0, "fin_a": fin,
            "ferme": ferme, "votes": dict(votes or {})}


def _cog(bot=None):
    return S.Sondage(bot or FauxBot())


class Horloge:
    def __init__(self, t):
        self.t = t

    def __enter__(self):
        self._old = S._now
        S._now = lambda: self.t
        return self

    def __exit__(self, *a):
        S._now = self._old


# --------------------------------------------------------------------------- options
class TestOptions(unittest.TestCase):
    def test_separateur_et_nettoyage(self):
        self.assertEqual(S.parse_options(" Oui | Non |  | Peut-être "),
                         ["Oui", "Non", "Peut-être"])

    def test_bornes_2_a_10(self):
        with self.assertRaises(ValueError):
            S.parse_options("Seul")
        with self.assertRaises(ValueError):
            S.parse_options("|".join(str(i) for i in range(11)))
        self.assertEqual(len(S.parse_options("|".join(str(i) for i in range(10)))), 10)

    def test_libelle_tronque_a_80_et_backticks(self):
        opts = S.parse_options("x" * 200 + "|`b`")
        self.assertEqual(len(opts[0]), 80)
        self.assertEqual(opts[1], "'b'")


# --------------------------------------------------------------------------- votes
class TestVotes(unittest.TestCase):
    def test_choix_unique_toggle_et_remplacement(self):
        s = _s()
        self.assertEqual(S.appliquer_vote(s, 42, 0), "ajoute")
        self.assertEqual(s["votes"]["42"], [0])
        self.assertEqual(S.appliquer_vote(s, 42, 1), "remplace")
        self.assertEqual(s["votes"]["42"], [1])          # l'ancien a disparu
        self.assertEqual(S.appliquer_vote(s, 42, 1), "retire")
        self.assertNotIn("42", s["votes"])               # toggle → plus de voix
        self.assertEqual(S.decompte(s), [0, 0, 0])

    def test_choix_multiple_independant(self):
        s = _s(multiple=True)
        S.appliquer_vote(s, 42, 0)
        S.appliquer_vote(s, 42, 2)
        self.assertEqual(s["votes"]["42"], [0, 2])
        self.assertEqual(S.appliquer_vote(s, 42, 0), "retire")
        self.assertEqual(s["votes"]["42"], [2])
        self.assertEqual(S.decompte(s), [0, 0, 1])

    def test_votes_par_utilisateur_pas_par_option(self):
        s = _s()
        S.appliquer_vote(s, 1, 0)
        S.appliquer_vote(s, 2, 0)
        S.appliquer_vote(s, 3, 1)
        self.assertEqual(S.decompte(s), [2, 1, 0])
        self.assertEqual(S.gagnants(s), {0})
        S.appliquer_vote(s, 3, 0)                        # 3 change d'avis → 3/0/0
        self.assertEqual(S.decompte(s), [3, 0, 0])

    def test_gagnants_ex_aequo_et_zero(self):
        self.assertEqual(S.gagnants(_s()), set())
        s = _s(votes={"1": [0], "2": [1]})
        self.assertEqual(S.gagnants(s), {0, 1})

    def test_index_hors_bornes_ignore_au_decompte(self):
        s = _s(votes={"1": [7]})
        self.assertEqual(S.decompte(s), [0, 0, 0])


# --------------------------------------------------------------------------- rendu
class TestRendu(unittest.TestCase):
    def test_barre_20_segments(self):
        self.assertEqual(S.barre(0, 0), "░" * 20 + " 0%")
        self.assertEqual(S.barre(1, 2), "█" * 10 + "░" * 10 + " 50%")
        self.assertEqual(S.barre(3, 3), "█" * 20 + " 100%")

    def test_embed_ouvert(self):
        s = _s(votes={"1": [0], "2": [0], "3": [1]}, fin=5000.0)
        emb = S.build_embed(s)
        self.assertTrue(emb.title.startswith("📊 Pizza"))
        self.assertIn("2 voix", emb.description)
        self.assertIn("67%", emb.description)
        self.assertNotIn("🏆", emb.description)          # pas de gagnant avant la fin
        self.assertIn("3 votants", emb.footer.text)
        self.assertIn("<t:5000:R>", emb.fields[0].value)

    def test_embed_ferme_marque_les_gagnants(self):
        s = _s(votes={"1": [0], "2": [0], "3": [1]}, ferme=True)
        s["ferme_a"] = 10
        s["ferme_par"] = "programmé"
        emb = S.build_embed(s)
        self.assertIn("[Terminé]", emb.title)
        lignes = emb.description.split("\n")
        self.assertIn("🏆", lignes[0])
        self.assertNotIn("🏆", lignes[2])
        self.assertIn("programmé", emb.fields[0].value)

    def test_embed_anonyme_ne_montre_aucun_votant(self):
        s = _s(anonyme=True, votes={"4242": [0]})
        emb = S.build_embed(s)
        self.assertNotIn("4242", emb.description)
        self.assertIn("anonyme", emb.footer.text)

    def test_multiple_pourcentage_par_votant(self):
        s = _s(multiple=True, votes={"1": [0, 1], "2": [0]})
        emb = S.build_embed(s)
        self.assertIn("100%", emb.description)           # option 0 : 2 votants sur 2
        self.assertIn("50%", emb.description)


# --------------------------------------------------------------------------- vue
class TestVue(unittest.TestCase):
    def _view(self, **kw):
        async def go():
            return S.SondageView(_cog(), _s(**kw))
        return asyncio.run(go())

    def test_est_une_gated_view_exemptee_avec_raison(self):
        self.assertTrue(issubclass(S.SondageView, GatedView))
        self.assertIsNone(S.SondageView.gate)
        self.assertTrue(S.SondageView.gate_reason)

    def test_custom_ids_persistants(self):
        v = self._view()
        self.assertIsNone(v.timeout)
        ids = [c.custom_id for c in v.children]
        self.assertEqual(ids, ["sondage:1:0", "sondage:1:1", "sondage:1:2", "sondage:1:qui"])

    def test_anonyme_sans_bouton_qui_a_vote(self):
        ids = [c.custom_id for c in self._view(anonyme=True).children]
        self.assertNotIn("sondage:1:qui", ids)
        self.assertEqual(len(ids), 3)

    def test_dix_options_sur_deux_rangees(self):
        v = self._view(options=[f"o{i}" for i in range(10)])
        rows = {c.custom_id: c.row for c in v.children}
        self.assertEqual(rows["sondage:1:4"], 0)
        self.assertEqual(rows["sondage:1:5"], 1)
        self.assertEqual(rows["sondage:1:qui"], 2)

    def test_autre_guild_refuse(self):
        v = self._view()
        itx = SimpleNamespace(client=FauxBot(), guild_id=999, user=SimpleNamespace(id=1))
        self.assertFalse(asyncio.run(v.interaction_check(itx)))
        itx.guild_id = 100
        self.assertTrue(asyncio.run(v.interaction_check(itx)))


# --------------------------------------------------------------------------- cog
class TestCog(unittest.TestCase):
    def test_reenregistrement_des_vues_ouvertes_seulement(self):
        bot = FauxBot()
        bot.state.d["sondages"] = {"1": _s("1", message_id=901),
                                   "2": _s("2", ferme=True, message_id=902),
                                   "3": _s("3", message_id=None)}
        cog = _cog(bot)

        async def go():
            return cog.enregistrer_vues()
        self.assertEqual(asyncio.run(go()), 1)
        self.assertEqual([mid for _, mid in bot.views], [901])
        self.assertIsInstance(bot.views[0][0], S.SondageView)
        self.assertIn("1", cog._vues)

    def test_cloture_programmee(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        msg = FauxMessage(900)
        ch.messages[900] = msg
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._sondages = {"1": _s("1", fin=1000.0, votes={"1": [0]}),
                         "2": _s("2", fin=5000.0)}
        with Horloge(1001.0):
            asyncio.run(cog.tick())
        s = cog._sondages["1"]
        self.assertTrue(s["ferme"])
        self.assertEqual(s["ferme_par"], "programmé")
        self.assertTrue(s["rendu_final"])
        self.assertFalse(cog._sondages["2"]["ferme"])
        self.assertEqual(len(msg.edits), 1)
        self.assertIsNone(msg.edits[0]["view"])              # boutons retirés
        self.assertIn("🏆", msg.edits[0]["embed"].description)
        self.assertEqual([l["action"] for l in bot.audit.lines], ["sondage-fermer"])
        self.assertTrue(bot.state.d["sondages"]["1"]["ferme"])

    def test_cloture_idempotente_au_redemarrage(self):
        """ferme=True est posé AVANT l'édition : si l'édition échoue (bot tué, API KO),
        le redémarrage ne referme pas (0 audit en plus) mais RETENTE le rendu."""
        bot = FauxBot()
        ch = FauxChannel(555)
        msg = FauxMessage(900, fail=True)
        ch.messages[900] = msg
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._sondages = {"1": _s("1", fin=1000.0)}
        with Horloge(1001.0):
            asyncio.run(cog.tick())
        self.assertTrue(cog._sondages["1"]["ferme"])
        self.assertFalse(cog._sondages["1"]["rendu_final"])
        self.assertEqual(len(bot.audit.lines), 1)
        # « redémarrage » : nouveau cog depuis l'état persisté, l'API revient
        bot2 = FauxBot()
        bot2.state.d["sondages"] = bot.state.d["sondages"]
        ch2 = FauxChannel(555)
        msg2 = FauxMessage(900)
        ch2.messages[900] = msg2
        bot2.channels[555] = ch2
        cog2 = _cog(bot2)
        async def demarrage():
            cog2.enregistrer_vues()
            await cog2.tick()
        with Horloge(1100.0):
            asyncio.run(demarrage())
            asyncio.run(cog2.tick())
        self.assertEqual(bot2.views, [])                     # fermé : pas de vue
        self.assertEqual(len(bot2.audit.lines), 0)           # pas de second audit
        self.assertEqual(len(msg2.edits), 1)                 # rendu final fait UNE fois
        self.assertTrue(cog2._sondages["1"]["rendu_final"])
        self.assertEqual(cog2._sondages["1"]["ferme_a"], 1001.0)  # date d'origine

    def test_message_supprime_abandon_apres_essais(self):
        bot = FauxBot()
        cog = _cog(bot)
        cog._sondages = {"1": _s("1", fin=1000.0)}
        with Horloge(1001.0):
            for _ in range(S.RENDU_ESSAIS_MAX + 2):
                asyncio.run(cog.tick())
        self.assertTrue(cog._sondages["1"]["rendu_final"])
        self.assertEqual(cog._sondages["1"]["rendu_essais"], S.RENDU_ESSAIS_MAX)

    def test_fermeture_manuelle_puis_programmee_sans_doublon(self):
        bot = FauxBot()
        ch = FauxChannel(555)
        msg = FauxMessage(900)
        ch.messages[900] = msg
        bot.channels[555] = ch
        cog = _cog(bot)
        cog._sondages = {"1": _s("1", fin=1000.0)}
        with Horloge(900.0):
            asyncio.run(cog.fermer("1", "nico (7)"))
        with Horloge(1001.0):
            asyncio.run(cog.tick())
        self.assertEqual(cog._sondages["1"]["ferme_par"], "nico (7)")
        self.assertEqual(len(msg.edits), 1)
        self.assertEqual(len(bot.audit.lines), 1)

    def test_vote_sur_sondage_echu_refuse(self):
        cog = _cog()
        cog._sondages = {"1": _s("1", fin=1000.0)}
        rep = []

        class Resp:
            async def send_message(self, *a, **kw):
                rep.append((a, kw))

            async def edit_message(self, **kw):
                rep.append(("edit", kw))

        itx = SimpleNamespace(user=SimpleNamespace(id=42), response=Resp(),
                              followup=SimpleNamespace(send=Resp().send_message))
        with Horloge(2000.0):
            asyncio.run(cog.voter(itx, "1", 0))
        self.assertIn("terminé", rep[0][0][0])
        self.assertEqual(cog._sondages["1"]["votes"], {})

    def test_vote_edite_le_message_et_persiste(self):
        bot = FauxBot()
        cog = _cog(bot)
        cog._sondages = {"1": _s("1")}
        rep = []

        class Resp:
            async def send_message(self, *a, **kw):
                rep.append(("msg", a, kw))

            async def edit_message(self, **kw):
                rep.append(("edit", kw))

        async def fu(*a, **kw):
            rep.append(("followup", a, kw))

        itx = SimpleNamespace(user=SimpleNamespace(id=42), response=Resp(),
                              followup=SimpleNamespace(send=fu))
        with Horloge(10.0):
            asyncio.run(cog.voter(itx, "1", 2))
        self.assertEqual(cog._sondages["1"]["votes"], {"42": [2]})
        self.assertEqual(bot.state.d["sondages"]["1"]["votes"], {"42": [2]})
        self.assertEqual(rep[0][0], "edit")
        self.assertIn("1 voix", rep[0][1]["embed"].description)
        self.assertEqual(rep[1][0], "followup")
        self.assertTrue(rep[1][2]["ephemeral"])

    def test_qui_a_vote_anonyme_et_droits(self):
        bot = FauxBot()
        cog = _cog(bot)
        cog._sondages = {"1": _s("1", anonyme=True, votes={"42": [0]}),
                         "2": _s("2", votes={"42": [0]})}
        rep = []

        class Resp:
            async def send_message(self, *a, **kw):
                rep.append((a, kw))

        def itx(uid):
            return SimpleNamespace(user=SimpleNamespace(id=uid, roles=[]), response=Resp(),
                                   client=bot, guild=SimpleNamespace(owner_id=1),
                                   guild_id=100, channel=None)
        asyncio.run(cog.qui_a_vote(itx(7), "1"))          # créateur, mais anonyme
        self.assertIn("anonyme", rep[-1][0][0])
        asyncio.run(cog.qui_a_vote(itx(99), "2"))         # ni créateur ni M/O
        self.assertIn("Réservé", rep[-1][0][0])
        asyncio.run(cog.qui_a_vote(itx(7), "2"))          # créateur : liste
        emb = rep[-1][1]["embed"]
        self.assertIn("<@42>", emb.fields[0].value)
        self.assertIn("intent members", emb.footer.text)
        asyncio.run(cog.qui_a_vote(itx(1), "2"))          # propriétaire du guild = O
        self.assertIn("embed", rep[-1][1])

    def test_ids_incrementaux(self):
        cog = _cog()
        cog._sondages = {"3": _s("3"), "x": _s("x")}
        self.assertEqual(cog._nouvel_id(), "4")


if __name__ == "__main__":
    unittest.main()
