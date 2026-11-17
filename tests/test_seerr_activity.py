"""Invariants des journaux médias (2026-08-24) : le format des lignes Jellyseerr et
les helpers d'enrichissement Jellyfin (timecode) sont purs — on les fige ici."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.cogs.seerr_activity import _fmt_media, _fmt_request  # noqa: E402
from bot.cogs.jellyfin_activity import _hms, _RX_IP  # noqa: E402


class TestFormatSeerr(unittest.TestCase):
    def test_film(self):
        self.assertEqual(_fmt_media("movie", "Zootopie 2", "2025"),
                         "le film **Zootopie 2** (2025)")

    def test_serie_saisons(self):
        self.assertEqual(_fmt_media("tv", "GoT", None, [1, 2]),
                         "la série **GoT** — saisons 1, 2")

    def test_demande_auto(self):
        line = _fmt_request("2026-08-24 15:52", "admin", "movie", "Zootopie 2",
                            "2025", auto=True)
        self.assertIn("**admin** a demandé le film **Zootopie 2** (2025)", line)
        self.assertIn("(auto-approuvée)", line)

    def test_titre_inconnu(self):
        # titre non résolu (Seerr muet) : la ligne sort quand même, sans lever
        self.assertIn("**?**", _fmt_media("movie", None))


class TestTimecode(unittest.TestCase):
    def test_hms(self):
        self.assertEqual(_hms(4930), "1:22:10")
        self.assertEqual(_hms(130), "2:10")
        self.assertEqual(_hms(0), "0:00")

    def test_rx_ip(self):
        m = _RX_IP.search("Adresse IP : 10.3.20.120")
        self.assertEqual(m.group(1), "10.3.20.120")


if __name__ == "__main__":
    unittest.main()


class _State(dict):
    def set(self, k, v):
        self[k] = v


class _Chan:
    def __init__(self):
        self.sent = []

    async def send(self, msg, **kw):
        self.sent.append(msg)


class _Seerr:
    def __init__(self):
        self.media_status = 5

    async def aget(self, path, params=None, quiet=False):
        if path.startswith("/api/v1/tv/"):
            return {"name": "Classroom of the Elite", "firstAirDate": "2017-07-12"}
        return {"results": [{"id": 105, "status": 2, "type": "tv",
                             "media": {"mediaType": "tv", "tmdbId": 72517,
                                       "status": self.media_status},
                             "seasons": [{"seasonNumber": n} for n in (1, 2, 3, 4)],
                             "requestedBy": {"displayName": "Nico"}}]}


class TestDisponibleUneFois(unittest.IsolatedAsyncioTestCase):
    """17/11 : Seerr fait osciller dispo(5) <-> partiel(4) à chaque scan ;
    « … est disponible » ne doit partir qu UNE fois par demande."""

    async def test_oscillation(self):
        from unittest import mock
        from bot.cogs import seerr_activity as sa
        chan = _Chan()
        bot = mock.Mock()
        bot.state = _State(prov={"jellyfin_logs": 1}, seerr_last_req_id=105,
                           seerr_req_status={"105": [2, 4]})
        bot.get_channel.return_value = chan
        with mock.patch.object(sa, "client_for", return_value=None):
            cog = sa.SeerrActivity(bot)           # seerr None : la boucle ne démarre pas
        cog.seerr = _Seerr()
        cog._annonces = {}                        # comme une demande jamais annoncée
        for statut in (5, 4, 5, 4, 5):
            cog.seerr.media_status = statut
            await cog.poll.coro(cog)
        dispo = [m for m in chan.sent if "disponible" in m]
        self.assertEqual(len(dispo), 1, chan.sent)
        self.assertIn("Classroom of the Elite", dispo[0])
