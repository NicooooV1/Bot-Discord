"""Journal Jellyseerr dans #jelly-logs (catégorie 🔒 Lock, 2026-08-24) — demandes
(« X a demandé X films/série »), refus et disponibilité. D'abord voulu séparé, puis
fusionné le même jour : « il faut que tout soit dans un seul salon, jelly-logs » —
le cog écrit donc dans LE MÊME salon que JellyfinActivity (prov jellyfin_logs).

Source : l'API Jellyseerr (servarr-apis.json, comme le cog Medias) :
  • GET /api/v1/request?sort=added  -> nouvelles demandes (« Nico a demandé … ») ;
  • le même endpoint, relu à chaque tour, sert aussi de suivi d'état : une demande
    refusée (status 3) ou un média devenu disponible (media.status 4/5) se publient
    en transition — on retient {id: (status, media_status)} dans l'état persisté.

Le titre n'est PAS dans la demande (juste un tmdbId) : il se résout via
/api/v1/movie/{id} ou /api/v1/tv/{id}, avec un cache mémoire (les relances du poll
ne doivent pas marteler TMDB via Seerr).

Salon provisionné par provision.py (_provision_seerr_log_channel), channel_id recâblé
en direct après provisioning, exactement comme JellyfinActivity."""
import logging

import discord
from discord.ext import commands, tasks

from ..core.http import client_for, load_service_apis

log = logging.getLogger("discord-bot.seerractivity")

POLL_MINUTES = 2
PAGE_SIZE = 30

# statuts Jellyseerr (documentés dans son OpenAPI)
REQ_PENDING, REQ_APPROVED, REQ_DECLINED = 1, 2, 3
MEDIA_PARTIAL, MEDIA_AVAILABLE = 4, 5


def _fmt_media(kind, title, year=None, seasons=None):
    """« le film **Titre** (2025) » / « la série **Titre** — saison(s) 1, 2 »."""
    what = "la série" if kind == "tv" else "le film"
    s = f"{what} **{title or '?'}**"
    if year:
        s += f" ({year})"
    if seasons:
        s += " — saison" + ("s " if len(seasons) > 1 else " ") \
             + ", ".join(str(n) for n in seasons)
    return s


def _fmt_request(when, user, kind, title, year=None, seasons=None, auto=False):
    line = f"📥 `{when}` **{user or '?'}** a demandé {_fmt_media(kind, title, year, seasons)}"
    if auto:
        line += " *(auto-approuvée)*"
    return line[:500]


class SeerrActivity(commands.Cog):
    """Journal des demandes Jellyseerr (salon #jellyseerr-logs, propriétaire only)."""

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.seerr = client_for(load_service_apis(), "seerr")
        self.channel_id = (bot.state.get("prov", {}) or {}).get("jellyfin_logs")
        self._last_id = bot.state.get("seerr_last_req_id")
        # {str(request_id): [status, media_status]} — transitions déjà publiées
        self._statuts = dict(bot.state.get("seerr_req_status", {}) or {})
        self._titres = {}          # (type, tmdbId) -> (titre, année) ; cache mémoire
        if self.channel_id and self.enabled:
            self.poll.start()

    @property
    def enabled(self):
        return self.seerr is not None

    def cog_unload(self):
        self.poll.cancel()

    # ------------------------------------------------------------------ titres

    async def _titre(self, kind, tmdb_id):
        """(titre, année) depuis Seerr, None-tolérant. Cache mémoire : un même média
        revient à chaque transition d'état, inutile de re-résoudre."""
        if not tmdb_id:
            return None, None
        k = (kind, tmdb_id)
        if k in self._titres:
            return self._titres[k]
        path = f"/api/v1/tv/{tmdb_id}" if kind == "tv" else f"/api/v1/movie/{tmdb_id}"
        d = await self.seerr.aget(path, quiet=True)
        if not isinstance(d, dict):
            return None, None            # PAS mis en cache : réessaiera au tour suivant
        titre = d.get("title") or d.get("name")
        date = d.get("releaseDate") or d.get("firstAirDate") or ""
        annee = date[:4] if len(date) >= 4 else None
        self._titres[k] = (titre, annee)
        return titre, annee

    # ------------------------------------------------------------------ boucle

    async def _decrire(self, r):
        """(user, kind, titre, année, saisons) d'une demande brute."""
        media = r.get("media") or {}
        kind = r.get("type") or media.get("mediaType") or "movie"
        titre, annee = await self._titre(kind, media.get("tmdbId"))
        saisons = sorted(s.get("seasonNumber") for s in (r.get("seasons") or [])
                         if isinstance(s.get("seasonNumber"), int))
        user = ((r.get("requestedBy") or {}).get("displayName")
                or (r.get("requestedBy") or {}).get("jellyfinUsername"))
        return user, kind, titre, annee, saisons

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        if not (self.channel_id and self.enabled):
            return
        # quiet : Seerr éteint est nominal (CT120 redémarre), l'échec se répète
        d = await self.seerr.aget("/api/v1/request",
                                  {"take": PAGE_SIZE, "sort": "added"}, quiet=True)
        if not isinstance(d, dict):
            return                        # échec d'appel : ne rien conclure, ne rien avancer
        reqs = d.get("results") or []
        if self._last_id is None:
            # premier démarrage : pas de rattrapage de l'historique, on se borne
            self._last_id = max((r.get("id") or 0 for r in reqs), default=0)
            for r in reqs:
                self._statuts[str(r["id"])] = [r.get("status"),
                                               (r.get("media") or {}).get("status")]
            self._save()
            return
        ch = self.bot.get_channel(self.channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(self.channel_id)
            except Exception:  # noqa: BLE001
                log.warning("salon jelly-logs %s introuvable", self.channel_id)
                return
        lignes = []
        maxi = self._last_id
        for r in sorted(reqs, key=lambda x: x.get("id") or 0):
            rid = r.get("id") or 0
            statut = r.get("status")
            m_statut = (r.get("media") or {}).get("status")
            avant = self._statuts.get(str(rid))
            if rid > self._last_id:
                user, kind, titre, annee, saisons = await self._decrire(r)
                when = (r.get("createdAt") or "")[:19].replace("T", " ")
                lignes.append(_fmt_request(when, user, kind, titre, annee, saisons,
                                           auto=statut == REQ_APPROVED))
                maxi = max(maxi, rid)
            elif avant is not None and [statut, m_statut] != avant:
                user, kind, titre, annee, saisons = await self._decrire(r)
                media = _fmt_media(kind, titre, annee, saisons)
                if statut == REQ_DECLINED and avant[0] != REQ_DECLINED:
                    lignes.append(f"⛔ demande de **{user or '?'}** refusée : {media}")
                elif statut == REQ_APPROVED and avant[0] == REQ_PENDING:
                    lignes.append(f"✅ demande de **{user or '?'}** approuvée : {media}")
                if m_statut == MEDIA_AVAILABLE and avant[1] != MEDIA_AVAILABLE:
                    lignes.append(f"🎬 {media} est **disponible**")
                elif m_statut == MEDIA_PARTIAL and avant[1] not in (MEDIA_PARTIAL,
                                                                    MEDIA_AVAILABLE):
                    lignes.append(f"🎬 {media} est **partiellement disponible**")
            self._statuts[str(rid)] = [statut, m_statut]
        # garde-fou anti-flood, même logique que JellyfinActivity : 20 lignes max/tour ;
        # le curseur n'avance que si l'envoi a réussi (un échec Discord rejouera le tour)
        envoyees = 0
        try:
            for ligne in lignes[:20]:
                await ch.send(ligne, allowed_mentions=discord.AllowedMentions.none())
                envoyees += 1
        except (discord.Forbidden, discord.NotFound):
            log.warning("jelly-logs %s inaccessible — %d ligne(s) non publiée(s)",
                        self.channel_id, len(lignes) - envoyees, exc_info=True)
            return
        except discord.HTTPException:
            log.warning("envoi vers jelly-logs échoué — reprise au cycle suivant",
                        exc_info=True)
            return
        self._last_id = maxi
        self._save()

    def _save(self):
        # l'historique d'états est borné : Seerr ne renvoie que les PAGE_SIZE dernières
        # demandes, les ids sortis de cette fenêtre ne transitionneront plus ici
        if len(self._statuts) > 400:
            for k in sorted(self._statuts, key=int)[:len(self._statuts) - 400]:
                self._statuts.pop(k, None)
        self.bot.state.set("seerr_last_req_id", self._last_id)
        self.bot.state.set("seerr_req_status", self._statuts)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SeerrActivity(bot))
