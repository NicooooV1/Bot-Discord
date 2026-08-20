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
from .graphs import to_local   # même repère de temps (cfg.tz) pour tous les graphes

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
            # 7 lectures Flux indépendantes : groupées, le dashboard coûte la plus lente
            # au lieu de leur somme — le bouton 🔄 et /dashboard sont interactifs.
            # ⚠️ influx.raid() renvoie un TUPLE (ctrl, summ).
            cpu, mem, load, cts, st, (ctrl, _), bs = await asyncio.gather(
                bot.influx.host_cpu_series("6h"),
                bot.influx.host_mem_series("6h"),
                bot.influx.host_load(),
                bot.influx.ct_table(),
                bot.influx.storages(),
                bot.influx.raid(),
                bot.influx.backup_summary())
            cur = []
            if cpu[1]:
                cur.append(f"CPU **{cpu[1][-1]:.0f}%**")
            if mem[1]:
                cur.append(f"RAM **{mem[1][-1]:.0f}%**")
            if load:
                cur.append(f"load {float(load.get('load1') or 0):.2f}")
            emb.add_field(name=f"Hôte {bot.cfg.pve_node}", value=" · ".join(cur) or "—", inline=False)

            up = [c for c in cts if c["running"]]
            top = "\n".join(
                f"🟢 {c['name']} — CPU {c['cpu_pct']:.0f}% · RAM {c['ram_pct']:.0f}% "
                f"({fmt.humanize_bytes(c['mem'])}/{fmt.humanize_bytes(c['maxmem'])})"
                for c in up[:5]) or "—"
            # ct_table() ne couvre que le bucket « Proxmox » : les invités Aveyron sont
            # dans un autre bucket et ne sont PAS comptés ici — le dire (2026-08-11).
            scope = " · R820" if getattr(bot.pve, "avy_enabled", False) else ""
            emb.add_field(name=f"Top CT ({len(up)}/{len(cts)}){scope}",
                          value=top[:1024], inline=True)

            st_txt = "\n".join(f"{s['name']} — {fmt.pct_of(s['used'], s['total'])}"
                               for s in st[:4]) or "—"
            if len(st) > 4:      # liste tronquée : le dire (2026-08-20)
                st_txt += f"\n… + {len(st) - 4} autres"
            emb.add_field(name="Stockage", value=st_txt[:1024], inline=True)

            if ctrl:
                emb.add_field(name="RAID",
                              value="optimal ✅" if ctrl.get("vd_optimal") else "⚠️ dégradé",
                              inline=True)
            if bs:
                emb.add_field(name="Backup +ancien",
                              value=fmt.humanize_duration(bs.get("oldest_age_seconds")), inline=True)

            # même avertissement que /health : sans lui le dashboard épinglé restait
            # présenté comme frais pendant une panne InfluxDB (2026-08-20)
            if getattr(bot.influx, "blind", False):
                emb.add_field(name="⚠️ InfluxDB",
                              value="dernière requête Flux en échec — métriques "
                                    "ci-dessus possiblement périmées", inline=False)

            # Influx horodate en UTC : ramener dans cfg.tz avant le rendu, sinon l'axe
            # du dashboard est décalé de 2 h l'été par rapport aux messages Discord.
            file = await asyncio.to_thread(render.timeseries, "Hôte CPU/RAM (6h)", "%",
                                           to_local({"CPU%": cpu, "RAM%": mem}, bot.cfg.tz),
                                           "dash.png", True)
            if file:
                emb.set_image(url="attachment://dash.png")
        # Compter TOUTES les alertes encore maintenues, quel que soit le cog qui les
        # écrit : le filtre sur Alerts.OWNED_KEYS ({"ipmi_temp"}) excluait les 6 clés
        # servarr_* bien vivantes (VPN down, qBittorrent, ratio…), si bien que le
        # tableau de bord épinglé affichait « alertes actives: 0 » pendant que le
        # kill-switch AirVPN avait coupé le trafic (2026-08-11). `alerts_active()` fait
        # l'UNION des espaces de noms (alerts: / servarr:) ; les clés périmées, elles,
        # sont purgées au démarrage par Alerts.__init__.
        nb = len(bot.state.alerts_active())
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
            except Exception as e:
                log.warning("dashboard: salon %s introuvable (%s)", d["channel_id"], e)
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
            except Exception as e:
                # Boucle de 2 min : en debug pour ne pas noyer les logs si le salon a
                # été supprimé, mais plus jamais en silence total.
                log.debug("dashboard: salon %s injoignable (%s)", d["channel_id"], e)
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
        # PURGER explicitement les pièces jointes quand il n'y a pas de nouvelle image :
        # sans `attachments`, discord.py CONSERVE l'ancienne. Quand telegraf s'arrête,
        # render.timeseries renvoie None et l'embed perd son set_image — le vieux
        # dash.png restait alors attaché au message, présenté comme courant, pendant que
        # les champs affichaient « — » (2026-08-11).
        kwargs = {"embed": emb, "view": RefreshView(self.bot),
                  "attachments": [file] if file else []}
        try:
            await msg.edit(**kwargs)
        except discord.HTTPException as e:
            log.debug("dashboard: édition du message impossible (%s)", e)

    @refresh_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(Dashboard(bot))
