"""Heartbeat externe (dead-man's-switch).

Le bot « ping » un service EXTERNE (healthchecks.io / ntfy / UptimeRobot) toutes les
minutes. Si le R820 — ou son réseau, ou son courant — tombe, le bot cesse de pinger et
le service EXTERNE alerte l'utilisateur (mail/push/SMS). C'est le SEUL moyen de détecter
une panne totale du homelab, puisque toute la stack de supervision (bot, InfluxDB,
Grafana, Loki, mailserver) vit sur ce même R820.

Le ping est aussi conditionné à la SANTÉ DE LA PASSERELLE Discord (2026-08-11) : un bot
vivant mais muet — Internet joignable, Discord seul injoignable, discord.py en boucle de
reconnexion — laissait le check au vert alors que plus aucune commande, alerte ni
rapport ne fonctionnait. Les autres pannes se détectaient déjà toutes seules (WAN coupé =
le ping ne part pas non plus ; token révoqué = `bot.run()` remonte et le processus meurt).

⚠️ Ne PAS garder la passerelle avec `is_closed()` (vrai seulement après un `close()`
explicite), `is_ready()` (pas systématiquement remis à zéro sur un RESUME) ni
`latency` (figé sur sa dernière valeur quand la passerelle meurt : ni NaN, ni croissant).
On suit donc nous-mêmes les évènements connect/disconnect/resumed.

Activation : renseigner HEARTBEAT_URL (l'URL de ping d'un check healthchecks.io gratuit).
Si vide, le cog ne fait rien.
"""
import asyncio
import logging
import time
import urllib.parse
import urllib.request

from discord.ext import commands, tasks

log = logging.getLogger("discord-bot.heartbeat")

# Au-delà de ce délai sans passerelle, on cesse de pinguer : c'est le silence qui alerte.
GW_GRACE_S = 180


class Heartbeat(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.url = (getattr(bot.cfg, "heartbeat_url", "") or "").strip()
        self._fails = 0
        # état de la passerelle. On démarre « connectée » : le cog n'est chargé qu'après
        # l'ouverture de la session, et `before_loop` attend `wait_until_ready()`.
        self._gw_ok = True
        self._gw_lost_at = 0.0
        self._skipped = 0
        if self.url:
            self.beat.start()
            # l'UUID du check ne doit pas partir dans les logs (donc dans Loki) : qui le
            # lit peut pinguer le check à la place du bot, et donc MASQUER une panne.
            log.info("heartbeat externe activé (%s)",
                     urllib.parse.urlparse(self.url).hostname or "?")
        else:
            log.info("heartbeat externe désactivé (HEARTBEAT_URL vide)")

    def cog_unload(self):
        if self.url:
            self.beat.cancel()

    # ------------------------------------------------------------ santé passerelle
    @commands.Cog.listener()
    async def on_connect(self):
        self._gw_ok = True

    @commands.Cog.listener()
    async def on_resumed(self):
        self._gw_ok = True

    @commands.Cog.listener()
    async def on_disconnect(self):
        if self._gw_ok:
            self._gw_lost_at = time.monotonic()
        self._gw_ok = False

    def _gateway_alive(self):
        """Vrai tant que la passerelle est là — ou l'a été il y a moins de GW_GRACE_S.

        La grâce évite de couper le heartbeat sur le va-et-vient normal
        disconnect/connect d'une simple reconnexion."""
        if self._gw_ok:
            return True
        return (time.monotonic() - self._gw_lost_at) < GW_GRACE_S

    @tasks.loop(seconds=60)
    async def beat(self):
        if not self._gateway_alive():
            # pas de ping = le service externe alerte, ce qui est EXACTEMENT ce qu'on
            # veut quand le bot ne peut plus rien dire sur Discord.
            self._skipped += 1
            if self._skipped <= 3 or self._skipped % 30 == 0:
                log.warning("passerelle Discord perdue depuis %.0f s — heartbeat "
                            "volontairement NON transmis (%d cycles)",
                            time.monotonic() - self._gw_lost_at, self._skipped)
            return
        self._skipped = 0

        # urlopen local, et PAS core.http (2026-08-11) : un check healthchecks.io répond
        # « OK » en texte brut. `request_json` renverrait donc None — c'est-à-dire
        # « appel en échec » selon son invariant — sur un ping parfaitement transmis, et
        # `self._fails` grimperait indéfiniment en journalisant de fausses pannes. Ici
        # seul le CODE HTTP compte, pas le corps : il n'y a rien à factoriser.
        def _ping():
            req = urllib.request.Request(self.url, headers={"User-Agent": "edmine-heartbeat"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status
        try:
            await asyncio.to_thread(_ping)
            self._fails = 0
        except Exception as e:
            self._fails += 1
            # on ne log qu'occasionnellement pour ne pas spammer si l'externe est down,
            # mais SANS jamais devenir totalement muet : l'ancien `in (1, 5, 30)`
            # n'émettait plus rien passé la 30e minute d'échec (2026-08-11).
            if self._fails <= 5 or self._fails % 60 == 0:
                log.warning("heartbeat non transmis (%d échecs) : %s", self._fails, e)

    @beat.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Heartbeat(bot))
