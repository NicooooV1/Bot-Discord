"""Tests de la mémoire par nœud des rapports quotidiens Aveyron (Avy.post_rapports).

POURQUOI CES TESTS : le 2026-08-31, `llm` injoignable + rattrapage toutes les 5 min +
compteur d'essais remis à zéro à chaque redémarrage = #rapports-nas et #rapports-ms01
inondés de doublons (« un rapport quotidien toutes les 10 minutes » — Nico). Le correctif
mémorise dans state quels nœuds ont déjà leur rapport DU JOUR. On verrouille ici :
zéro doublon sur retentative, complétion différée quand le nœud raté revient, survie au
redémarrage (state partagé), et remise à zéro au changement de jour.
"""
import asyncio
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs.avy import Avy  # noqa: E402

NODES = ["nas", "ms01", "llm"]


class FauxState:
    def __init__(self):
        self.d = {"prov": {"avy_sup": {n: {"rapports": i + 1}
                                       for i, n in enumerate(NODES)}}}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FauxSalon:
    def __init__(self):
        self.envois = 0

    async def send(self, *a, **kw):
        self.envois += 1


class FauxPve:
    avy_enabled = True

    def avy_nodes(self):
        return list(NODES)


class FauxCfg:
    avy_enabled = True
    avy_suffix = "avy"


class FauxBot:
    def __init__(self, state):
        self.cfg = FauxCfg()
        self.state = state
        self.pve = FauxPve()
        self.salons = {i + 1: FauxSalon() for i in range(len(NODES))}

    def get_cog(self, name):
        return None

    def get_channel(self, cid):
        return self.salons.get(cid)


def _cog(bot, morts=()):
    cog = Avy(bot)

    async def rapport(node):
        if node in morts:
            raise RuntimeError("injoignable")
        return object()
    cog.build_rapport = rapport
    return cog


class TestRapportsParNoeud(unittest.TestCase):
    def _salon(self, bot, node):
        return bot.salons[NODES.index(node) + 1]

    def test_retentative_sans_doublon_puis_completion(self):
        bot = FauxBot(FauxState())
        cog = _cog(bot, morts={"llm"})
        # 1er essai : llm échoue -> journée PAS faite, nas/ms01 publiés une fois
        self.assertEqual(asyncio.run(cog.post_rapports()), 0)
        self.assertEqual(self._salon(bot, "nas").envois, 1)
        self.assertEqual(self._salon(bot, "ms01").envois, 1)
        # 2e essai (rattrapage 5 min) : AUCUN doublon
        self.assertEqual(asyncio.run(cog.post_rapports()), 0)
        self.assertEqual(self._salon(bot, "nas").envois, 1)
        self.assertEqual(self._salon(bot, "ms01").envois, 1)
        # llm revient : seul son rapport part, et la journée est marquée faite
        cog2 = _cog(bot)
        self.assertTrue(asyncio.run(cog2.post_rapports()))
        self.assertEqual(self._salon(bot, "llm").envois, 1)
        self.assertEqual(self._salon(bot, "nas").envois, 1)

    def test_redemarrage_du_bot_sans_doublon(self):
        state = FauxState()
        bot = FauxBot(state)
        asyncio.run(_cog(bot, morts={"llm"}).post_rapports())
        # « redémarrage » : nouveau bot, nouveau cog, MÊME state persisté
        bot2 = FauxBot(state)
        self.assertEqual(asyncio.run(_cog(bot2, morts={"llm"}).post_rapports()), 0)
        self.assertEqual(self._salon(bot2, "nas").envois, 0)
        self.assertEqual(self._salon(bot2, "ms01").envois, 0)

    def test_tout_deja_publie_renvoie_complet_sans_envoi(self):
        state = FauxState()
        state.set("avy_rapports", {"day": datetime.date.today().isoformat(),
                                   "sent": sorted(NODES)})
        bot = FauxBot(state)
        self.assertTrue(asyncio.run(_cog(bot).post_rapports()))
        self.assertEqual(sum(s.envois for s in bot.salons.values()), 0)

    def test_nouveau_jour_remet_a_zero(self):
        state = FauxState()
        state.set("avy_rapports", {"day": "2000-01-01", "sent": sorted(NODES)})
        bot = FauxBot(state)
        self.assertTrue(asyncio.run(_cog(bot).post_rapports()))
        self.assertEqual(sum(s.envois for s in bot.salons.values()), 3)
        self.assertEqual(state.get("avy_rapports")["day"],
                         datetime.date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
