"""Loki commands: /logsearch /ctlogs /apperrors (centralized logs, LOKI_URL).

Read-only queries against the Loki HTTP API. Label contract (frozen, see core/loki.py):
host = machine hostname, unit = systemd unit, level = journald vocabulary,
job = systemd-journal|jellyfin|caddy.
"""
import time

import discord
from discord import app_commands
from discord.ext import commands

from ..core.loki import _esc, build_logql
from ..core.permissions import read_check
from ..core.ui import ct_autocomplete
from .logs import journal_response

PLAGES = [
    app_commands.Choice(name="15 min", value=15),
    app_commands.Choice(name="1 h", value=60),
    app_commands.Choice(name="6 h", value=360),
    app_commands.Choice(name="24 h", value=1440),
    app_commands.Choice(name="7 j", value=10080),
]

LEVEL_CHOICES = [app_commands.Choice(name=n, value=n) for n in
                 ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")]


def _fmt_lines(rows):
    out = []
    for ts, labels, line in rows:
        hhmmss = time.strftime("%H:%M:%S", time.localtime(ts))
        host = labels.get("host", "?")
        unit = labels.get("unit") or labels.get("job") or "-"
        out.append(f"{hhmmss} {host} {unit}: {line}")
    return out


class LokiLogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _guard(self, itx):
        """True if Loki is usable; otherwise reply and return False."""
        if not self.bot.loki.enabled:
            await itx.response.send_message("Loki non configuré (LOKI_URL).",
                                            ephemeral=True)
            return False
        await itx.response.defer()
        return True

    async def _reply(self, itx, rows, header):
        content, file = journal_response(_fmt_lines(rows), header)
        if file:
            await itx.followup.send(content=None, file=file)
        else:
            await itx.followup.send(content=content)

    @app_commands.command(description="Recherche dans les logs centralisés (Loki).")
    @app_commands.describe(
        requete="LogQL brut si commence par { ; sinon texte recherché",
        host="Hôte (pve, jellyfin, ...)", unit="Unité systemd",
        level="Sévérité journald exacte", contains="Texte contenu dans la ligne",
        plage="Fenêtre de recherche (défaut 1 h)")
    @app_commands.choices(plage=PLAGES, level=LEVEL_CHOICES)
    @read_check()
    async def logsearch(self, itx: discord.Interaction, requete: str = "",
                        host: str = "", unit: str = "",
                        level: app_commands.Choice[str] = None, contains: str = "",
                        plage: app_commands.Choice[int] = None):
        if not await self._guard(itx):
            return
        minutes = plage.value if plage else 60
        requete = requete.strip()
        if requete.startswith("{"):
            q = requete
        else:
            q = build_logql(host or None, unit or None,
                            level.value if level else None,
                            contains or requete or None)
        rows = await self.bot.loki.query_range(q, minutes)
        label = plage.name if plage else "1 h"
        header = f"logsearch · {q[:120]} · {label} ({len(rows)} lignes)"
        await self._reply(itx, rows, header)

    @app_commands.command(description="Logs d'un conteneur via Loki (host = nom du CT).")
    @app_commands.describe(ct="Conteneur (hostname)", unit="Unité systemd",
                           level="Sévérité journald exacte",
                           plage="Fenêtre de recherche (défaut 1 h)")
    @app_commands.choices(plage=PLAGES, level=LEVEL_CHOICES)
    @app_commands.autocomplete(ct=ct_autocomplete)
    @read_check()
    async def ctlogs(self, itx: discord.Interaction, ct: str, unit: str = "",
                     level: app_commands.Choice[str] = None,
                     plage: app_commands.Choice[int] = None):
        if not await self._guard(itx):
            return
        minutes = plage.value if plage else 60
        q = build_logql(ct, unit or None, level.value if level else None)
        rows = await self.bot.loki.query_range(q, minutes)
        label = plage.name if plage else "1 h"
        header = f"ctlogs · {ct} · {label} ({len(rows)} lignes)"
        await self._reply(itx, rows, header)

    @app_commands.command(description="Erreurs récentes d'un conteneur (Loki).")
    @app_commands.describe(ct="Conteneur (hostname)",
                           plage="Fenêtre de recherche (défaut 1 h)")
    @app_commands.choices(plage=PLAGES)
    @app_commands.autocomplete(ct=ct_autocomplete)
    @read_check()
    async def apperrors(self, itx: discord.Interaction, ct: str,
                        plage: app_commands.Choice[int] = None):
        if not await self._guard(itx):
            return
        minutes = plage.value if plage else 60
        q = f'{{host="{_esc(ct)}"}} | level=~"err|crit|alert|emerg"'
        rows = await self.bot.loki.query_range(q, minutes)
        note = ""
        if not rows:  # fallback: apps logging errors at info level / without label
            q = f'{{host="{_esc(ct)}"}} |~ "(?i)error"'
            rows = await self.bot.loki.query_range(q, minutes)
            note = " · repli texte \"error\""
        label = plage.name if plage else "1 h"
        header = f"apperrors · {ct} · {label} ({len(rows)} lignes){note}"
        await self._reply(itx, rows, header)


async def setup(bot):
    await bot.add_cog(LokiLogs(bot))
