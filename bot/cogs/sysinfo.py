"""In-guest detail from node_exporter (installed in each CT, scraped by host telegraf):
/sys (load, real RAM, swap, procs, uptime, filesystems) and /df (filesystems)."""
import time

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.permissions import read_check
from ..core.ui import ct_autocomplete


def sysinfo_fields(emb, d):
    if "node_load1" in d:
        emb.add_field(name="Load",
                      value=f"{d.get('node_load1', 0):.2f} / {d.get('node_load5', 0):.2f} / {d.get('node_load15', 0):.2f}")
    # 2026-08-20 : Available/Free absents ≠ zéro libre — un `or 0` affichait une RAM
    # « 100 % » fabriquée sur un simple trou de collecte.
    mt, ma = d.get("node_memory_MemTotal_bytes"), d.get("node_memory_MemAvailable_bytes")
    if mt:
        emb.add_field(name="RAM (réelle)",
                      value=fmt.pct_of(mt - ma, mt) if ma is not None else "utilisation inconnue")
    st, sf = d.get("node_memory_SwapTotal_bytes"), d.get("node_memory_SwapFree_bytes")
    if st:
        emb.add_field(name="Swap",
                      value=fmt.pct_of(st - sf, st) if sf is not None else "utilisation inconnue")
    if "node_procs_running" in d:
        emb.add_field(name="Procs",
                      value=f"running {int(d.get('node_procs_running', 0))} · blocked {int(d.get('node_procs_blocked', 0))}")
    bt = d.get("node_boot_time_seconds")
    if bt:
        emb.add_field(name="Uptime", value=fmt.humanize_duration(time.time() - bt))


class SysInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Détail interne d'un conteneur (load, RAM réelle, swap, procs, disques).")
    @app_commands.describe(name="Conteneur")
    @app_commands.autocomplete(name=ct_autocomplete)
    @read_check()
    async def sys(self, itx: discord.Interaction, name: str):
        await itx.response.defer()
        bot = self.bot
        if not bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré (`INFLUX_TOKEN`).")
            return
        d = await bot.influx.ct_sysinfo(name)
        fs = await bot.influx.ct_filesystems(name)
        if not d and not fs:
            await itx.followup.send(f"Pas de métriques in-guest pour `{name}` "
                                    "(node_exporter pas encore remonté ?).")
            return
        emb = discord.Embed(title=f"🔎 {name} — vue interne (node_exporter)", color=fmt.BLURPLE)
        sysinfo_fields(emb, d)
        if fs:
            lines = [f"{fmt.status_emoji(f['pct'] < 90)} `{f['mount']}` {f['pct']:.0f}% "
                     f"({fmt.humanize_bytes(f['used'])}/{fmt.humanize_bytes(f['size'])})" for f in fs[:8]]
            emb.add_field(name="Disques (in-guest)", value="\n".join(lines)[:1024], inline=False)
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Systèmes de fichiers d'un conteneur (df in-guest).")
    @app_commands.describe(name="Conteneur")
    @app_commands.autocomplete(name=ct_autocomplete)
    @read_check()
    async def df(self, itx: discord.Interaction, name: str):
        await itx.response.defer()
        bot = self.bot
        if not bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré.")
            return
        fs = await bot.influx.ct_filesystems(name)
        if not fs:
            await itx.followup.send(f"Pas de données de disque pour `{name}`.")
            return
        emb = discord.Embed(title=f"💽 {name} — systèmes de fichiers", color=fmt.BLURPLE)
        for f in fs[:12]:
            emb.add_field(name=f"{f['mount']} — {f['pct']:.0f}%",
                          value=f"{fmt.pct_bar(f['pct'])}\n{fmt.pct_of(f['used'], f['size'])}",
                          inline=True)
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(SysInfo(bot))
