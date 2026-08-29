"""Journal d'activité Jellyfin dans #jellyfin-logs (catégorie 🔒 Lock, 2026-07-18) —
demande Nico : « les logs de Jellyfin (comme vu dans l'interface admin), film
commencé, compte créé etc. ».

Source : GET /System/ActivityLog/Entries (même API que Réglages -> Panneau
d'administration -> Journal d'activité dans Jellyfin). Poll simple : on retient le
plus grand `Id` déjà posté (les Id sont monotones croissants) et on ne publie que
les entrées plus récentes, dans l'ordre chronologique. Au tout premier démarrage on
se cale sur l'entrée la plus récente sans spammer tout l'historique.

Salon provisionné par provision.py (_provision_jellyfin_log_channel) ; channel_id
recâblé en direct après provisioning, comme NodeChannel."""
import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from ..core.http import ApiClient

log = logging.getLogger("discord-bot.jellyfinactivity")

POLL_MINUTES = 2
PAGE_SIZE = 50

_TYPE_EMOJI = {
    "sessionstarted": "🟢",
    "sessionended": "🔴",
    "videoplayback": "▶️",
    "videoplaybackstopped": "⏹️",
    "audioplayback": "🎵",
    "audioplaybackstopped": "⏹️",
    "authenticationsucceeded": "🔓",
    "authenticationfailed": "🚫",
    "authenticationfailure": "🚫",
    "usercreated": "👤➕",
    "userdeleted": "👤➖",
    "userpasswordchanged": "🔑",
    "userlockedout": "🔒",
    "userupdated": "👤✏️",
    "subtitledownloadfailure": "⚠️",
    "cameraimageuploaded": "📷",
    "issued": "⚠️",
}

_SEV_EMOJI = {"error": "❌", "critical": "❌", "warn": "⚠️", "warning": "⚠️"}

# ------------------------------------------------ micro-coupures de session
# L'appli TV (« Salon ») ferme et rouvre sa session en ~1 s à chaque reprise (et
# parfois en boucle pendant la lecture) : Jellyfin logue une paire
# SessionEnded/SessionStarted que le bot relayait telle quelle — Nico recevait des
# « déconnecté » alors que la reconnexion avait suivi dans la seconde (2026-08-20).
# Règle : une fin de session n'est publiée que si AUCUNE reconnexion du même
# (utilisateur, appareil) n'arrive dans les FLAP_WINDOW_S ; une paire rapide est
# absorbée en silence. Les fins en attente vivent dans state["jf_pending_ended"].
FLAP_WINDOW_S = 300
_RX_DEPUIS = re.compile(r" depuis (.+)$")
_RX_IP = re.compile(r"Adresse IP\s*:\s*([0-9a-fA-F.:]+)")
_RX_SUR = re.compile(r" sur (.+)$")


def _appli_pour(devices, uid, appareil, ref):
    """« Streamyfin (iPhone) » : l'appli réelle derrière un nom d'appareil.

    L'ActivityLog ne donne que le NOM de l'appareil (« iPhone », « Opera »,
    « Ordi-Nico ») — impossible d'y distinguer Streamyfin de l'appli Jellyfin iOS,
    ou Jellyfin Desktop d'un navigateur (Nico 29/08). /Devices, lui, mémorise
    durablement (utilisateur, nom d'appareil) -> AppName. Quand un même
    utilisateur a plusieurs appareils du même nom (« LG Smart TV » = appli WebOS
    ET navigateur), on prend celui dont la dernière activité est la plus proche
    de l'entrée. Rend None si rien ne colle (la ligne sort sans suffixe)."""
    if not (uid and appareil):
        return None
    best = None
    for d in devices or []:
        if d.get("LastUserId") != uid or (d.get("Name") or "").strip() != appareil:
            continue
        try:
            dt = datetime.fromisoformat(
                (d.get("DateLastActivity") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        ecart = abs((dt - ref).total_seconds())
        if best is None or ecart < best[0]:
            best = (ecart, d)
    if best is None:
        return None
    app = (best[1].get("AppName") or "").strip()
    if not app:
        return None
    return f"**{app}**" + (f" ({appareil})" if appareil.lower() not in app.lower() else "")


def _hms(sec):
    """4930 -> « 1:22:10 » (heures sans zéro de tête, comme l'UI Jellyfin)."""
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _seerr_host_ip():
    """IP du Jellyseerr de servarr-apis.json (None s'il n'est pas configuré) : une
    authentification Jellyfin venant de cette adresse est faite PAR Seerr."""
    try:
        from urllib.parse import urlsplit
        from ..core.http import load_service_apis
        url = (load_service_apis().get("seerr") or {}).get("url") or ""
        return urlsplit(url).hostname
    except Exception:  # noqa: BLE001
        return None


def _flap_key(entry):
    """(UserId, appareil, type) pour les entrées de session, None sinon."""
    t = (entry.get("Type") or "").lower()
    if t not in ("sessionstarted", "sessionended"):
        return None
    m = _RX_DEPUIS.search(entry.get("Name") or "")
    return (entry.get("UserId"), m.group(1) if m else None, t)


def _entry_dt(entry):
    try:
        d = datetime.fromisoformat((entry.get("Date") or "").replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _classify(entries, pending, now):
    """Sépare ce qui se publie de ce qui attend une reconnexion.

    Rend (a_publier, pending'). `pending` = {str(Id): entrée} des fins de session
    (ou des lignes dont l'envoi a échoué) en attente ; les entrées mûres (fenêtre
    dépassée sans reconnexion) passent en tête de `a_publier`, en ordre d'Id.
    """
    pending = dict(pending)
    posts = []
    for e in entries:
        key = _flap_key(e)
        if key and key[2] == "sessionended":
            pending[str(e["Id"])] = e
            continue
        if key and key[2] == "sessionstarted":
            match = None
            for pid, pe in pending.items():
                pk = _flap_key(pe)
                if (pk and pk[2] == "sessionended" and pk[:2] == key[:2]
                        and abs((_entry_dt(e) - _entry_dt(pe)).total_seconds()) <= FLAP_WINDOW_S):
                    match = pid
                    break
            if match is not None:
                pending.pop(match)      # micro-coupure : les DEUX lignes disparaissent
                continue
        posts.append(e)
    mures = [pe for pe in pending.values()
             if (now - _entry_dt(pe)).total_seconds() > FLAP_WINDOW_S]
    for pe in mures:
        pending.pop(str(pe["Id"]), None)
    return sorted(mures, key=lambda x: x.get("Id") or 0) + posts, pending


def _emoji_for(entry):
    t = (entry.get("Type") or "").lower()
    if t in _TYPE_EMOJI:
        return _TYPE_EMOJI[t]
    sev = (entry.get("Severity") or "").lower()
    return _SEV_EMOJI.get(sev, "ℹ️")


class JellyfinActivity(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        self.channel_id = (bot.state.get("prov", {}) or {}).get("jellyfin_logs")
        self._last_id = bot.state.get("jellyfin_activity_last_id")
        self._seerr_ip = _seerr_host_ip()
        self._sessions_cache = None    # rempli au plus une fois par tour de poll
        self._devices_cache = None
        if self.channel_id and self.enabled:
            self.poll.start()

    @property
    def enabled(self):
        return bool(getattr(self.cfg, "jellyfin_logs_enabled", True)
                     and self.cfg.jellyfin_url and self.cfg.jellyfin_api_key)

    def cog_unload(self):
        self.poll.cancel()

    # ------------------------------------------------------------------ API

    def _client(self):
        """Client de l'API Jellyfin — en-tête MediaBrowser Token : Jellyfin 10.11 ignore
        X-Emby-Token et `api_key=`."""
        return ApiClient(self.cfg.jellyfin_url,
                         {"Authorization": f'MediaBrowser Token="{self.cfg.jellyfin_api_key}"'},
                         timeout=8, label="jellyfin activity")

    def _format(self, entry, extra=None):
        when = (entry.get("Date") or "")[:19].replace("T", " ")
        who = entry.get("Name") or "?"
        overview = entry.get("ShortOverview") or entry.get("Overview") or ""
        line = f"{_emoji_for(entry)} `{when}` {who}"
        if overview and overview != who:
            line += f" — {overview}"
        if extra:
            line += f" — {extra}"
        return line[:500]

    # ------------------------------------------------ enrichissements (Nico 24/08)

    async def _sessions(self):
        """GET /Sessions, UNE fois par tour de poll (cache remis à zéro par poll()).
        Sert à nommer le client d'une authentification : l'ActivityLog ne donne que
        l'adresse IP, la session dit « Streamyfin sur iPhone »."""
        if self._sessions_cache is None:
            s = await self._client().aget("/Sessions", quiet=True)
            self._sessions_cache = s if isinstance(s, list) else []
        return self._sessions_cache

    async def _devices(self):
        """GET /Devices, UNE fois par tour de poll (même cache-reset que /Sessions)."""
        if self._devices_cache is None:
            d = await self._client().aget("/Devices", quiet=True)
            items = d.get("Items") if isinstance(d, dict) else None
            self._devices_cache = items if isinstance(items, list) else []
        return self._devices_cache

    async def _appareil(self, entry):
        """Suffixe « — Streamyfin (iPhone) » pour une session ou une lecture : le nom
        d'appareil est en fin de Name (« … depuis iPhone » / « … sur Opera »)."""
        name = entry.get("Name") or ""
        m = _RX_DEPUIS.search(name) or _RX_SUR.search(name)
        if not m:
            return None
        return _appli_pour(await self._devices(), entry.get("UserId"),
                           m.group(1).strip(), _entry_dt(entry))

    async def _provenance_auth(self, entry):
        """« via Jellyseerr » / « Streamyfin sur iPhone » / « Jellyfin Web sur Firefox ».

        Deux sources, dans l'ordre :
        • l'adresse IP de l'entrée : celle du CT Jellyseerr = une connexion faite PAR
          Seerr (import d'utilisateur, login Seerr adossé à Jellyfin) — les sessions ne
          la voient pas, c'est un aller-retour serveur→serveur ;
        • sinon la session la plus récente du même utilisateur (fenêtre 10 min) : son
          Client/DeviceName désigne l'appli réelle (Streamyfin, Jellyfin Web, TV…)."""
        m = _RX_IP.search(entry.get("ShortOverview") or "")
        ip = m.group(1) if m else None
        if ip and self._seerr_ip and ip == self._seerr_ip:
            return "via **Jellyseerr**"
        uid = entry.get("UserId") or ""
        if not uid.strip("0"):
            return None                      # échec d'auth : pas de session à croiser
        ref = _entry_dt(entry)
        best = None
        for s in await self._sessions():
            if s.get("UserId") != uid:
                continue
            try:
                d = datetime.fromisoformat(
                    (s.get("LastActivityDate") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            ecart = abs((d - ref).total_seconds())
            if ecart <= 600 and (best is None or ecart < best[0]):
                best = (ecart, s)
        if best is None:
            return None
        client = (best[1].get("Client") or "").strip()
        device = (best[1].get("DeviceName") or "").strip()
        if not client:
            return None
        via = f"sur **{client}**"
        if device and device.lower() not in client.lower():
            via += f" ({device})"
        return via

    async def _timecode_arret(self, entry):
        """« arrêté à 0:42:10 / 0:57:31 (73 %) » pour un VideoPlaybackStopped.

        La position n'est pas dans l'ActivityLog : c'est la position de REPRISE que
        Jellyfin vient d'enregistrer sur l'item (UserData.PlaybackPositionTicks).
        Position nulle + Played = visionnage terminé. Échec d'appel = pas de suffixe
        (la ligne sort quand même, l'info est un bonus)."""
        uid, iid = entry.get("UserId"), entry.get("ItemId")
        if not (uid and iid):
            return None
        item = await self._client().aget(f"/Users/{uid}/Items/{iid}", quiet=True)
        if not isinstance(item, dict):
            return None
        ud = item.get("UserData") or {}
        pos = int(ud.get("PlaybackPositionTicks") or 0) // 10_000_000
        duree = int(item.get("RunTimeTicks") or 0) // 10_000_000
        if pos <= 0:
            return "**terminé**" if ud.get("Played") else None
        txt = f"arrêté à **{_hms(pos)}**"
        if duree > 0:
            txt += f" / {_hms(duree)} ({round(pos * 100 / duree)} %)"
        return txt

    async def _extra(self, entry):
        t = (entry.get("Type") or "").lower()
        try:
            if t in ("authenticationsucceeded", "authenticationfailed",
                     "authenticationfailure"):
                return await self._provenance_auth(entry)
            if t == "videoplaybackstopped":
                parts = [await self._appareil(entry), await self._timecode_arret(entry)]
                return " — ".join(p for p in parts if p) or None
            if t in ("sessionstarted", "sessionended", "videoplayback",
                     "audioplayback", "audioplaybackstopped"):
                return await self._appareil(entry)
        except Exception:  # noqa: BLE001 — l'enrichissement ne doit jamais bloquer la ligne
            log.debug("enrichissement impossible", exc_info=True)
        return None

    # ------------------------------------------------------------------ boucle

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        if not (self.channel_id and self.enabled):
            return
        # quiet=True : Jellyfin éteint est un cas NOMINAL ici et l'échec se répète toutes
        # les 2 min -> journal de debug (l'erreur exacte, 401 = clé périmée, y reste
        # trouvable au lieu d'être avalée).
        data = await self._client().aget("/System/ActivityLog/Entries",
                                         {"startIndex": 0, "limit": PAGE_SIZE}, quiet=True)
        if data is None:
            # None = appel EN ÉCHEC, à ne pas confondre avec « aucune entrée » : dans les
            # deux cas on ne publie rien, mais le curseur ne doit surtout pas bouger.
            return
        items = (data.get("Items") if isinstance(data, dict) else None) or []
        if not items:
            return
        # Jellyfin renvoie du plus récent au plus ancien
        items = sorted(items, key=lambda e: e.get("Id") or 0)
        if self._last_id is None:
            # premier démarrage : pas de rattrapage de l'historique
            self._last_id = items[-1]["Id"]
            self.bot.state.set("jellyfin_activity_last_id", self._last_id)
            return
        new = [e for e in items if (e.get("Id") or 0) > self._last_id]
        # même sans nouveauté, une fin de session en attente doit pouvoir mûrir et
        # être publiée une fois la fenêtre passée (sinon la DERNIÈRE déconnexion de
        # la journée ne sortirait jamais, 2026-08-20)
        if not new and not (self.bot.state.get("jf_pending_ended") or {}):
            return
        ch = self.bot.get_channel(self.channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(self.channel_id)
            except Exception:
                log.warning("salon jellyfin-logs %s introuvable", self.channel_id)
                return
        # Garde-fou anti-flood : on n'envoie que 20 entrées par tour, et le curseur
        # n'avance QUE sur ce qui est réellement parti. Avant (2026-08-11) il sautait à
        # `new[-1]` — dernier élément de la liste COMPLÈTE : le reliquat au-delà de 20,
        # comme tout ce qui suivait un envoi en échec, était perdu pour Discord (le
        # curseur est persisté dans state.json, donc la perte survivait au redémarrage).
        # Le reliquat est repris au cycle suivant, 20 par 20.
        batch = new[:20]
        # micro-coupures de session absorbées : cf. _classify. Le curseur avance sur
        # tout le lot (les fins de session non publiées survivent dans pending, qui
        # est persisté — rien n'est perdu, 2026-08-20).
        pending = self.bot.state.get("jf_pending_ended") or {}
        posts, pending = _classify(batch, pending, discord.utils.utcnow())
        self._sessions_cache = None    # cache /Sessions : au plus un GET par tour
        self._devices_cache = None     # idem /Devices
        for i, entry in enumerate(posts):
            try:
                await ch.send(self._format(entry, extra=await self._extra(entry)))
            except (discord.Forbidden, discord.NotFound):
                # Panne DURABLE (droit d'écriture retiré, salon supprimé) : rejouer le
                # même envoi voué à l'échec ne mène nulle part — on jette, et on le DIT.
                log.warning("jellyfin-logs %s inaccessible — %d entrée(s) non publiée(s)",
                            self.channel_id, len(posts) - i, exc_info=True)
                break
            except discord.HTTPException:
                # Panne transitoire : les lignes non parties retournent en attente et
                # ressortiront au prochain cycle (via l'échéance de _classify).
                log.warning("envoi vers jellyfin-logs échoué — reprise au cycle suivant",
                            exc_info=True)
                for e in posts[i:]:
                    pending[str(e["Id"])] = e
                break
        self.bot.state.set("jf_pending_ended", pending)
        if batch and batch[-1]["Id"] != self._last_id:
            self._last_id = batch[-1]["Id"]
            self.bot.state.set("jellyfin_activity_last_id", self._last_id)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(JellyfinActivity(bot))
