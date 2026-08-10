"""Live-refreshing dashboard: /dashboard pins a message edited by a background loop,
plus a persistent 🔄 Refresh button. State persists so it re-attaches after restart."""
import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core import render
from ..core.permissions import admin_check
from ..views.refresh import RefreshView
from .alerts import Alerts  # source unique des clés d'alerte réellement maintenues

log = logging.getLogger("discord-bot.dashboard")

ACTIONS = [app_commands.Choice(name="create", value="create"),
           app_commands.Choice(name="stop", value="stop")]


class Dashboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.refresh_loop.change_interval(minutes=bot.cfg.dashboard_interval_min)
        self.refresh_loop.start()

    def cog_unload(self):
        self.refresh_loop.cancel()

    async def build_dashboard(self):
        bot = self.bot
        emb = discord.Embed(title="📊 Dashboard homelab", color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        file = None
        if not bot.influx.enabled:
            emb.description = "⚠️ InfluxDB non configuré (`INFLUX_TOKEN`)."
        else:
            cpu = await bot.influx.host_cpu_series("6h")
            mem = await bot.influx.host_mem_series("6h")
            load = await bot.influx.host_load()
            cur = []
            if cpu[1]:
                cur.append(f"CPU **{cpu[1][-1]:.0f}%**")
            if mem[1]:
                cur.append(f"RAM **{mem[1][-1]:.0f}%**")
            if load:
                cur.append(f"load {float(load.get('load1') or 0):.2f}")
            emb.add_field(name=f"Hôte {bot.cfg.pve_node}", value=" · ".join(cur) or "—", inline=False)

            cts = await bot.influx.ct_table()
            up = [c for c in cts if c["running"]]
            top = "\n".join(
                f"🟢 {c['name']} — CPU {c['cpu_pct']:.0f}% · RAM {c['ram_pct']:.0f}% "
                f"({fmt.humanize_bytes(c['mem'])}/{fmt.humanize_bytes(c['maxmem'])})"
                for c in up[:5]) or "—"
            emb.add_field(name=f"Top CT ({len(up)}/{len(cts)})", value=top, inline=True)

            st = await bot.influx.storages()
            emb.add_field(name="Stockage",
                          value="\n".join(f"{s['name']} — {fmt.pct_of(s['used'], s['total'])}"
                                          for s in st[:4]) or "—",
                          inline=True)

            ctrl, _ = await bot.influx.raid()
            if ctrl:
                emb.add_field(name="RAID",
                              value="optimal ✅" if ctrl.get("vd_optimal") else "⚠️ dégradé",
                              inline=True)
            bs = await bot.influx.backup_summary()
            if bs:
                emb.add_field(name="Backup +ancien",
                              value=fmt.humanize_duration(bs.get("oldest_age_seconds")), inline=True)

            file = await asyncio.to_thread(render.timeseries, "Hôte CPU/RAM (6h)", "%",
                                           {"CPU%": cpu, "RAM%": mem}, "dash.png", True)
            if file:
                emb.set_image(url="attachment://dash.png")
        adict = bot.state.d.get("alerts", {}) or {}
        # N'afficher que les alertes que le bot maintient encore (OWNED_KEYS) — sinon
        # d'anciennes clés persistées feraient remonter des faux "actives".
        nb = sum(1 for k, v in adict.items() if k in Alerts.OWNED_KEYS and v.get("level"))
        emb.set_footer(text=f"alertes actives: {nb} · rafraîchi")
        return emb, file

    async def _teardown(self, d):
        """Neutralise et retire l'ancien message dashboard épinglé (best-effort).
        Retire le bouton persistant PUIS désépingle/supprime, pour qu'il ne puisse
        plus être reconstruit. N'efface PAS l'état (à la charge de l'appelant)."""
        if not d or not d.get("message_id"):
            return
        ch = self.bot.get_channel(d["channel_id"])
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(d["channel_id"])
            except Exception:
                return
        try:
            msg = await ch.fetch_message(d["message_id"])
            await msg.edit(view=None)
            try:
                await msg.unpin()
            except discord.HTTPException:
                pass
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    @app_commands.command(description="Crée/épingle le dashboard live, ou l'arrête.")
    @app_commands.choices(action=ACTIONS)
    @admin_check()
    async def dashboard(self, itx: discord.Interaction, action: app_commands.Choice[str] = None):
        await itx.response.defer(ephemeral=True)
        act = action.value if action else "create"
        if act == "stop":
            # Retirer réellement le message épinglé + son bouton avant de vider l'état,
            # sinon le dashboard "arrêté" reste affiché et se reconstruit.
            await self._teardown(self.bot.state.get("dashboard") or {})
            self.bot.state.set("dashboard", {})
            await itx.followup.send("Dashboard live arrêté.", ephemeral=True)
            return
        emb, file = await self.build_dashboard()
        # Dédup : au plus un dashboard vivant — retirer l'ancien avant d'en créer un nouveau.
        await self._teardown(self.bot.state.get("dashboard") or {})
        kwargs = {"embed": emb, "view": RefreshView(self.bot)}
        if file:
            kwargs["file"] = file
        msg = await itx.channel.send(**kwargs)
        try:
            await msg.pin()
        except discord.HTTPException:
            pass
        self.bot.state.set("dashboard", {"channel_id": msg.channel.id, "message_id": msg.id})
        await itx.followup.send("Dashboard créé et épinglé — il se rafraîchit automatiquement.",
                                ephemeral=True)

    @tasks.loop(minutes=2)
    async def refresh_loop(self):
        d = self.bot.state.get("dashboard") or {}
        if not d.get("message_id"):
            return
        ch = self.bot.get_channel(d["channel_id"])
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(d["channel_id"])
            except Exception:
                return
        try:
            msg = await ch.fetch_message(d["message_id"])
        except discord.NotFound:
            # Compare-and-clear : ne vider l'état que si le message disparu est toujours
            # le message courant — sinon on effacerait un dashboard fraîchement (re)créé.
            cur = self.bot.state.get("dashboard") or {}
            if cur.get("message_id") == d["message_id"]:
                self.bot.state.set("dashboard", {})
            return
        except discord.HTTPException:
            return
        try:
            emb, file = await self.build_dashboard()
        except Exception:
            log.exception("dashboard build failed")
            return
        kwargs = {"embed": emb, "view": RefreshView(self.bot)}
        if file:
            kwargs["attachments"] = [file]
        try:
            await msg.edit(**kwargs)
        except discord.HTTPException:
            pass

    @refresh_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Dashboard(bot))
