"""Cog YouTube/Twitch à la demande — /yt, /tw, /musique, /yt-config + pilotage + suppression auto.

Modèle « à la demande + jetable » (spec Nico 2026-07-14) :
- /yt <url> [suppression] : le bot demande à ytgrab (CT120:8770) de télécharger la
  vidéo/rediff en 1080p H.264 dans /mnt/media/YouTube (biblio Jellyfin
  « YouTube & Twitch »), avec une barre de progression éditée en direct.
- Suppression AUTO après N jours SI aucun visionnage n'a commencé ; dès qu'un
  visionnage démarre (Played ou position > 0, n'importe quel user), l'item passe
  en suppression MANUELLE (le bouton Supprimer de Jellyfin, activé pour l'user).

DÉLAI RÉGLABLE (spec Nico 2026-07-16) : le délai global se règle avec /yt-config
(admin, persistant) et chaque /yt ou /tw peut choisir SON délai via `suppression`.
Unité = HEURES (1 h à 30 j, ou jamais) pour couvrir le très court terme. Les délais
par fichier sont mémorisés dans state.json (clé yt_delays, {chemin: {hours, ts}}) et
lus par la boucle autodelete (toutes les 15 min pour honorer les délais courts).

PLAYLISTS (spec Nico 2026-07-16) : une URL de playlist déclenche d'abord un SONDAGE
(énumération sans téléchargement + taille totale estimée par échantillon), puis une
CONFIRMATION explicite — règle « annoncer la taille avant tout téléchargement ».
Une URL watch?v=…&list=… propose aussi « cette vidéo seule ».

MUSIQUE (spec Nico 2026-07-16) : /musique télécharge la piste ou la playlist en
Opus NATIF YouTube (copie de flux, zéro réencodage) dans /mnt/media/Musique
(biblio Jellyfin « Musique »). Jamais d'auto-suppression pour la musique.

PILOTAGE (spec Nico 2026-07-15) : trois boutons sous la barre de progression —
⏸️ Pause / ▶️ Reprendre (SIGSTOP/SIGCONT côté ytgrab = reprise à l'octet près) et
⏹️ Arrêter (arrêt + nettoyage des résidus). Tout arrêt est ATTRIBUÉ : l'embed affiche
« ⏹️ Arrêté par X », jamais « ❌ Échec » — ce dernier est réservé aux vraies pannes.
Un arrêt fait hors du bot (pkill depuis le PVE) s'affiche « arrêté hors du bot ».
Boutons réservés à celui qui a lancé le job + aux admins.

Sécurité suppression : ne supprime QUE des items dont le chemin commence par
/mnt/media/YouTube (double garde en plus du parentId de la biblio).
Sécurité lancement (2026-08-11) : l'URL est filtrée par _valid_url() AVANT de partir
vers ytgrab — le tier LECTURE pouvait sinon faire émettre à CT120 une requête vers
n'importe quel hôte (SSRF quasi aveugle vers le LAN) ou faire écrire n'importe quoi
dans /mnt/media. Défense en profondeur : ytgrab valide de son côté.
Réseau : bot CT106 -> ytgrab CT120:8770 (pare-feu 120.fw) et Jellyfin CT105:8096.
"""
import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from . import youtube_render as render
from ..core import bg, channels
from ..core.gates import GatedView
from ..core.http import ApiClient
from ..core.permissions import admin_check, is_admin, read_check

log = logging.getLogger("discord-bot.youtube")

YTGRAB = "http://10.3.20.120:8770"
YT_LIB_NAME = "YouTube & Twitch"
MUSIC_LIB_NAME = "Musique"
YT_MEDIA_PREFIX = "/mnt/media/YouTube"
AUTO_DELETE_HOURS = 96        # défaut = 4 jours ; surchargé par /yt-config (state.json)
POLL_SECONDS = 8
NOTICE_CHANNEL = "youtube"

ACTIVE = ("queued", "running", "paused")

# Délais d'auto-suppression en HEURES (-1 = jamais). Unité = heures pour couvrir du très
# court (1 h) au long (30 j). Migration depuis l'ancienne unité « jours » au chargement.
DELAY_CHOICES = [
    app_commands.Choice(name="1 heure", value=1),
    app_commands.Choice(name="3 heures", value=3),
    app_commands.Choice(name="6 heures", value=6),
    app_commands.Choice(name="12 heures", value=12),
    app_commands.Choice(name="1 jour", value=24),
    app_commands.Choice(name="2 jours", value=48),
    app_commands.Choice(name="4 jours", value=96),
    app_commands.Choice(name="7 jours", value=168),
    app_commands.Choice(name="30 jours", value=720),
    app_commands.Choice(name="jamais (garder)", value=-1),
]


# URLs MULTI-vidéos qui ne sont pas des playlists explicites : chaîne/onglet/
# recherche YouTube, pages vidéos/collections Twitch. yt-dlp les traite comme des
# playlists complètes et --no-playlist est SANS EFFET dessus -> même circuit
# sondage + annonce de taille + confirmation. (ytgrab refuse d'ailleurs ces URL
# en mode « simple » — défense en profondeur.)
_MULTI_RE = re.compile(
    r"(youtube\.com/(@|channel/|c/|user/|results\b|feed/)"
    r"|twitch\.tv/[^/]+/(videos|clips)\b"
    r"|twitch\.tv/(collections?|directory)/)", re.I)


def _playlist_intent(url):
    """True si l'URL désigne un ENSEMBLE de vidéos -> sondage + confirmation."""
    u = url.lower()
    if _MULTI_RE.search(u):
        return True
    if "twitch.tv/" in u:
        return False
    if "/playlist" in u:
        return True
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    except ValueError:
        return False
    return bool(qs.get("list"))


# Domaines acceptés par /yt, /tw et /musique (2026-08-11). L'URL est relayée telle
# quelle à ytgrab, qui la donne à yt-dlp : sans liste blanche ICI, n'importe quel
# porteur du rôle LECTURE pouvait faire émettre à CT120 une requête sortante vers un
# hôte arbitraire — y compris interne (SSRF quasi aveugle, l'erreur revenant dans
# l'embed) — ou faire écrire du contenu quelconque dans /mnt/media.
# Test par SUFFIXE de domaine : un `in {...}` strict casserait les sous-domaines
# réellement utilisés (m., music., clips., player.).
_ALLOWED_SUFFIXES = ("youtube.com", "youtu.be", "youtube-nocookie.com", "twitch.tv")

# Aucune URL YouTube/Twitch légitime n'approche cette longueur ; la borne évite en prime
# de dépasser les 4096 caractères de description d'embed avec le « [Lien](...) ».
_MAX_URL = 500

# Caractères qui n'apparaissent JAMAIS dans une URL YouTube/Twitch légitime et qui
# rendraient la liste blanche INOPÉRANTE (2026-08-11) :
#  - blancs et caractères de contrôle : urlparse RETIRE lui-même \t \r \n avant
#    d'analyser (_UNSAFE_URL_BYTES_TO_REMOVE), si bien que « https://www.you<TAB>tube.com/… »
#    était déclaré valide alors que c'est la chaîne BRUTE, différente, qui part vers
#    ytgrab : on validait une URL et on en envoyait une autre ;
#  - parenthèses : l'URL est rendue telle quelle dans « [Lien](url) ». Une « ) » referme
#    le lien et laisse écrire du markdown arbitraire — un lien de hameçonnage posté par
#    le BOT dans un salon public, à la portée du seul tier LECTURE.
_UNSAFE_URL_CHARS = re.compile(r"[\s()<>\x00-\x1f\x7f]")


def _valid_url(u):
    """True si `u` est un lien http(s) vers un domaine YouTube/Twitch autorisé.

    Rejette au passage tout ce qui n'a pas de schéma http(s) : « --exec=… », « file:// »,
    « http://10.3.20.x », etc. — la validation d'entrée est de notre côté, pas déléguée.

    ⚠️ Le contrôle porte sur la chaîne DÉJÀ normalisée (`.strip()`) : l'appelant doit
    transmettre cette même chaîne à ytgrab, sinon la garde ne garde rien (cf.
    _start_download).
    """
    if not isinstance(u, str):
        return False
    u = u.strip()
    if not u or len(u) > _MAX_URL:
        return False
    if _UNSAFE_URL_CHARS.search(u):
        return False
    try:
        p = urllib.parse.urlparse(u)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in _ALLOWED_SUFFIXES)


def _strip_playlist(url):
    """« watch?v=X&list=Y » -> URL de la vidéo SEULE (None si pas de vidéo isolable)."""
    try:
        pu = urllib.parse.urlparse(url)
        if "youtu.be" in (pu.netloc or ""):
            return f"https://youtu.be{pu.path}"
        v = (urllib.parse.parse_qs(pu.query).get("v") or [None])[0]
        if v and re.match(r"^[A-Za-z0-9_-]{5,20}$", v):
            return f"https://www.youtube.com/watch?v={v}"
    except ValueError:
        pass
    return None


class DownloadView(GatedView):
    """⏸️/▶️ + ⏹️ sous la barre de progression. Réservé au lanceur et aux admins.

    Porte « read » (2026-08-11) : /yt est ouvert au tier LECTURE, donc l'auteur d'un job
    peut n'avoir que ce rôle — exiger « mod » lui retirerait le pilotage de SON propre
    téléchargement. La porte apporte surtout la session 2FA : la vue a timeout=None et
    restait cliquable bien après l'expiration de la session (le 2FA a été généralisé aux
    BOUTONS le 2026-07-16, cette vue était restée à l'écart).
    ⚠️ `gate_user_id` n'est PAS posé : il verrouillerait la vue sur le seul lanceur, alors
    que le modèle documenté ouvre aussi le pilotage aux admins (voir interaction_check).
    """

    gate = "read"

    def __init__(self, cog, jid, owner_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.jid = jid
        self.owner_id = owner_id

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        # porte commune d'abord (tier lecture + session 2FA), puis la règle propre à la vue
        if not await super().interaction_check(itx):
            return False
        if itx.user.id == self.owner_id or is_admin(itx.client.cfg, itx):
            return True
        await itx.response.send_message(
            "⛔ Seul l'auteur du téléchargement (ou un admin) peut le piloter.", ephemeral=True)
        return False

    def sync(self, state):
        """Aligne les boutons sur l'état réel du job."""
        paused = state == "paused"
        self.pause_btn.label = "Reprendre" if paused else "Pause"
        self.pause_btn.emoji = "▶️" if paused else "⏸️"
        self.pause_btn.style = discord.ButtonStyle.success if paused else discord.ButtonStyle.secondary
        self.pause_btn.disabled = state not in ("running", "paused")
        self.stop_btn.disabled = state not in ACTIVE

    def freeze(self):
        for c in self.children:
            c.disabled = True

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary)
    async def pause_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        await itx.response.defer()
        j = await self.cog._yt("GET", f"/jobs/{self.jid}") or {}
        action = "resume" if j.get("state") == "paused" else "pause"
        actor = itx.user.display_name
        r = await self.cog._yt("POST", f"/jobs/{self.jid}/{action}", {"actor": actor})
        if not r or r.get("error"):
            await itx.followup.send(
                f"⚠️ {(r or {}).get('error', 'ytgrab injoignable')}", ephemeral=True)

    @discord.ui.button(label="Arrêter", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        await itx.response.defer()
        actor = itx.user.display_name
        r = await self.cog._yt("POST", f"/jobs/{self.jid}/cancel",
                               {"actor": actor, "delete_partial": True})
        err = r.get("error") if r else "ytgrab injoignable"
        # l'arrêt supprime les résidus partiels : il mérite la même trace append-only que
        # le lancement (2026-08-11 — `actor` seul est un display_name, pas une identité).
        self.cog.bot.audit.record(
            user=f"{itx.user} ({itx.user.id})", action="yt-cancel",
            target=f"job {self.jid}", result=f"error: {str(err)[:120]}" if err else "ok")
        if err:
            await itx.followup.send(f"⚠️ {err}", ephemeral=True)
        else:
            log.info("téléchargement %s arrêté par %s", self.jid, actor)


class PlaylistConfirmView(GatedView):
    """Confirmation APRÈS annonce de la taille totale d'une playlist.

    choice : "full" (toute la playlist) / "single" (l'élément isolé, si l'URL était
    watch?v=…&list=…) / None (annulé ou expiré). Réservée au demandeur + admins.

    Porte « read » (2026-08-11), même raisonnement que DownloadView : le demandeur peut
    n'avoir que le tier LECTURE, et le bouton « Toute la playlist » lance plusieurs Gio
    de téléchargement — il mérite la session 2FA comme le reste du bot.
    """

    gate = "read"

    def __init__(self, owner_id, single_url=None, timeout=300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.single_url = single_url
        self.choice = None
        self.message = None
        if single_url is None:
            self.remove_item(self.single_btn)

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if not await super().interaction_check(itx):
            return False
        if itx.user.id == self.owner_id or is_admin(itx.client.cfg, itx):
            return True
        await itx.response.send_message("⛔ Ce n'est pas ta demande.", ephemeral=True)
        return False

    def _disable(self):
        for c in self.children:
            c.disabled = True

    async def _finish(self, itx, choice):
        self.choice = choice
        self._disable()
        await itx.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Toute la playlist", emoji="📃", style=discord.ButtonStyle.primary)
    async def full_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        await self._finish(itx, "full")

    @discord.ui.button(label="Cet élément seul", emoji="🎬", style=discord.ButtonStyle.secondary)
    async def single_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        await self._finish(itx, "single")

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, itx: discord.Interaction, _btn: discord.ui.Button):
        await self._finish(itx, None)

    async def on_timeout(self):
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class _JellyfinClient(ApiClient):
    """ApiClient Jellyfin qui rend None sur TOUTE erreur — il ne lève jamais.

    ⚠️ `core.http.request_json` construit son `urllib.request.Request` AVANT son `try` :
    une base SANS SCHÉMA remonte alors « ValueError: unknown url type » jusqu'à
    l'appelant. Or JELLYFIN_URL vaut "" par défaut (core/config.py) : sur une instance où
    Jellyfin n'est pas configuré, `autodelete` mourrait à CHAQUE cycle (relancée en
    boucle par bg.guard_cog_loops, une trace d'erreur toutes les ~5 min) et `_scan_library`
    ferait finir en erreur la tâche de suivi d'un téléchargement pourtant réussi.
    L'ancien `_jf` local attrapait tout et rendait None : ce filet rétablit exactement ce
    contrat. À SUPPRIMER si core.http finit par construire sa requête dans le `try`.
    """

    def _safe(self, call, *args, **kw):
        try:
            return call(*args, **kw)
        except Exception as e:  # noqa: BLE001 — un appel en échec vaut None, pas une exception
            log.warning("jellyfin %s: %s", getattr(self, "base", "") or "(URL absente)", e)
            return None

    def get(self, path, params=None, **kw):
        return self._safe(super().get, path, params, **kw)

    def post(self, path, body=None, **kw):
        return self._safe(super().post, path, body, **kw)

    def delete(self, path, **kw):
        return self._safe(super().delete, path, **kw)


class YouTube(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._lib_id = None
        # Client Jellyfin partagé (core.http) : timeout obligatoire, et None == APPEL EN
        # ÉCHEC et rien d'autre. La distinction est vitale ici : autodelete SAUTE tout son
        # cycle quand un listing revient None, parce qu'un « aucun item » confondu avec
        # « Jellyfin injoignable » ferait supprimer des vidéos jugées non regardées à tort.
        self._jf = _JellyfinClient(
            getattr(bot.cfg, "jellyfin_url", "") or "",
            {"Authorization": f'MediaBrowser Token="{getattr(bot.cfg, "jellyfin_api_key", "") or ""}"',
             "Accept": "application/json"},
            timeout=20, label="jellyfin")

    async def cog_load(self):
        self._migrate_delays_to_hours()
        self.autodelete.start()
        # filet anti-restart : si le bot est tombé pendant un download lancé avec un
        # délai spécifique, le job ytgrab (qui porte `del_hours`) a fini sans _track ->
        # on rejoue l'enregistrement des délais manquants au démarrage. La réconciliation
        # est REJOUÉE à chaque cycle d'autodelete (2026-08-11) : au démarrage seul, un job
        # encore actif n'était jamais rattrapé.
        # bg.spawn et non create_task : sans référence forte le GC peut ramasser la tâche.
        bg.spawn(self._reconcile_at_start(), name="youtube:reconcile", logger=log)

    async def _reconcile_at_start(self):
        """Réconciliation du démarrage. L'attente du READY reste ICI et PAS dans
        _reconcile_delays : appelée depuis autodelete, elle bloquerait le cycle de
        suppression pendant une reconnexion Discord (2026-08-11)."""
        await self.bot.wait_until_ready()
        await self._reconcile_delays()

    def _migrate_delays_to_hours(self):
        """Migre l'ancienne unité JOURS -> HEURES (2026-07-16). Idempotent."""
        st = self.bot.state
        old = st.get("yt_autodelete_days")
        if st.get("yt_autodelete_hours") is None and old is not None:
            try:
                old = int(old)
                st.set("yt_autodelete_hours", -1 if old < 0 else old * 24)
            except (TypeError, ValueError):
                pass
        d = st.get("yt_delays")
        if isinstance(d, dict):
            changed = False
            for v in d.values():
                if isinstance(v, dict) and "hours" not in v and "days" in v:
                    dd = v.pop("days")
                    try:
                        v["hours"] = -1 if int(dd) < 0 else int(dd) * 24
                    except (TypeError, ValueError):
                        v["hours"] = self._global_hours()
                    changed = True
            if changed:
                st.set("yt_delays", d)
                log.info("délais /yt migrés jours -> heures")

    def cog_unload(self):
        self.autodelete.cancel()

    # ---------------------------------------------------------------- ytgrab
    def _yt_sync(self, method, path, body=None, timeout=20):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(YTGRAB + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    async def _yt(self, method, path, body=None, timeout=20):
        """Appel ytgrab. None == injoignable ; un DICT peut être le corps d'une erreur HTTP.

        ⚠️ Volontairement PAS core.http.ApiClient (2026-08-11) : `request_json` rend None
        sur toute HTTPError, or ici le CORPS de l'erreur porte deux informations dont le
        cog dépend :
          - le diagnostic de ytgrab (« playlist refusée », « yt-dlp: … ») affiché tel quel
            dans l'embed ; le perdre transformerait chaque refus en « service injoignable » ;
          - le 404 « job inconnu » de GET /jobs/{id}, que `_track` reconnaît à l'absence de
            clé « state » : c'est le signe que ytgrab a REDÉMARRÉ et tué le yt-dlp, donc un
            ARRÊT (« ⏹️ Arrêté ») et pas une panne. Rendu None, ce cas deviendrait
            indiscernable d'un CT120 éteint et l'embed tournerait ~10 h dans le vide.
        Les deux invariants de core.http sont tenus ici : timeout toujours posé, et None
        réservé à l'échec d'appel. Duplication ASSUMÉE avec `cogs/docker._dk`, qui parle au
        même service et a le même besoin.
        """
        try:
            return await asyncio.to_thread(self._yt_sync, method, path, body, timeout)
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:  # noqa: BLE001
                return {"error": f"HTTP {e.code}"}
        except Exception as e:  # noqa: BLE001
            log.warning("ytgrab %s %s: %s", method, path, e)
            return None

    # ---------------------------------------------------------------- jellyfin
    async def _youtube_lib_id(self):
        if self._lib_id:
            return self._lib_id
        vf = await self._jf.aget("/Library/VirtualFolders")
        for f in vf or []:
            if (f.get("Name") or "").lower() == YT_LIB_NAME.lower():
                self._lib_id = f.get("ItemId")
                return self._lib_id
        return None

    # ---------------------------------------------------------------- délais (HEURES)
    def _global_hours(self):
        try:
            return int(self.bot.state.get("yt_autodelete_hours", AUTO_DELETE_HOURS))
        except (TypeError, ValueError):
            return AUTO_DELETE_HOURS

    def _delays(self):
        d = self.bot.state.get("yt_delays", {})
        return dict(d) if isinstance(d, dict) else {}

    def _store_delays(self, files, hours):
        """Mémorise le délai (en HEURES) choisi pour chaque fichier livré par le job.

        hours=None (= « réglage global ») ne crée pas d'entrée : l'item suit alors le
        réglage /yt-config COURANT, y compris s'il change après coup — c'est voulu.
        """
        if hours is None or not files:
            return
        d = self._delays()
        now = time.time()
        for bn in files:
            d[bn] = {"hours": int(hours), "ts": now}
        self.bot.state.set("yt_delays", d)

    def _delay_footer(self, hours, audio):
        if audio:
            return "conservé (la musique n'est jamais auto-supprimée)"
        h = self._global_hours() if hours is None else hours
        suffix = "" if hours is not None else " (réglage global)"
        if h < 0:
            return f"jamais supprimé automatiquement{suffix}"
        return f"auto-suppression dans {render.fmt_delay(h)} si non regardé{suffix}"

    async def _reconcile_delays(self):
        """Ré-enregistre les délais des fichiers livrés par des jobs qu'aucun _track ne
        suit plus (bot redémarré pendant le téléchargement).

        Appelée au démarrage ET à chaque cycle d'autodelete (2026-08-11) : au démarrage
        seul, un job ENCORE ACTIF à cet instant n'était jamais rattrapé — il finissait
        sans _track, son `del_hours` n'était jamais écrit, et « suppression:jamais »
        retombait en silence sur le réglage global (donc suppression au bout de 4 j).
        Les jobs actifs ne sont plus filtrés : leurs fichiers DÉJÀ terminés sont exposés
        dans `files` (comme le montre l'affichage « n terminé(s) » du suivi) et peuvent
        être clés dès maintenant.
        Limite connue : si ytgrab a AUSSI redémarré, ses jobs en RAM sont perdus."""
        jobs = await self._yt("GET", "/jobs")
        if not isinstance(jobs, list):
            return
        found = {}
        for j in jobs:
            if not isinstance(j, dict) or j.get("kind") == "audio":
                continue
            try:
                hours = int(j["del_hours"])
            except (TypeError, KeyError, ValueError):
                continue          # pas de délai spécifique (ou champ hors contrat)
            # `ts` DOIT rester un flottant epoch, comme celui de _store_delays : la purge
            # des délais orphelins d'autodelete le compare à `time.time() - 7 j`. Un
            # `finished` sérialisé en ISO-8601 par ytgrab (format jamais spécifié de notre
            # côté) y lèverait « str < float » et tuerait la boucle d'auto-suppression —
            # d'autant plus probable que la réconciliation tourne désormais tous les
            # cycles et non plus une seule fois au démarrage. (2026-08-11)
            try:
                ts = float(j.get("finished"))
            except (TypeError, ValueError):
                ts = time.time()
            for rel in j.get("files") or []:
                found[rel] = {"hours": hours, "ts": ts}
        # lecture FRAÎCHE avant écriture : le GET ci-dessus contient un await pendant
        # lequel un _store_delays (fin de download) a pu écrire — repartir de la photo
        # d'avant effacerait son entrée (lost update).
        d = self._delays()
        added = 0
        for rel, entry in found.items():
            if rel not in d:
                d[rel] = entry
                added += 1
        if added:
            self.bot.state.set("yt_delays", d)
            log.info("délais /yt réconciliés : %d fichier(s) rattrapé(s)", added)

    # ---------------------------------------------------------------- commandes
    @app_commands.command(name="yt", description="Télécharger une vidéo, rediff ou playlist YouTube/Twitch dans Jellyfin")
    @app_commands.describe(url="Lien YouTube ou Twitch (vidéo, rediffusion de live ou playlist)",
                           suppression="Délai d'auto-suppression pour CE téléchargement (défaut : réglage global)")
    @app_commands.choices(suppression=DELAY_CHOICES)
    @read_check()
    async def yt(self, itx: discord.Interaction, url: str, suppression: Optional[int] = None):
        await self._start_download(itx, url, hours=suppression)

    @app_commands.command(name="tw", description="Télécharger une rediffusion de live Twitch dans Jellyfin")
    @app_commands.describe(url="Lien de la rediffusion Twitch (VOD)",
                           suppression="Délai d'auto-suppression pour CE téléchargement (défaut : réglage global)")
    @app_commands.choices(suppression=DELAY_CHOICES)
    @read_check()
    async def tw(self, itx: discord.Interaction, url: str, suppression: Optional[int] = None):
        await self._start_download(itx, url, hours=suppression)

    @app_commands.command(name="musique", description="Télécharger une piste ou playlist YouTube en Opus (biblio Jellyfin Musique)")
    @app_commands.describe(url="Lien YouTube ou YouTube Music (piste ou playlist)")
    @read_check()
    async def musique(self, itx: discord.Interaction, url: str):
        await self._start_download(itx, url, audio=True)

    @app_commands.command(name="yt-config", description="Voir/régler le délai global d'auto-suppression des téléchargements /yt")
    @app_commands.describe(delai="Nouveau délai global (laisser vide pour afficher le réglage actuel)")
    @app_commands.choices(delai=DELAY_CHOICES)
    @admin_check(require_admin_channel=False)
    async def yt_config(self, itx: discord.Interaction, delai: Optional[int] = None):
        overrides = self._delays()
        if delai is None:
            g = self._global_hours()
            emb = discord.Embed(title="⚙️ Auto-suppression /yt", color=0x5865F2)
            emb.add_field(name="Délai global", value=render.fmt_delay(g), inline=False)
            emb.add_field(
                name="Délais par téléchargement",
                value=(f"{len(overrides)} fichier(s) avec un délai spécifique "
                       "(choisi via le paramètre `suppression` de /yt)."
                       if overrides else
                       "Aucun — tout suit le réglage global."), inline=False)
            emb.set_footer(text="La musique (/musique) n'est jamais auto-supprimée.")
            await itx.response.send_message(embed=emb, ephemeral=True)
            return
        self.bot.state.set("yt_autodelete_hours", int(delai))
        self.bot.audit.record(user=f"{itx.user} ({itx.user.id})", action="yt-config",
                              target=f"delai={render.fmt_delay(delai)}", result="ok")
        emb = discord.Embed(
            title="⚙️ Auto-suppression /yt",
            description=f"Délai global réglé sur **{render.fmt_delay(delai)}**.\n"
                        "S'applique aux téléchargements sans délai spécifique "
                        "(existants comme futurs).",
            color=0x2ECC71)
        await itx.response.send_message(embed=emb)

    @app_commands.command(name="dl", description="Téléchargements en cours : reprendre le suivi et le pilotage.")
    @admin_check(require_admin_channel=False)
    async def dl(self, itx: discord.Interaction):
        """Filet de sécurité après un redémarrage du bot.

        Les boutons d'un embed /yt vivent dans la RAM du bot (la vue n'est pas persistante)
        et _track est une tâche asyncio : un redémarrage du bot laisse l'embed figé avec des
        boutons morts, alors que ytgrab continue de télécharger. /dl reconstruit un panneau
        neuf et rebranche le suivi sur les jobs encore actifs.
        """
        await itx.response.defer(ephemeral=True)
        jobs = await self._yt("GET", "/jobs")
        if not isinstance(jobs, list):
            await itx.followup.send("⚠️ ytgrab injoignable.", ephemeral=True)
            return
        actifs = [j for j in jobs if j.get("state") in ACTIVE]
        if not actifs:
            await itx.followup.send("Aucun téléchargement en cours.", ephemeral=True)
            return
        await itx.followup.send(f"{len(actifs)} téléchargement(s) en cours — panneaux ci-dessous.",
                                ephemeral=True)
        for j in actifs:
            jid, url = j["id"], j.get("url", "")
            audio = j.get("kind") == "audio"
            emb = discord.Embed(title=self._title(audio), color=0xE5A50A,
                                description=f"[Lien]({url})")
            emb.add_field(name="État", value="Reprise du suivi…", inline=False)
            emb.set_footer(text=f"job {jid} · lancé par {j.get('actor', '?')}")
            view = DownloadView(self, jid, itx.user.id)
            view.sync(j.get("state"))
            try:
                msg = await itx.channel.send(embed=emb, view=view)   # vrai Message = pas de péremption
            except discord.HTTPException:
                continue
            bg.spawn(self._track(msg, jid, url, view, j.get("actor", "?"),
                                 hours=j.get("del_hours"), audio=audio,
                                 playlist=bool(j.get("playlist"))),
                     name=f"youtube:track:{jid}", logger=log)

    # ---------------------------------------------------------------- lancement
    @staticmethod
    def _title(audio):
        return "🎵 Téléchargement musique" if audio else "⬇️ Téléchargement"

    async def _start_download(self, itx: discord.Interaction, url: str, *,
                              audio=False, hours=None):
        # `url` est normalisée UNE fois, puis c'est CETTE chaîne qui circule partout :
        # valider une copie (.strip()) et relayer l'originale rouvrait l'écart entre la
        # valeur contrôlée et celle envoyée à ytgrab (2026-08-11).
        url = url.strip() if isinstance(url, str) else ""
        # Liste blanche AVANT tout effet de bord — et avant le defer, pour répondre en
        # éphémère sans laisser un « le bot réfléchit… » public en suspens (2026-08-11).
        if not _valid_url(url):
            await itx.response.send_message(
                "⛔ Seuls les liens **YouTube** et **Twitch** en http(s) sont acceptés "
                "(et de moins de 500 caractères).", ephemeral=True)
            return
        await itx.response.defer()
        if _playlist_intent(url):
            await self._flow_playlist(itx, url, audio=audio, hours=hours)
            return
        emb = discord.Embed(title=self._title(audio), description=f"[Lien]({url})",
                            color=0xE5A50A)
        emb.add_field(name="État", value="Lancement…", inline=False)
        msg = await itx.followup.send(embed=emb)
        msg = await self._durable(itx, msg)
        await self._launch(msg, url, itx.user, audio=audio, playlist=False, hours=hours)

    async def _launch(self, msg, url, user, *, audio, playlist, hours):
        """POST /download puis transforme `msg` en panneau de progression suivi."""
        actor = user.display_name
        action = "yt-musique" if audio else "yt-download"
        # Défense en profondeur (2026-08-11) : _launch et _probe sont les deux SEULS
        # points qui parlent vraiment à ytgrab. La garde est répétée ici parce que
        # _flow_playlist rejoue une URL (« cet élément seul ») bien après le contrôle
        # d'entrée de _start_download.
        if not _valid_url(url):
            emb = discord.Embed(title=self._title(audio), color=0xE74C3C)
            emb.add_field(name="⛔ Lien refusé",
                          value="Seuls les liens YouTube et Twitch en http(s) sont acceptés.",
                          inline=False)
            self.bot.audit.record(user=f"{user} ({user.id})", action=action,
                                  target=str(url)[:200], result="refusé: lien non autorisé")
            await self._safe_edit(msg, emb, None)
            return
        # `del_hours` voyage avec le JOB ytgrab : il survit ainsi à un restart du bot
        # (relu par /dl et par la réconciliation du démarrage).
        r = await self._yt("POST", "/download",
                           {"url": url, "actor": actor, "audio": audio,
                            "playlist": playlist, "del_hours": hours})
        if not r or r.get("error") or "id" not in r:
            # `or` et non un défaut de .get() : ytgrab peut renvoyer error=None
            # (embed avec un champ vide -> 400 côté Discord).
            why = (r or {}).get("error") or "service injoignable"
            emb = discord.Embed(title=self._title(audio), color=0xE74C3C,
                                description=f"[Lien]({url})")
            emb.add_field(name="⚠️ Impossible de lancer", value=str(why)[:1024], inline=False)
            self.bot.audit.record(user=f"{user} ({user.id})", action=action,
                                  target=url[:200], result=f"error: {str(why)[:120]}")
            await self._safe_edit(msg, emb, None)
            return
        jid = r["id"]
        # _launch est l'entonnoir UNIQUE (/yt, /tw, /musique, les trois branches playlist
        # et /dl) : un seul enregistrement y couvre tout sans doublon. `actor` est un
        # display_name modifiable — seule cette ligne porte un user id stable (2026-08-11).
        self.bot.audit.record(user=f"{user} ({user.id})", action=action,
                              target=url[:200], result=f"job {jid}")
        emb = discord.Embed(title=self._title(audio), description=f"[Lien]({url})",
                            color=0xE5A50A)
        emb.add_field(name="État", value="En file d'attente…", inline=False)
        emb.set_footer(text=f"job {jid} · lancé par {actor}")
        view = DownloadView(self, jid, user.id)
        view.sync("queued")
        await self._safe_edit(msg, emb, view)
        bg.spawn(self._track(msg, jid, url, view, actor,
                             hours=hours, audio=audio, playlist=playlist),
                 name=f"youtube:track:{jid}", logger=log)

    # ---------------------------------------------------------------- playlists
    async def _probe(self, url, audio):
        # même garde que _launch : le sondage fait DÉJÀ sortir une requête de CT120
        # vers l'hôte visé (2026-08-11).
        if not _valid_url(url):
            return {"state": "error",
                    "error": "lien non autorisé (YouTube/Twitch en http(s) uniquement)"}
        r = await self._yt("POST", "/probe", {"url": url, "audio": audio})
        if not r or r.get("error") or "id" not in r:
            # `or` et non un défaut de .get() : une réponse « error: null » (ou sans « id »)
            # renvoyait error=None, alors que le contrat de _probe est une chaîne.
            return {"state": "error",
                    "error": (r or {}).get("error") or "service injoignable"}
        pid = r["id"]
        for _ in range(150):                    # 150 × 2 s = 5 min max
            await asyncio.sleep(2)
            p = await self._yt("GET", f"/probe/{pid}")
            if p and p.get("state") in ("done", "error"):
                return p
        return {"state": "error", "error": "délai de sondage dépassé"}

    async def _flow_playlist(self, itx, url, *, audio, hours):
        """Playlist = JAMAIS de téléchargement direct : sondage, annonce de la taille
        totale, puis confirmation explicite (règle « annoncer avant de télécharger »)."""
        emb = discord.Embed(
            title="🔍 Analyse de la playlist…",
            description=f"[Lien]({url})\nÉnumération et estimation de la taille "
                        "(rien n'est téléchargé). ~30 s.",
            color=0x5865F2)
        msg = await itx.followup.send(embed=emb)
        msg = await self._durable(itx, msg)
        p = await self._probe(url, audio)
        if not p or p.get("state") != "done":
            emb = discord.Embed(title="❌ Sondage impossible", color=0xE74C3C,
                                description=f"[Lien]({url})")
            # `or` : un sondage en erreur peut porter error=None -> None[:300] planterait
            # la commande au lieu d'afficher l'échec (2026-08-11).
            emb.add_field(name="Erreur",
                          value=f"`{str((p or {}).get('error') or 'inconnue')[:300]}`",
                          inline=False)
            await self._safe_edit(msg, emb, None)
            return
        if not p.get("playlist"):
            # le paramètre list= ne menait pas à une vraie playlist -> download simple
            await self._launch(msg, url, itx.user, audio=audio, playlist=False, hours=hours)
            return

        count = p.get("count") or 0
        emb = discord.Embed(
            title=("🎵 Playlist musique détectée" if audio else "📃 Playlist détectée"),
            description=f"**{str(p.get('title') or 'Playlist')[:150]}**\n[Lien]({url})",
            color=0xE5A50A)
        emb.add_field(name="Contenu",
                      value=f"**{count}** élément(s) · durée totale {render.fmt_dur(p.get('duration'))}"
                            + (" · liste tronquée à 500" if p.get("truncated") else ""),
                      inline=False)
        emb.add_field(name="Taille totale estimée", value=render.size_estimate_text(p),
                      inline=False)
        apercu = render.entries_preview(p, count)
        if apercu:
            emb.add_field(name="Aperçu", value=apercu, inline=False)
        if (p.get("size_est") or 0) > 30 * 1024 ** 3:
            emb.add_field(name="⚠️ Gros volume",
                          value="Plus de 30 Gio — vérifie l'espace libre avant de confirmer "
                                "(/mnt/media est déjà bien rempli).", inline=False)
        emb.set_footer(text="Rien ne se télécharge sans confirmation.")
        single_url = _strip_playlist(url)
        view = PlaylistConfirmView(itx.user.id, single_url=single_url)
        view.message = msg
        ok = await self._safe_edit(msg, emb, view)
        if not ok:
            return
        await view.wait()
        if view.choice == "full":
            await self._launch(msg, url, itx.user, audio=audio, playlist=True, hours=hours)
        elif view.choice == "single" and single_url:
            await self._launch(msg, single_url, itx.user, audio=audio, playlist=False, hours=hours)
        else:
            emb.color = 0x95A5A6
            emb.set_footer(text="Annulé — rien n'a été téléchargé.")
            await self._safe_edit(msg, emb, None)

    @staticmethod
    async def _durable(itx, msg):
        """Reconvertit le WebhookMessage de followup.send en Message ordinaire.

        ⚠️ CRUCIAL : followup.send renvoie un WebhookMessage dont les edits passent par
        PATCH /webhooks/{app_id}/{TOKEN_D_INTERACTION}/messages/{id} — or ce token expire
        au bout de 15 MINUTES. Un téléchargement de plusieurs heures verrait donc son embed
        se figer à 15 min, et les edits terminaux lèveraient NotFound (non attrapé) en tuant
        la tâche de suivi. Un Message ordinaire s'édite via PATCH /channels/{cid}/messages/{id}
        avec le jeton du BOT : aucune expiration.
        """
        try:
            return await itx.channel.fetch_message(msg.id)
        except (discord.HTTPException, AttributeError):
            return msg  # au pire on garde l'ancien handle (suivi limité à 15 min)

    # ---------------------------------------------------------------- suivi
    async def _track(self, msg, jid, url, view, actor, hours=None, audio=False, playlist=False):
        lost = 0
        # ~10 h de suivi pour une vidéo ; une playlist peut durer bien plus longtemps
        budget = 20000 if playlist else 4500
        lib = MUSIC_LIB_NAME if audio else YT_LIB_NAME
        title = self._title(audio)
        while budget > 0:
            await asyncio.sleep(POLL_SECONDS)
            j = await self._yt("GET", f"/jobs/{jid}")
            if j is None:
                # ytgrab injoignable -> transitoire, on réessaie. Mais on CONSOMME du
                # budget (2026-08-11) : sans ça, un CT120 éteint rendait la tâche de
                # suivi immortelle (le garde-fou des ~10 h était neutralisé exactement
                # dans le cas où il sert) et chaque /yt d'avant la panne laissait un
                # poller à vie occupant un thread du pool partagé de to_thread.
                budget -= 1
                continue
            if "state" not in j:
                # _yt renvoie le CORPS d'une erreur HTTP (ex. 404 « job inconnu ») : ce n'est
                # PAS un job. Un 404 franc = ytgrab a redémarré et _sweep_orphans a tué le
                # yt-dlp -> c'est un ARRÊT, pas une panne. Ne jamais afficher « Échec » ici.
                lost += 1
                if lost < 3:
                    continue
                emb = discord.Embed(title=title, color=0x95A5A6,
                                    description=f"[Lien]({url})")
                emb.add_field(
                    name="⏹️ Arrêté",
                    value="Le service de téléchargement a redémarré, le job a été interrompu.\n"
                          "Relance avec `/yt`, `/tw` ou `/musique`.", inline=False)
                emb.set_footer(text=f"job {jid}")
                view.freeze()
                await self._safe_edit(msg, emb, view)
                return
            lost = 0
            state = j.get("state")
            if state != "paused":
                budget -= 1
            emb = discord.Embed(title=title, description=f"[Lien]({url})")
            fn = j.get("filename", "")
            files = j.get("files") or []
            view.sync(state)

            if state in ACTIVE:
                emb.color = 0x5865F2 if state == "paused" else 0xE5A50A
                emb.add_field(name="Progression",
                              value=render.progress_text(j, files), inline=False)
                if fn:
                    emb.add_field(name="Fichier", value=f"`{fn[:90]}`", inline=False)

            elif state == "done":
                emb.color = 0x2ECC71
                nb = len(files) if files else 1
                emb.add_field(
                    name="✅ Terminé",
                    value=(f"**{nb} fichier(s)** disponibles" if nb > 1 else "Disponible")
                          + f" dans Jellyfin → **{lib}** (peut prendre ~1 min à apparaître).",
                    inline=False)
                if fn:
                    emb.add_field(name="Fichier" if nb == 1 else "Dernier fichier",
                                  value=f"`{fn[:90]}`", inline=False)
                emb.set_footer(text=f"job {jid} · {self._delay_footer(hours, audio)}")
                view.freeze()
                await self._safe_edit(msg, emb, view)
                if not audio:
                    self._store_delays(files or ([fn] if fn else []), hours)
                await self._scan_library()
                return

            elif state == "cancelled":
                # arrêt VOLONTAIRE : ce n'est pas un échec, et on dit par QUI.
                emb.color = 0x95A5A6
                who = j.get("stopped_by") or "?"
                txt = f"Arrêté par **{who}** à {j.get('percent', '?')}."
                cleaned = j.get("cleaned")
                if cleaned:
                    txt += f"\nFichiers partiels supprimés ({cleaned})."
                if files:
                    txt += f"\n**{len(files)}** fichier(s) déjà terminés sont conservés."
                txt += "\nRelance avec `/yt`, `/tw` ou `/musique` pour repartir."
                emb.add_field(name="⏹️ Arrêté", value=txt, inline=False)
                emb.set_footer(text=f"job {jid}")
                view.freeze()
                await self._safe_edit(msg, emb, view)
                if files:
                    if not audio:
                        self._store_delays(files, hours)
                    await self._scan_library()
                return

            elif state == "error" and files:
                # playlist partiellement réussie (--ignore-errors) : PAS un échec sec.
                emb.color = 0xE67E22
                emb.add_field(
                    name="⚠️ Terminé avec des erreurs",
                    value=f"**{len(files)}** fichier(s) téléchargés dans **{lib}**, "
                          f"mais certains éléments ont échoué.\n"
                          f"`{(j.get('error') or 'détail indisponible')[:300]}`",
                    inline=False)
                emb.set_footer(text=f"job {jid} · {self._delay_footer(hours, audio)}")
                view.freeze()
                await self._safe_edit(msg, emb, view)
                if not audio:
                    self._store_delays(files, hours)
                await self._scan_library()
                return

            else:  # error = vraie panne uniquement
                emb.color = 0xE74C3C
                emb.add_field(name="❌ Échec",
                              value=f"`{(j.get('error') or j.get('last') or 'erreur inconnue')[:400]}`",
                              inline=False)
                view.freeze()
                await self._safe_edit(msg, emb, view)
                return

            emb.set_footer(text=f"job {jid} · lancé par {actor}")
            await self._safe_edit(msg, emb, view)

        # budget épuisé : on gèle plutôt que d'abandonner en laissant des boutons
        # actifs sous un embed qui ne bougera plus jamais.
        emb = discord.Embed(title=title, color=0x95A5A6,
                            description=f"[Lien]({url})")
        emb.add_field(name="⏱️ Suivi interrompu",
                      value=f"Le suivi s'arrête après ~10 h (~44 h pour une playlist). "
                            f"Le téléchargement, lui, peut continuer.\nÉtat réel : "
                            f"`pct exec 120 -- ytgrab-ctl list` (job `{jid}`).", inline=False)
        view.freeze()
        await self._safe_edit(msg, emb, view)

    @staticmethod
    async def _safe_edit(msg, emb, view):
        """Un edit qui échoue ne doit jamais tuer la tâche de suivi."""
        try:
            if view is None:
                await msg.edit(embed=emb, view=None)
            else:
                await msg.edit(embed=emb, view=view)
            return True
        except discord.HTTPException as e:
            log.warning("edit embed impossible: %s", e)
            return False

    async def _scan_library(self):
        # déclenche un scan de bibliothèque pour que Jellyfin détecte le nouveau fichier
        await self._jf.apost("/Library/Refresh")

    # ---------------------------------------------------------------- auto-delete
    @tasks.loop(minutes=15)
    async def autodelete(self):
        # Rattrapage des délais AVANT de décider des suppressions : un job encore en cours
        # au démarrage du bot n'était réconcilié nulle part, et son « suppression:jamais »
        # se perdait (2026-08-11). Encadré : une réconciliation en échec ne doit pas
        # empêcher le cycle de suppression (ni tuer la boucle).
        try:
            await self._reconcile_delays()
        except Exception:  # noqa: BLE001
            log.exception("autodelete: réconciliation des délais en échec")
        lib = await self._youtube_lib_id()
        if not lib:
            return
        # récupère les users réels (pour savoir si "quelqu'un a commencé")
        users = await self._jf.aget("/Users") or []
        uids = [u.get("Id") for u in users if u.get("Id")]
        if not uids:
            return
        # items + UserData agrégé par item
        started = {}      # itemId -> bool (visionnage commencé par n'importe qui)
        meta = {}         # itemId -> {name, path, created}
        for uid in uids:
            data = await self._jf.aget(
                f"/Users/{uid}/Items?parentId={lib}&recursive=true"
                f"&includeItemTypes=Video,Movie,Episode&fields=DateCreated,Path,MediaSources"
                f"&enableUserData=true")
            if data is None:
                # ⚠️ échec PARTIEL = danger : l'item pourrait n'être « commencé » que
                # dans la vue du user en échec -> on le supprimerait à tort. On saute
                # tout le cycle, le prochain (1 h) réessaiera.
                log.warning("autodelete: Jellyfin injoignable pour un user, cycle sauté")
                return
            for it in (data or {}).get("Items", []):
                iid = it.get("Id")
                if not iid:
                    continue
                meta.setdefault(iid, {
                    "name": it.get("Name", "?"),
                    "path": (it.get("Path") or ""),
                    "created": it.get("DateCreated"),
                })
                ud = it.get("UserData") or {}
                if ud.get("Played") or (ud.get("PlaybackPositionTicks") or 0) > 0 or (ud.get("PlayCount") or 0) > 0:
                    started[iid] = True
        now = datetime.now(timezone.utc)
        g = self._global_hours()
        delays = self._delays()          # photo pour les DÉCISIONS de ce cycle
        removed_keys = set()             # entrées consommées (fichier supprimé)
        for iid, m in meta.items():
            if started.get(iid):
                continue  # visionnage commencé -> suppression manuelle uniquement
            path = m["path"]
            if not path.startswith(YT_MEDIA_PREFIX):
                continue  # GARDE-FOU : jamais hors /mnt/media/YouTube
            # clé = chemin RELATIF à la biblio (sous-dossier de playlist inclus) :
            # le basename seul entrerait en collision entre deux playlists.
            key = path[len(YT_MEDIA_PREFIX):].lstrip("/")
            ov = delays.get(key)
            try:
                d = int(ov["hours"]) if ov else g
            except (TypeError, KeyError, ValueError):
                d = g
            if d < 0:
                continue  # « jamais » (par item ou global)
            created = self._parse_dt(m["created"])
            if created is None or created > now - timedelta(hours=d):
                continue
            ok = await self._jf.adelete(f"/Items/{iid}")
            if ok is None:
                log.warning("échec suppression item %s (%s)", iid, m["name"])
                continue
            if ov:
                removed_keys.add(key)
            log.info("auto-suppr YouTube non-regardé >%dh: %s", d, m["name"])
            await self._notify(
                f"🗑️ **Supprimé** (non regardé depuis {render.fmt_delay(d)}) : {m['name']}")
        # Écriture : on repart d'une lecture FRAÎCHE de l'état — la boucle ci-dessus
        # contient des awaits pendant lesquels un _store_delays (fin de download) a pu
        # écrire ; réécrire notre photo effacerait son entrée (lost update).
        known = {m["path"][len(YT_MEDIA_PREFIX):].lstrip("/")
                 for m in meta.values()
                 if (m.get("path") or "").startswith(YT_MEDIA_PREFIX)}
        cut = time.time() - 7 * 86400
        cur = self._delays()
        dirty = False
        for key in removed_keys:
            dirty |= cur.pop(key, None) is not None
        # purge des délais orphelins : fichier absent de Jellyfin depuis > 7 j (marge
        # pour ceux qui attendent leur premier scan). Le listing a réussi pour TOUS
        # les users (sinon on a `return` plus haut), biblio vide comprise.
        for key in list(cur):
            if key in known:
                continue
            # `ts` relu défensivement (2026-08-11) : state.json peut contenir des entrées
            # héritées d'un `finished` non numérique, et « str < float » lèverait ici —
            # dans le corps NON encadré de la boucle, donc auto-suppression morte jusqu'au
            # prochain redémarrage. Une entrée hors contrat est traitée comme périmée.
            try:
                ts = float((cur[key] or {}).get("ts") or 0)
            except (TypeError, ValueError, AttributeError):
                ts = 0.0
            if ts < cut:
                cur.pop(key, None)
                dirty = True
        if dirty:
            self.bot.state.set("yt_delays", cur)

    @autodelete.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _parse_dt(s):
        if not s:
            return None
        try:
            s = s.strip().replace("Z", "+00:00")
            # Jellyfin émet parfois >6 chiffres de fraction -> tronquer à 6 (garde le suffixe TZ)
            m = re.match(r"^(.*?\.\d{1,6})\d*([+\-].*)?$", s)
            if m:
                s = m.group(1) + (m.group(2) or "")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:  # noqa: BLE001
            return None

    async def _notify(self, text):
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return
        # La catégorie vient de core.channels, SOURCE UNIQUE DE VÉRITÉ : le nom en dur
        # « 📊 Supervision » était PÉRIMÉ depuis le passage multi-serveurs (provision crée
        # « 📊 Supervision <SERVER_KEY> »), donc introuvable, donc salon créé à la racine
        # et lisible de tout le serveur (2026-08-11).
        # ensure_channel porte la règle : PAS de catégorie -> PAS de salon. Un journal de
        # téléchargements manquant se répare au prochain cycle de provisioning ; un journal
        # public, non.
        ch = await channels.ensure_channel(
            self.bot, guild, NOTICE_CHANNEL,
            channels.supervision_category(self.bot, guild),
            topic="Téléchargements YouTube à la demande (/yt)")
        if ch is None:
            # ne pas avaler : sans ce salon, une suppression automatique reste totalement
            # invisible pour l'utilisateur (2026-08-11).
            log.warning("suppression automatique non annoncée : salon #%s indisponible",
                        NOTICE_CHANNEL)
            return
        try:
            await ch.send(text)
        except discord.HTTPException as e:
            log.warning("notification /yt non envoyée dans #%s: %s", NOTICE_CHANNEL, e)


async def setup(bot):
    await bot.add_cog(YouTube(bot))
