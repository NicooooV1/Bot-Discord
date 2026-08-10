"""Backup freshness command: /backups."""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.permissions import read_check


class Backups(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Fraîcheur des sauvegardes (PBS).")
    @read_check()
    async def backups(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        emb = discord.Embed(title="🛟 Sauvegardes", color=fmt.GREEN)
        if bot.influx.enabled:
            summ = await bot.influx.backup_summary()
            if summ:
                emb.add_field(name="Plus ancienne",
                              value=fmt.humanize_duration(summ.get("oldest_age_seconds")), inline=True)
                nob = int(summ.get("guests_without_backup", 0))
                emb.add_field(name="Sans sauvegarde", value=str(nob), inline=True)
                if nob > 0:
                    emb.color = fmt.YELLOW
                logical = float(summ.get("total_logical_bytes", 0) or 0)
                real = float(summ.get("real_used_bytes", 0) or 0)
                dedup = float(summ.get("dedup_factor", 0) or 0)
                if logical:
                    emb.add_field(
                        name="📦 Volume (NAS)",
                        value=(f"{fmt.humanize_bytes(logical)} logique → **{fmt.humanize_bytes(real)}** "
                               f"réel · dédup **×{dedup:.1f}**"),
                        inline=False)
            lines = []
            for r in await bot.influx.backup_ages():
                age = r.get("age_seconds")
                name = r.get("name") or r.get("vmid")
                sz = r.get("size_bytes")
                szs = f" · {fmt.humanize_bytes(sz)}" if sz else ""
                if age is None or age < 0 or not r.get("has_backup"):
                    lines.append(f"❌ **{name}** — aucune sauvegarde")
                    emb.color = fmt.RED
                else:
                    flag = "⚠️" if age > 108000 else "✅"  # > 30 h
                    lines.append(f"{flag} **{name}** — il y a {fmt.humanize_duration(age)}{szs}")
            if lines:
                emb.add_field(name="Par conteneur", value="\n".join(lines)[:1024], inline=False)
        else:
            emb.description = "⚠️ InfluxDB non configuré (`INFLUX_TOKEN`)."
        if bot.pve.enabled:
            try:
                content = await asyncio.to_thread(bot.pve.pbs_content)
                total = sum(int(i.get("size", 0) or 0) for i in content)
                emb.set_footer(text=f"PBS « {bot.cfg.pve_pbs_storage} » : {len(content)} snapshots "
                                    f"· {fmt.humanize_bytes(total)}")
            except Exception:
                pass
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Backups(bot))
