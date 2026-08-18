"""Daily report at REPORT_HOUR:REPORT_MINUTE (Europe/Paris) + catch-up loop
(the host is off at night, so a missed scheduled fire is replayed once on power-on).

Le rattrapage vit dans une boucle `catchup` de 5 min, PAS dans `on_ready` : le salon de
rapports est câblé par `provision._rewire()` à la toute fin du provisioning, bien après
que `on_ready` ait été dispatché — un rattrapage tenté une seule fois au démarrage lisait
un id vide et se marquait « fait » (corrigé 2026-08-11). La boucle réessaie jusqu'à ce
que le salon existe, puis devient un no-op (elle NE s'arrête PAS : une boucle arrêtée est
comptée « en échec » par bg.REGISTRY, cf. le docstring de `catchup`)."""
import asyncio
import datetime as dt
import logging

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core import render

log = logging.getLogger("discord-bot.reports")

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


class Reports(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tz = None
        if ZoneInfo:
            try:
                self.tz = ZoneInfo(bot.cfg.tz)
            except Exception:
                self.tz = dt.timezone.utc
        else:
            self.tz = dt.timezone.utc
        self._catchup_done = False
        self._catchup_tries = 0
        self._avy_tries = 0
        self._avy_try_day = None
        t = dt.time(hour=bot.cfg.report_hour, minute=bot.cfg.report_minute, tzinfo=self.tz)
        self.daily.change_interval(time=t)
        self.daily.start()
        self.catchup.start()

    def cog_unload(self):
        self.daily.cancel()
        self.catchup.cancel()

    async def build_report(self, title):
        bot = self.bot
        emb = discord.Embed(title=f"📅 {title}", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        file = None
        if not bot.influx.enabled:
            emb.description = "⚠️ InfluxDB non configuré."
            return emb, file

        cpu = await bot.influx.host_cpu_series("24h")
        mem = await bot.influx.host_mem_series("24h")
        load = await bot.influx.host_load()
        line = []
        if cpu[1]:
            line.append(f"CPU moy/max {sum(cpu[1]) / len(cpu[1]):.0f}/{max(cpu[1]):.0f}%")
        if mem[1]:
            line.append(f"RAM max {max(mem[1]):.0f}%")
        if load:
            line.append(f"load {float(load.get('load1') or 0):.2f}")
        emb.add_field(name=f"Hôte {bot.cfg.pve_node} (24h)", value=" · ".join(line) or "—", inline=False)

        cts = await bot.influx.ct_table()
        up = [c for c in cts if c["running"]]
        emb.add_field(name="Conteneurs", value=f"{len(up)} actifs / {len(cts)}", inline=True)

        st = await bot.influx.storages()
        worst = st[0] if st else None
        if worst:
            emb.add_field(name="Stockage le + plein",
                          value=f"{worst['name']} — {fmt.pct_of(worst['used'], worst['total'])}",
                          inline=True)
        tp = await bot.influx.thinpool()
        if tp and tp.get("overcommit_percent") is not None:
            emb.add_field(name="Thinpool overcommit",
                          value=f"{tp['overcommit_percent']:.0f}% "
                                f"({fmt.humanize_bytes(tp.get('allocated_bytes'))} alloués / "
                                f"{fmt.humanize_bytes(tp.get('pool_bytes'))} pool)", inline=True)

        ctrl, summ = await bot.influx.raid()
        if ctrl:
            disks = await bot.influx.raid_disks()
            worst_gd = max((int(d.get("grown_defects", 0) or 0) for d in disks), default=0)
            # 2026-08-11 : le rapport ne regardait QUE vd_optimal. Un disque tombé alors
            # que le VD reste « optimal » (hot spare déjà reconstruit) n'apparaissait
            # nulle part — alors que /raid, /alerts et /status le signalent tous.
            offline = [str(d.get("slot")) for d in disks if not d.get("online")]
            value = ("optimal ✅" if ctrl.get("vd_optimal") else "⚠️ dégradé") \
                + f" · grown max {worst_gd}"
            if offline:
                value += f" · ❌ slot(s) offline : {', '.join(offline[:6])}"
            emb.add_field(name="RAID", value=value[:1024], inline=True)

        bs = await bot.influx.backup_summary()
        if bs:
            emb.add_field(name="Sauvegardes",
                          value=f"+ancienne {fmt.humanize_duration(bs.get('oldest_age_seconds'))} · "
                                f"sans backup {int(bs.get('guests_without_backup') or 0)}",
                          inline=True)

        ipmi = await bot.influx.ipmi_temps()
        if ipmi:
            mx = max((v for _, v in ipmi if v is not None), default=None)
            if mx is not None:
                flag = " 🔥" if mx >= 60 else (" ⚠️" if mx >= 45 else "")
                emb.add_field(name="Temp max", value=f"{mx:.0f}°C{flag} (seuil 60°C)", inline=True)

        avy = bot.get_cog("Avy")
        if avy is not None and getattr(avy, "enabled", False):
            try:
                rows = await avy.health_rows()
            except Exception:
                log.warning("rapport : état du cluster Aveyron indisponible", exc_info=True)
                rows = []
            if rows:
                icon = {"crit": "🔥", "warn": "⚠️", "ok": "✅", "na": "➖"}
                lines = [f"{icon.get(l, '•')} {n} : {d}" for l, n, d in rows]
                emb.add_field(name="Aveyron (3 nœuds)", value="\n".join(lines)[:1024],
                              inline=False)

        if bot.loki.enabled:
            try:
                rows = await bot.loki.instant(
                    'topk(5, sum by (host) (count_over_time({host=~".+"} '
                    '| level=~"warning|err|crit|alert|emerg" [24h])))')
                rows.sort(key=lambda r: -float(r[2]))
                if rows:
                    top = " · ".join(f"{labels.get('host', '?')} {float(val):.0f}"
                                     for _, labels, val in rows)
                    emb.add_field(name="Top logs (24h)", value=top[:1024], inline=False)
            except Exception:
                log.exception("rapport: requête Loki top logs échouée")

        file = await asyncio.to_thread(render.timeseries, "Hôte CPU/RAM (24h)", "%",
                                       {"CPU%": cpu, "RAM%": mem}, "report.png", True)
        if file:
            emb.set_image(url="attachment://report.png")
        return emb, file

    async def _post(self, title):
        """Retourne True uniquement si le rapport a réellement été envoyé
        (sinon on ne doit PAS avancer last_daily, pour laisser le rattrapage jouer)."""
        cid = self.bot.cfg.report_channel_id
        if not cid:
            # muet jusqu'ici : l'absence de rapport ne laissait AUCUNE trace (2026-08-11)
            log.warning("rapport « %s » non publié : REPORT_CHANNEL_ID pas encore câblé "
                        "(provision._rewire le renseigne en fin de provisioning)", title)
            return False
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception:
                log.warning("rapport « %s » non publié : salon %s introuvable",
                            title, cid, exc_info=True)
                return False
        emb, file = await self.build_report(title)
        try:
            if file:
                await ch.send(embed=emb, file=file)
            else:
                await ch.send(embed=emb)
            return True
        except discord.HTTPException:
            log.exception("failed posting report")
            return False

    async def _post_avy(self):
        """Rapport quotidien de CHAQUE nœud Aveyron, dans son propre #rapports-<nœud>.

        DÉCOUPLÉ du rapport R820 (revue 2026-08-18) : marqueur propre `last_daily_avy`
        + compteur d'essais propre. Avant, le rattrapage ne publiait Aveyron que si le
        rapport R820 venait de partir — un salon #rapports R820 cassé privait les trois
        nœuds de leur rapport alors que leurs salons allaient bien."""
        avy = self.bot.get_cog("Avy")
        if avy is None:
            return
        today = dt.datetime.now(self.tz).date().isoformat()
        if self.bot.state.get("last_daily_avy") == today:
            return
        if self._avy_try_day != today:
            self._avy_try_day, self._avy_tries = today, 0
        if self._avy_tries >= 12:      # même borne que le rattrapage R820
            return
        self._avy_tries += 1
        try:
            n = await avy.post_rapports()
        except Exception:
            log.exception("rapports quotidiens Aveyron")
            return
        if n:
            self.bot.state.set("last_daily_avy", today)
            log.info("rapports quotidiens Aveyron publiés : %d", n)

    @tasks.loop(time=dt.time(8, 0))
    async def daily(self):
        try:
            if await self._post("Rapport quotidien"):
                self._mark_today()
        except Exception:
            log.exception("daily report failed")
        await self._post_avy()

    def _mark_today(self):
        now = dt.datetime.now(self.tz)
        self.bot.state.set("last_daily", now.date().isoformat())

    @tasks.loop(minutes=5)
    async def catchup(self):
        """Rattrapage du rapport manqué (l'hôte est éteint la nuit : un tir planifié
        raté doit être rejoué une fois à l'allumage).

        2026-08-11 — c'était un listener `on_ready` qui posait `_catchup_done = True`
        AVANT la moindre tentative. Or `_post` lit `cfg.report_channel_id` dès sa
        première instruction, et cet id n'est renseigné que par `provision._rewire()`,
        tout à la fin de `provision_all()` : les deux `on_ready` partent en tâches
        concurrentes et celle de Reports atteignait la lecture sans aucun point de
        suspension — donc toujours 0. `_post` renvoyait False, le drapeau était déjà
        consommé, et le rattrapage ne rejouait JAMAIS du processus (la boucle planifiée,
        elle, ne repasse que le lendemain). Résultat : aucun rapport le jour du
        démarrage, sans une ligne d'erreur. La boucle réessaie donc jusqu'à ce que le
        salon soit câblé, puis devient un no-op.

        ⚠️ Une fois le rattrapage réglé, on NE fait PAS `catchup.stop()` : `bg.REGISTRY`
        (filet des boucles) juge une boucle « saine » sur `is_running()`, donc une boucle
        volontairement arrêtée serait comptée en ÉCHEC et /health afficherait
        « ⚠️ Avertissements · en échec: Reports.catchup » à CHAQUE démarrage — un faux
        positif permanent sur la surface même qui doit rester digne de confiance
        (2026-08-11). Le tour de boucle coûte un test de drapeau toutes les 5 min.
        """
        if not self.bot.cfg.report_channel_id:
            return                       # provision._rewire() le renseignera
        now = dt.datetime.now(self.tz)
        sched = now.replace(hour=self.bot.cfg.report_hour,
                            minute=self.bot.cfg.report_minute, second=0, microsecond=0)
        # Aveyron d'abord, HORS du drapeau _catchup_done : il a son propre marqueur
        # quotidien et sa propre borne d'essais (cf. _post_avy) — le rattrapage R820
        # déjà réglé ne doit pas empêcher de retenter les rapports des nœuds.
        if now >= sched:
            await self._post_avy()
        if self._catchup_done:
            return
        if self.bot.state.get("last_daily") == now.date().isoformat() or now < sched:
            # rien à rattraper : soit c'est déjà publié, soit l'heure n'est pas passée
            # (la boucle `daily` s'en chargera).
            self._catchup_done = True
            return
        # borne : `_post` reconstruit tout le rapport (requêtes Influx + graphe) avant
        # d'essayer d'envoyer. Sans plafond, un salon devenu inaccessible ferait tourner
        # ce travail toutes les 5 min jusqu'au prochain redémarrage.
        self._catchup_tries += 1
        if self._catchup_tries > 12:
            log.warning("rattrapage abandonné après %d tentatives", self._catchup_tries - 1)
            self._catchup_done = True
            return
        log.info("publication du rapport de rattrapage")
        if await self._post("Rapport (rattrapage)"):
            self._mark_today()
            self._catchup_done = True

    # Un `before_loop` PAR boucle : empiler `@daily.before_loop` et `@catchup.before_loop`
    # sur la même fonction ne « marche » que parce que `Loop.before_loop` renvoie la
    # coroutine qu'on lui passe — un détail d'implémentation de discord.py. S'il changeait,
    # le second décorateur recevrait None et le cog entier échouerait à l'IMPORT (plus
    # aucun rapport quotidien). Trois lignes valent mieux que ce pari (relecture 2026-08-11).
    @daily.before_loop
    async def _before_daily(self):
        await self.bot.wait_until_ready()

    @catchup.before_loop
    async def _before_catchup(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Reports(bot))
