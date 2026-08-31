"""Tests du cog `transfert` — bouton Rafraîchir silencieux + reprise après flap.

POURQUOI CES TESTS (2026-08-31, retours Nico) :
  1. Le bouton Rafraîchir postait « ✅ Relevé rafraîchi » à chaque clic — un message
     inutile. Il ne doit RIEN poster en cas de succès (l'embed se met à jour tout seul),
     et n'afficher un message QUE si le relevé échoue.
  2. Un `systemctl restart` (changement de BWLIMIT) faisait clignoter le service hors des
     ETATS_ACTIFS : le cog postait aussitôt « 🔴 arrêté … salon supprimé dans 1h », resté
     sous un embed redevenu vert. Désormais : anti-rebond (2 relevés inactifs avant
     d'annoncer) + retrait de l'annonce quand le transfert repart.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs import transfert as T  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeState:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FakeCfg:
    transfert_enabled = False       # n'arme pas la boucle poll dans __init__
    node_ssh_key = ""
    transfert_keep_min = 60
    transfert_channel_name = "transfert-medias"
    transfert_total_hint = ""
    general_channel_id = 0
    transfert_source = "/mnt/media"
    transfert_dest = "/mnt/avy-media"
    transfert_poll_sec = 60


class FakeBot:
    def __init__(self):
        self.cfg = FakeCfg()
        self.state = FakeState()


class FakeMsg:
    def __init__(self, mid):
        self.id = mid
        self.deleted = False
        self.pinned = False

    async def delete(self):
        self.deleted = True

    async def edit(self, **kw):
        pass

    async def pin(self):
        self.pinned = True


class FakeChannel:
    def __init__(self, cid=1):
        self.id = cid
        self.name = "transfert-medias"
        self._next = 1000
        self.sent = []            # messages TEXTE envoyés (ch.send)
        self.by_id = {}

    async def send(self, content=None, **kw):
        self._next += 1
        m = FakeMsg(self._next)
        self.sent.append(content)
        self.by_id[m.id] = m
        return m

    async def fetch_message(self, mid):
        if mid in self.by_id:
            return self.by_id[mid]
        raise T.discord.NotFound(_Resp(), "nope")


class _Resp:
    status = 404
    reason = "Not Found"


class FakeGuild:
    def __init__(self, ch):
        self._ch = ch

    def get_channel(self, cid):
        return self._ch if self._ch and cid == self._ch.id else None


def _cog():
    cog = T.Transfert(FakeBot())
    # on court-circuite Discord : _salon rend notre faux salon, _publier ne fait rien
    return cog


_BASE = {"octets": None, "debit": "", "reste": None, "eta": None, "pct_fin": None,
         "analyse": None, "total": None, "libre": None, "hote": "", "chemin": "",
         "debut": "", "lignes": []}
ACTIF = {**_BASE, "etat": "activating", "resultat": "", "octets": 1}
INACTIF = {**_BASE, "etat": "failed", "resultat": "signal"}


def _prep():
    """Cog prêt à tester, Discord court-circuité. À appeler DANS une boucle (la View
    créée par __init__ exige un event loop en cours)."""
    cog = _cog()
    ch = FakeChannel(cid=7)
    guild = FakeGuild(ch)
    cog._etat = {"channel_id": 7}

    async def fake_salon(g, creer):
        return ch
    cog._salon = fake_salon

    async def fake_publier(c, emb):
        pass
    cog._publier = fake_publier
    return cog, ch, guild


class TestFlap(unittest.TestCase):
    def test_anti_rebond_pas_dannonce_au_1er_inactif(self):
        async def body():
            cog, ch, guild = _prep()
            await cog._appliquer(guild, INACTIF)
            self.assertEqual(ch.sent, [])                 # rien posté
            self.assertNotIn("fin_ts", cog._etat)         # pas de compte à rebours
            self.assertEqual(cog._etat.get("inactif_n"), 1)
        run(body())

    def test_annonce_au_2e_inactif_puis_retrait_a_la_reprise(self):
        async def body():
            cog, ch, guild = _prep()
            await cog._appliquer(guild, INACTIF)          # 1er : silencieux
            await cog._appliquer(guild, INACTIF)          # 2e : annonce
            self.assertEqual(len(ch.sent), 1)
            self.assertIn("arrêté", ch.sent[0])
            stop_id = cog._etat.get("stop_msg_id")
            self.assertIsNotNone(stop_id)
            self.assertIn("fin_ts", cog._etat)
            # reprise : l'annonce doit être SUPPRIMÉE et l'état nettoyé
            await cog._appliquer(guild, ACTIF)
            self.assertTrue(ch.by_id[stop_id].deleted)
            self.assertNotIn("stop_msg_id", cog._etat)
            self.assertNotIn("fin_ts", cog._etat)
            self.assertEqual(cog._etat.get("inactif_n"), 0)
        run(body())

    def test_suppression_salon_apres_delai(self):
        async def body():
            cog, ch, guild = _prep()
            supprime = []

            async def fake_supp(g):
                supprime.append(True)
            cog._supprimer = fake_supp
            await cog._appliquer(guild, INACTIF)
            await cog._appliquer(guild, INACTIF)          # fin_ts posé « maintenant »
            cog._etat["fin_ts"] = 1.0                      # délai écoulé (0 serait falsy)
            await cog._appliquer(guild, INACTIF)
            self.assertTrue(supprime)
        run(body())


class FakeResponse:
    def __init__(self):
        self.deferred_kw = None

    async def defer(self, **kw):
        self.deferred_kw = kw


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append(content)


class FakeItx:
    def __init__(self):
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeCog:
    RAF = 10

    def __init__(self, ok=True, recent=False):
        import time
        self._dernier_releve = time.time() if recent else 0.0
        self._ok = ok
        self.relev, = (0,)
        self.appels = 0

    async def relever(self):
        self.appels += 1
        return self._ok


class TestBouton(unittest.TestCase):
    def test_succes_silencieux(self):
        async def body():
            cog = FakeCog(ok=True)
            itx = FakeItx()
            await T.TransfertRefreshView(cog).rafraichir.callback(itx)
            self.assertEqual(itx.followup.sent, [])       # AUCUN message
            self.assertEqual(cog.appels, 1)               # a bien relevé
            # accusé silencieux (defer sans thinking)
            self.assertNotIn("thinking", itx.response.deferred_kw or {})
        run(body())

    def test_erreur_affiche_message(self):
        async def body():
            cog = FakeCog(ok=False)
            itx = FakeItx()
            await T.TransfertRefreshView(cog).rafraichir.callback(itx)
            self.assertEqual(len(itx.followup.sent), 1)
            self.assertIn("injoignable", itx.followup.sent[0])
        run(body())

    def test_anti_rafale_ne_resonde_pas_et_reste_muet(self):
        async def body():
            cog = FakeCog(ok=True, recent=True)
            itx = FakeItx()
            await T.TransfertRefreshView(cog).rafraichir.callback(itx)
            self.assertEqual(cog.appels, 0)               # pas de nouvelle sonde
            self.assertEqual(itx.followup.sent, [])       # et rien posté
        run(body())


if __name__ == "__main__":
    unittest.main()
