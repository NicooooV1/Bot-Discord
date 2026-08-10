"""Daily report at REPORT_HOUR:REPORT_MINUTE (Europe/Paris) + on_ready catch-up
(the host is off at night, so a missed scheduled fire is replayed once on power-on)."""
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
        t = dt.time(hour=bot.cfg.report_hour, minute=bot.cfg.report_minute, tzinfo=self.tz)
        self.daily.change_interval(time=t)
        self.daily.start()

    def cog_unload(self):
        self.daily.cancel()

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
            emb.add_field(name="RAID",
                          value=("optimal ✅" if ctrl.get("vd_optimal") else "⚠️ dégradé")
                                + f" · grown max {worst_gd}", inline=True)

        bs = await bot.influx.backup_summary()
        if bs:
            emb.add_field(name="Sauvegardes",
                          value=f"+ancienne {fmt.humanize_duration(bs.get('oldest_age_seconds'))} · "
                                f"sans backup {int(bs.get('guests_without_backup', 0))}", inline=True)

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
            return False
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception:
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

    @tasks.loop(time=dt.time(8, 0))
    async def daily(self):
        try:
            if await self._post("Rapport quotidien"):
                self._mark_today()
        except Exception:
            log.exception("daily report failed")

    def _mark_today(self):
        now = dt.datetime.now(self.tz)
        self.bot.state.set("last_daily", now.date().isoformat())

    @daily.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        if self._catchup_done:
            return
        self._catchup_done = True
        now = dt.datetime.now(self.tz)
        sched = now.replace(hour=self.bot.cfg.report_hour,
                            minute=self.bot.cfg.report_minute, second=0, microsecond=0)
        if self.bot.state.get("last_daily") != now.date().isoformat() and now >= sched:
            log.info("posting catch-up daily report")
            if await self._post("Rapport (rattrapage)"):
                self._mark_today()


async def setup(bot):
    await bot.add_cog(Reports(bot))
