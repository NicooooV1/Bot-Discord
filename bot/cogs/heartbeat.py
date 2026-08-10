"""Heartbeat externe (dead-man's-switch).

Le bot « ping » un service EXTERNE (healthchecks.io / ntfy / UptimeRobot) toutes les
minutes. Si le R820 — ou son réseau, ou son courant — tombe, le bot cesse de pinger et
le service EXTERNE alerte l'utilisateur (mail/push/SMS). C'est le SEUL moyen de détecter
une panne totale du homelab, puisque toute la stack de supervision (bot, InfluxDB,
Grafana, Loki, mailserver) vit sur ce même R820.

Activation : renseigner HEARTBEAT_URL (l'URL de ping d'un check healthchecks.io gratuit).
Si vide, le cog ne fait rien.
"""
import asyncio
import logging
import urllib.request

from discord.ext import commands, tasks

log = logging.getLogger("discord-bot.heartbeat")


class Heartbeat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.url = (getattr(bot.cfg, "heartbeat_url", "") or "").strip()
        self._fails = 0
        if self.url:
            self.beat.start()
            log.info("heartbeat externe activé (%s…)", self.url[:40])
        else:
            log.info("heartbeat externe désactivé (HEARTBEAT_URL vide)")

    def cog_unload(self):
        if self.url:
            self.beat.cancel()

    @tasks.loop(seconds=60)
    async def beat(self):
        def _ping():
            req = urllib.request.Request(self.url, headers={"User-Agent": "edmine-heartbeat"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status
        try:
            await asyncio.to_thread(_ping)
            self._fails = 0
        except Exception as e:
            self._fails += 1
            # on ne log qu'occasionnellement pour ne pas spammer si l'externe est down
            if self._fails in (1, 5, 30):
                log.warning("heartbeat non transmis (%d échecs) : %s", self._fails, e)

    @beat.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Heartbeat(bot))
