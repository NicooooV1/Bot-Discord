"""Logs subsystem: /journal /logs /tail /tasks + the live UDP/514 stream listener.

Host journal is read via the PVE API (GET /nodes/pve/journal). The API returns a flat
array mixing cursor lines (s=...;i=...) and human text lines, with no unit filter — so
/logs filters client-side. The bot inside CT106 cannot pct-exec, so per-CT application
logs come through the live stream (opt-in rsyslog forward) rather than a command.
"""
import asyncio
import io
import re

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.logstream import LiveLogStream
from ..core.permissions import admin_check, read_check
from ..core.syslog_lib import SEVERITY_NAMES, SEVERITY_NUM, sev_fr

_CURSOR = re.compile(r"^s=[0-9a-f]+;i=")

SEV_CHOICES = [app_commands.Choice(name=n, value=n) for n in
               ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")]


def split_journal(arr):
    lines = [x for x in arr if not _CURSOR.match(str(x))]
    cursors = [x for x in arr if _CURSOR.match(str(x))]
    return lines, (cursors[-1] if cursors else None)


def journal_response(lines, header):
    body = "\n".join(str(x) for x in lines) or "(aucune entrée)"
    # content = **header**\n```\nbody\n``` -> len(header) + len(body) + 13; Discord cap 2000.
    # The second guard matters for long headers (e.g. /logsearch echoes the query).
    if len(body) > 1850 or len(body) + len(header) > 1985:
        return None, discord.File(io.BytesIO(body.encode("utf-8", "replace")), "journal.txt")
    return f"**{header}**\n```\n{body}\n```", None


class TailView(discord.ui.View):
    def __init__(self, bot, unit, cursor, author_id, timeout=300):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.unit = unit
        self.cursor = cursor
        self.author_id = author_id

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.author_id:
            await itx.response.send_message("Ce n'est pas votre suivi.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Rafraîchir", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            if self.cursor:
                arr = await asyncio.to_thread(self.bot.pve.journal, None, None, self.cursor)
            else:
                arr = await asyncio.to_thread(self.bot.pve.journal, 200)
        except Exception:
            arr = await asyncio.to_thread(self.bot.pve.journal, 200)
        lines, cur = split_journal(arr)
        if cur:
            self.cursor = cur
        if self.unit:
            lines = [x for x in lines if self.unit in str(x).lower()]
        content, file = journal_response(lines[-40:], f"tail · {self.unit or 'journal'}")
        if file:
            await itx.message.edit(content=None, attachments=[file], view=self)
        else:
            await itx.message.edit(content=content, attachments=[], view=self)


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stream = None

    async def cog_load(self):
        self.stream = LiveLogStream(self.bot)
        self.stream.start()

    async def cog_unload(self):
        if self.stream:
            await self.stream.aclose()

    @app_commands.command(description="Journal de l'hôte (dernières entrées).")
    @app_commands.describe(lastentries="Nombre de lignes (def 60, max 2000)")
    @read_check()
    async def journal(self, itx: discord.Interaction, lastentries: int = 60):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        n = max(1, min(2000, lastentries))
        arr = await asyncio.to_thread(self.bot.pve.journal, n)
        lines, _ = split_journal(arr)
        content, file = journal_response(lines[-n:], f"journal · hôte {self.bot.cfg.pve_node}")
        if file:
            await itx.followup.send(content=None, file=file)
        else:
            await itx.followup.send(content=content)

    @app_commands.command(description="Logs filtrés par unité/mot-clé (journal hôte).")
    @app_commands.describe(unit="Unité/service ou mot-clé", lastentries="Fenêtre de recherche")
    @read_check()
    async def logs(self, itx: discord.Interaction, unit: str, lastentries: int = 1000):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        n = max(50, min(3000, lastentries))
        arr = await asyncio.to_thread(self.bot.pve.journal, n)
        lines, _ = split_journal(arr)
        matched = [x for x in lines if unit.lower() in str(x).lower()]
        content, file = journal_response(matched[-80:], f"logs « {unit} » ({len(matched)} lignes)")
        if file:
            await itx.followup.send(content=None, file=file)
        else:
            await itx.followup.send(content=content)

    @app_commands.command(description="Suivi d'un service (bouton Rafraîchir, 5 min).")
    @app_commands.describe(unit="Unité/mot-clé à suivre (vide = tout)")
    @read_check()
    async def tail(self, itx: discord.Interaction, unit: str = ""):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        arr = await asyncio.to_thread(self.bot.pve.journal, 200)
        lines, cur = split_journal(arr)
        u = unit.lower()
        if u:
            lines = [x for x in lines if u in str(x).lower()]
        view = TailView(self.bot, u, cur, itx.user.id)
        content, file = journal_response(lines[-40:], f"tail · {unit or 'journal'}")
        if file:
            await itx.followup.send(content=None, file=file, view=view)
        else:
            await itx.followup.send(content=content, view=view)

    @app_commands.command(description="Tâches PVE récentes (start/stop/backup), filtre vmid.")
    @app_commands.describe(vmid="Filtrer par vmid (optionnel)")
    @read_check()
    async def tasks(self, itx: discord.Interaction, vmid: int = None):
        await itx.response.defer()
        if not self.bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        ts = await asyncio.to_thread(self.bot.pve.tasks, vmid, 25)
        lines = []
        for t in ts:
            status = t.get("status") or ("en cours" if not t.get("endtime") else "?")
            flag = "✅" if status == "OK" else ("⏳" if status == "en cours" else "❌")
            who = t.get("id") or t.get("vmid") or "-"
            lines.append(f"{flag} `{t.get('type')}` {who} — {status}")
        emb = discord.Embed(title="🗂️ Tâches PVE",
                            description="\n".join(lines)[:4000] or "Aucune tâche.",
                            color=fmt.BLURPLE)
        await itx.followup.send(embed=emb)

    # ----- /logstream admin group (runtime control of the live UDP stream) -----

    logstream = app_commands.Group(name="logstream",
                                   description="Gestion du flux de logs live (admin).")

    def _stream_or_none(self):
        st = self.stream
        if st is None or not st.channel_id:
            return None
        return st

    @logstream.command(name="stats", description="Statistiques du flux de logs live.")
    @admin_check()
    async def logstream_stats(self, itx: discord.Interaction):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        s = st.stats()
        emb = discord.Embed(title="📡 Flux de logs — statistiques", color=fmt.BLURPLE)
        emb.add_field(name="Reçus", value=str(s["received"]), inline=True)
        emb.add_field(name="Filtrés", value=str(s["filtered"]), inline=True)
        emb.add_field(name="Groupes publiés", value=str(s["posted_groups"]), inline=True)
        emb.add_field(name="Répétitions supprimées",
                      value=str(s["suppressed_repeats"]), inline=True)
        emb.add_field(name="Groupes repliés (anti-flood)",
                      value=str(s["overflow_folded"]), inline=True)
        emb.add_field(name="Échecs d'envoi", value=str(s["send_failures"]), inline=True)
        emb.add_field(name="Réessais perdus", value=str(s["retry_dropped"]), inline=True)
        emb.add_field(name="File de réessai", value=str(len(st.pending)), inline=True)
        sevname = sev_fr(st.min_sev)
        state = "⏸️ en pause" if st.paused else "▶️ actif"
        emb.add_field(name="État", value=f"{state} · sévérité ≤ `{sevname}`", inline=True)
        emb.add_field(name="Depuis", value=f"<t:{int(s['since_ts'])}:R>", inline=True)
        await itx.response.send_message(embed=emb, ephemeral=True)

    @logstream.command(name="severity",
                       description="Change la sévérité minimale du flux (runtime).")
    @app_commands.describe(niveau="Sévérité minimale (emerg = le plus grave)")
    @app_commands.choices(niveau=SEV_CHOICES)
    @admin_check()
    async def logstream_severity(self, itx: discord.Interaction,
                                 niveau: app_commands.Choice[str]):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        st.min_sev = SEVERITY_NUM[niveau.value]
        await itx.response.send_message(
            f"Sévérité minimale du flux réglée sur `{niveau.value}` "
            "(runtime uniquement, non persistant — modifier `LIVE_LOG_MIN_SEVERITY` "
            "dans config.env pour rendre le changement permanent).", ephemeral=True)

    @logstream.command(name="pause", description="Met le flux de logs en pause.")
    @admin_check()
    async def logstream_pause(self, itx: discord.Interaction):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        st.paused = True
        self.bot.state.set("logstream_paused", True)
        await itx.response.send_message(
            "⏸️ Flux de logs en pause (les paquets reçus sont ignorés). "
            "_(état persistant — conservé après redémarrage)_", ephemeral=True)

    @logstream.command(name="resume", description="Reprend le flux de logs.")
    @admin_check()
    async def logstream_resume(self, itx: discord.Interaction):
        st = self._stream_or_none()
        if st is None:
            await itx.response.send_message(
                "Flux de logs non configuré (LIVE_LOG_CHANNEL_ID).", ephemeral=True)
            return
        st.paused = False
        self.bot.state.set("logstream_paused", False)
        await itx.response.send_message(
            "▶️ Flux de logs repris. _(état persistant — conservé après redémarrage)_",
            ephemeral=True)


async def setup(bot):
    await bot.add_cog(Logs(bot))
