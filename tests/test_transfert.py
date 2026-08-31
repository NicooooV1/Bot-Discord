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


class TestSondeProgressionReelle(unittest.TestCase):
    """La progression affichée doit venir des octets RÉELS à l'arrivée (df), pas du
    compteur de session rsync qui repart de 0 à chaque redémarrage (Nico 2026-08-31)."""

    SORTIE = ("etat=activating\nresultat=\n"
              "progres=  11.76G   0%   44.88MB/s    0:04:09\n"
              "chk=ir-chk=1010/1238\n"
              "libre=4700000000000\nutilise=767000000000\n"
              "total_reel=4180000000000\nhote=10.0.10.10\nchemin=mgmt\n")

    def test_reel_prime_sur_le_compteur_de_session(self):
        o = T.parse_sonde(self.SORTIE)
        # 767 Go réels, PAS les 12,6 Go du compteur rsync
        self.assertEqual(o["transfere_reel"], 767_000_000_000)
        self.assertAlmostEqual(o["pct_fin"], 767 / 4180 * 100, places=1)
        self.assertEqual(o["total"], 4_180_000_000_000)
        self.assertEqual(o["reste"], 4_180_000_000_000 - 767_000_000_000)
        # base réelle : le % ne dépend plus du scan incrémental (pas de « provisoire »)
        self.assertIs(o["analyse"], False)
        # l'embed montre le RÉEL, pas octets
        emb = T.embed_transfert(o, FakeCfg())
        transf = next(f.value for f in emb.fields if f.name == "Transféré")
        self.assertNotIn("12", transf)                    # pas 12,6 Gio
        self.assertTrue(any(f.name.startswith("Progression") for f in emb.fields))

    def test_repli_si_df_indisponible(self):
        # sans utilise/total_reel (df en échec) ET sans cache, on retombe sur l'ancienne
        # estimation par le compteur de session rsync
        sortie = ("etat=activating\n"
                  "progres=  50.0G   0%   40.00MB/s    1:00:00\nchk=to-chk=10/100\n")
        o = T.parse_sonde(sortie)
        self.assertEqual(o["transfere_reel"], o["octets"])
        self.assertIsNotNone(o["octets"])

    def test_cache_lisse_un_df_qui_expire(self):
        # df cible NFS expiré ce cycle (pas de utilise) : on garde la dernière progression
        # RÉELLE via le cache plutôt que d'osciller vers le compteur de session rsync
        sortie = ("etat=activating\n"
                  "progres=  21.9G   0%   50.00MB/s    2:00:00\n"
                  "total_reel=4180000000000\n")     # source locale OK, cible NFS expirée
        o = T.parse_sonde(sortie, reel_cache=(767_000_000_000, 4_180_000_000_000))
        self.assertEqual(o["transfere_reel"], 767_000_000_000)   # pas 21.9G
        self.assertAlmostEqual(o["pct_fin"], 767 / 4180 * 100, places=1)


class TestDisque(unittest.TestCase):
    """Vitesse & saturation du disque source dans l'embed (Nico 2026-08-31)."""

    def _etat(self, util):
        return T.parse_sonde(
            "etat=activating\nprogres=  50.0G   0%   40.00MB/s    1:00:00\n"
            "utilise=767000000000\ntotal_reel=4180000000000\n"
            f"disque=sda\ndisk_rd=52000000\ndisk_wr=1000000\ndisk_util={util}\n")

    def test_parse_disque(self):
        o = self._etat(35)
        self.assertEqual(o["disque"], "sda")
        self.assertEqual(o["disk_rd"], 52000000)
        self.assertEqual(o["disk_util"], 35)

    def test_verdict_non_sature(self):
        emb = T.embed_transfert(self._etat(35), FakeCfg())
        champ = next(f for f in emb.fields if f.name.startswith("💽 Disque"))
        self.assertIn("non saturé", champ.value)
        self.assertIn("35 %", champ.value)

    def test_verdict_sature(self):
        emb = T.embed_transfert(self._etat(96), FakeCfg())
        champ = next(f for f in emb.fields if f.name.startswith("💽 Disque"))
        self.assertIn("saturé", champ.value)
        self.assertNotIn("non saturé", champ.value)

    def test_champ_absent_si_pas_de_mesure(self):
        # df/diskstats indisponibles : pas de champ disque (pas de « 0 % » inventé)
        o = T.parse_sonde("etat=activating\nprogres=  50.0G   0%   40.00MB/s    1:00:00\n")
        emb = T.embed_transfert(o, FakeCfg())
        self.assertFalse(any(f.name.startswith("💽 Disque") for f in emb.fields))


if __name__ == "__main__":
    unittest.main()
