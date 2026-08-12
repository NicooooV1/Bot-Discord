"""Tests du cog /dist (whitelist du serveur de distribution Fronote).

Invariants ancrés ici :
  - le contrat footer « ip: <x> » entre la notification et ses boutons persistants
    (si l'un des deux formats dérive, les boutons deviennent muets — même classe de
    défaut que le Snooze des alertes) ;
  - l'agrégation par IP et l'avancée du curseur (un refus relu deux fois = spam) ;
  - l'anti-spam (cooldown de notification + « Ignorer » 24 h) ;
  - le parsing de la nouvelle config DIST_* (des deux vides = cog inactif).
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

from bot.core.config import Config  # noqa: E402
from bot.cogs import dist as dist_mod  # noqa: E402
from bot.cogs.dist import Dist, _ip_from_msg, _valid_ip  # noqa: E402


class FauxState:
    def __init__(self):
        self.d = {}

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v


class FauxBot:
    def __init__(self):
        self.state = FauxState()
        self.cfg = None


class FauxMessage:
    def __init__(self, embeds):
        self.embeds = embeds


def _cog():
    c = Dist.__new__(Dist)      # sans __init__ de commands.Cog (pas de boucle ici)
    c.bot = FauxBot()
    return c


# --------------------------------------------------------------------------- IP
class TestValidIp(unittest.TestCase):
    def test_v4_v6_ok(self):
        self.assertTrue(_valid_ip("203.0.113.5"))
        self.assertTrue(_valid_ip("2001:db8::1"))

    def test_rejets(self):
        for bad in ("", "abc", "203.0.113", "203.0.113.5; rm -rf", "1.2.3.4/24"):
            self.assertFalse(_valid_ip(bad), bad)


# --------------------------------------------------------------------------- footer
class TestContratFooter(unittest.TestCase):
    """L'embed de notification et _ip_from_msg doivent parler le MÊME format."""

    def test_aller_retour(self):
        cog = _cog()
        emb = cog._refused_embed("203.0.113.9", {
            "count": 3, "kinds": {"redeem", "download"},
            "first": "2026-08-12 10:00:00", "last": "2026-08-12 10:05:00", "max_id": 7})
        ip = _ip_from_msg(FauxMessage([emb]))
        self.assertEqual(ip, "203.0.113.9")

    def test_footer_sans_ip(self):
        emb = discord.Embed(title="x")
        emb.set_footer(text="alerte: ipmi_temp [warn]")
        self.assertIsNone(_ip_from_msg(FauxMessage([emb])))

    def test_message_sans_embed(self):
        self.assertIsNone(_ip_from_msg(FauxMessage([])))

    def test_ip_forgee_dans_footer_invalide(self):
        emb = discord.Embed(title="x")
        emb.set_footer(text="ip: pas-une-ip · serveur dist CT122")
        self.assertIsNone(_ip_from_msg(FauxMessage([emb])))


# --------------------------------------------------------------------------- agrégation
class TestAgregation(unittest.TestCase):
    def test_regroupe_par_ip_et_curseur(self):
        rows = [
            {"id": 1, "ip": "203.0.113.5", "kind": "redeem", "at": "t1"},
            {"id": 2, "ip": "203.0.113.5", "kind": "download", "at": "t2"},
            {"id": 5, "ip": "198.51.100.7", "kind": "redeem", "at": "t3"},
        ]
        agg = Dist._aggregate(rows)
        self.assertEqual(set(agg), {"203.0.113.5", "198.51.100.7"})
        a = agg["203.0.113.5"]
        self.assertEqual(a["count"], 2)
        self.assertEqual(a["kinds"], {"redeem", "download"})
        self.assertEqual((a["first"], a["last"]), ("t1", "t2"))
        # le curseur global = max des max_id → aucun refus relu au tour suivant
        self.assertEqual(max(x["max_id"] for x in agg.values()), 5)

    def test_journal_vide(self):
        self.assertEqual(Dist._aggregate([]), {})


# --------------------------------------------------------------------------- anti-spam
class TestAntiSpam(unittest.TestCase):
    def test_cooldown_notification(self):
        cog = _cog()
        now = time.time()
        self.assertFalse(cog._suppressed("203.0.113.5", now))
        cog._remember_notified(["203.0.113.5"], now)
        self.assertTrue(cog._suppressed("203.0.113.5", now + 10))
        self.assertFalse(cog._suppressed("203.0.113.5",
                                         now + dist_mod.NOTIFY_COOLDOWN + 1))

    def test_ignorer_24h(self):
        cog = _cog()
        now = time.time()
        cog.mark_ignored("198.51.100.7")
        self.assertTrue(cog._suppressed("198.51.100.7", now + 3600))
        self.assertFalse(cog._suppressed("198.51.100.7",
                                         now + dist_mod.IGNORE_SECONDS + 1))

    def test_purge_des_silences_expires(self):
        cog = _cog()
        cog.bot.state.set("dist_ignored",
                          {"192.0.2.1": time.time() - dist_mod.IGNORE_SECONDS - 10})
        cog.mark_ignored("192.0.2.2")
        self.assertNotIn("192.0.2.1", cog.bot.state.get("dist_ignored"))
        self.assertIn("192.0.2.2", cog.bot.state.get("dist_ignored"))


# --------------------------------------------------------------------------- config
class TestCurseurRefus(unittest.TestCase):
    """Le curseur ne doit avancer QUE si le lot a été réellement posté (sinon les refus
    seraient perdus à jamais — salon absent, 403, catégorie…)."""

    def _cog_with_api(self, refused_rows, allowed=False, chan=None):
        import asyncio
        cog = _cog()
        cog.bot.cfg = type("C", (), {"dist_alert_channel_id": 0, "alert_channel_id": 999})()

        async def fake_api(method, action, data=None, params=None, timeout=15):
            if action == "admin_refused":
                return {"status": "ok", "refused": refused_rows}
            if action == "admin_list":
                return {"status": "ok", "ip": {"allowed": allowed}}
            return {"status": "ok"}
        cog.api = fake_api
        cog.bot.get_channel = lambda cid: chan
        cog._refused_view = object()
        return cog, asyncio

    def test_curseur_non_avance_si_salon_absent(self):
        rows = [{"id": 7, "ip": "203.0.113.5", "kind": "redeem", "at": "t"}]
        cog, aio = self._cog_with_api(rows, chan=None)   # salon introuvable
        aio.run(cog.refus_watch())
        self.assertIsNone(cog.bot.state.get("dist_refused_cursor"))  # PAS avancé

    def test_curseur_avance_si_tout_deja_whiteliste(self):
        rows = [{"id": 7, "ip": "203.0.113.5", "kind": "redeem", "at": "t"}]
        cog, aio = self._cog_with_api(rows, allowed=True, chan=None)
        aio.run(cog.refus_watch())
        self.assertEqual(cog.bot.state.get("dist_refused_cursor"), 7)  # rien à poster → OK

    def test_curseur_avance_apres_envoi_reussi(self):
        sent = []

        class Chan:
            async def send(self, *a, **k):
                sent.append((a, k))
        rows = [{"id": 9, "ip": "203.0.113.5", "kind": "redeem", "at": "t"}]
        cog, aio = self._cog_with_api(rows, chan=Chan())
        aio.run(cog.refus_watch())
        self.assertEqual(cog.bot.state.get("dist_refused_cursor"), 9)
        self.assertEqual(len(sent), 1)

    def test_curseur_non_avance_si_envoi_echoue(self):
        class Chan:
            async def send(self, *a, **k):
                raise RuntimeError("403 Missing Access")  # pas une HTTPException
        rows = [{"id": 9, "ip": "203.0.113.5", "kind": "redeem", "at": "t"}]
        cog, aio = self._cog_with_api(rows, chan=Chan())
        aio.run(cog.refus_watch())
        self.assertIsNone(cog.bot.state.get("dist_refused_cursor"))  # lot rejouable


class TestConfigDist(unittest.TestCase):
    def _cfg(self, **extra):
        env = {"DISCORD_TOKEN": "x", "GUILD_ID": "100"}
        env.update(extra)
        return Config(env)

    def test_defauts(self):
        cfg = self._cfg()
        self.assertEqual(cfg.dist_url, "")
        self.assertEqual(cfg.dist_admin_token, "")
        self.assertEqual(cfg.dist_poll_seconds, 120)

    def test_valeurs_et_slash_final(self):
        cfg = self._cfg(DIST_URL="http://10.3.20.122/", DIST_ADMIN_TOKEN="tok",
                        DIST_POLL_SECONDS="45", DIST_ALERT_CHANNEL_ID="123")
        self.assertEqual(cfg.dist_url, "http://10.3.20.122")   # pas de double //
        self.assertEqual(cfg.dist_admin_token, "tok")
        self.assertEqual(cfg.dist_poll_seconds, 45)
        self.assertEqual(cfg.dist_alert_channel_id, 123)

    def test_plancher_poll(self):
        cfg = self._cfg(DIST_POLL_SECONDS="5")
        self.assertEqual(cfg.dist_poll_seconds, 30)

    def test_enabled_exige_les_deux(self):
        bot = FauxBot()
        cog = Dist.__new__(Dist)
        cog.bot = bot
        for url, tok, want in (("", "", False), ("http://x", "", False),
                               ("", "t", False), ("http://x", "t", True)):
            bot.cfg = self._cfg(DIST_URL=url, DIST_ADMIN_TOKEN=tok)
            self.assertIs(cog.enabled, want, (url, tok))


if __name__ == "__main__":
    unittest.main()
