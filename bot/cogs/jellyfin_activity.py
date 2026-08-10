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
import asyncio
import json
import logging
import urllib.request

import discord
from discord.ext import commands, tasks

log = logging.getLogger("discord-bot.jellyfinactivity")

POLL_MINUTES = 2
PAGE_SIZE = 50

_TYPE_EMOJI = {
    "sessionstart": "🟢",
    "sessionended": "🔴",
    "playbackstart": "▶️",
    "playbackstopped": "⏹️",
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
        if self.channel_id and self.enabled:
            self.poll.start()

    @property
    def enabled(self):
        return bool(getattr(self.cfg, "jellyfin_logs_enabled", True)
                     and self.cfg.jellyfin_url and self.cfg.jellyfin_api_key)

    def cog_unload(self):
        self.poll.cancel()

    # ------------------------------------------------------------------ API

    def _fetch(self):
        url = (f"{self.cfg.jellyfin_url}/System/ActivityLog/Entries"
               f"?startIndex=0&limit={PAGE_SIZE}")
        req = urllib.request.Request(
            url, headers={"Authorization": f'MediaBrowser Token="{self.cfg.jellyfin_api_key}"'})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read()).get("Items") or []

    def _format(self, entry):
        when = (entry.get("Date") or "")[:19].replace("T", " ")
        who = entry.get("Name") or "?"
        overview = entry.get("ShortOverview") or entry.get("Overview") or ""
        line = f"{_emoji_for(entry)} `{when}` {who}"
        if overview and overview != who:
            line += f" — {overview}"
        return line[:500]

    # ------------------------------------------------------------------ boucle

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        if not (self.channel_id and self.enabled):
            return
        try:
            items = await asyncio.to_thread(self._fetch)
        except Exception:
            log.debug("Jellyfin ActivityLog indisponible", exc_info=True)
            return
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
        if not new:
            return
        ch = self.bot.get_channel(self.channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(self.channel_id)
            except Exception:
                log.warning("salon jellyfin-logs %s introuvable", self.channel_id)
                return
        for entry in new[:20]:      # garde-fou anti-flood sur un rattrapage ponctuel
            try:
                await ch.send(self._format(entry))
            except discord.HTTPException:
                break
        self._last_id = new[-1]["Id"]
        self.bot.state.set("jellyfin_activity_last_id", self._last_id)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(JellyfinActivity(bot))
