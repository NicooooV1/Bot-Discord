"""Hardware-health commands (the bot's unique value vs Grafana): /raid /smart /temps /ipmi."""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core import render
from ..core.permissions import read_check


def _temp_flag(v, warn=45, crit=60):
    """Marqueur de seuil pour une température (mêmes seuils que le barchart)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return " 🔥" if v >= crit else (" ⚠️" if v >= warn else "")


class Hardware(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _need_influx(self, itx):
        if not self.bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré (`INFLUX_TOKEN`).")
            return False
        return True

    @app_commands.command(description="État RAID (PERC H710): VD, BBU, disques, grown defects/slot.")
    @read_check()
    async def raid(self, itx: discord.Interaction):
        await itx.response.defer()
        if not await self._need_influx(itx):
            return
        ctrl, summ = await self.bot.influx.raid()
        disks = await self.bot.influx.raid_disks()
        emb = discord.Embed(title="🧱 RAID — PERC H710")
        color = fmt.GREEN  # track severity as an int (never compare a Colour to an int)
        if ctrl:
            vd = "optimal ✅" if ctrl.get("vd_optimal") else "⚠️ DÉGRADÉ"
            bbu = "ok ✅" if ctrl.get("batt_good") else "⚠️ KO"
            line = f"VD: {vd}\nBBU: {bbu}"
            if ctrl.get("rebuild_percent"):
                line += f"\nrebuild: {ctrl['rebuild_percent']}%"
            emb.add_field(name="Contrôleur", value=line, inline=True)
            if not ctrl.get("vd_optimal") or not ctrl.get("batt_good"):
                color = fmt.RED
        if summ:
            emb.add_field(name="Disques",
                          value=f"{int(summ.get('disks_online', 0))}/{int(summ.get('disks_total', 0))} online",
                          inline=True)
        if disks:
            lines, worst = [], 0
            for d in disks:
                gd = int(d.get("grown_defects", 0) or 0)
                worst = max(worst, gd)
                flag = "⚠️" if gd >= 100 else ("•" if gd else "✅")
                offline = "" if d.get("online") else " **(offline!)**"
                lines.append(f"{flag} slot {d.get('slot')}: {gd} grown{offline}")
            emb.add_field(name="Secteurs réalloués (grown defects)",
                          value="\n".join(lines)[:1024], inline=False)
            if worst >= 100 and color != fmt.RED:
                color = fmt.YELLOW
        emb.color = color
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Températures (IPMI + capteurs carte mère).")
    @read_check()
    async def temps(self, itx: discord.Interaction):
        await itx.response.defer()
        if not await self._need_influx(itx):
            return
        data = await self.bot.influx.ipmi_temps() or await self.bot.influx.hwmon_temps()
        data = [(n, v) for n, v in data if v is not None]
        if not data:
            await itx.followup.send("Aucune donnée de température.")
            return
        labels = [d[0][:14] for d in data]
        vals = [d[1] for d in data]
        file = await asyncio.to_thread(render.barchart, "Températures (°C)", "°C",
                                       labels, vals, 45, 60, "temps.png")
        hottest = max(data, key=lambda x: x[1])
        emb = discord.Embed(
            title="🌡️ Températures",
            description=f"Max: **{hottest[0]}** {hottest[1]:.0f}°C{_temp_flag(hottest[1])} "
                        f"(seuils 45/60°C)", color=fmt.BLURPLE)
        if file:
            emb.set_image(url="attachment://temps.png")
            await itx.followup.send(embed=emb, file=file)
        else:
            await itx.followup.send(embed=emb)

    @app_commands.command(description="Capteurs IPMI: ventilateurs, alimentation, températures.")
    @read_check()
    async def ipmi(self, itx: discord.Interaction):
        await itx.response.defer()
        if not await self._need_influx(itx):
            return
        fans = await self.bot.influx.ipmi_fans()
        power = await self.bot.influx.ipmi_power()
        temps = await self.bot.influx.ipmi_temps()
        emb = discord.Embed(title="🔧 IPMI", color=fmt.BLURPLE)
        if fans:
            emb.add_field(name="Ventilateurs (RPM)",
                          value="\n".join(f"{n}: {int(v or 0)}" for n, v in fans[:12])[:1024], inline=True)
        if power:
            emb.add_field(name="Alimentation (W)",
                          value="\n".join(f"{n}: {(v or 0):.0f}" for n, v in power)[:1024], inline=True)
        if temps:
            emb.add_field(name="Températures (°C, seuils 45/60)",
                          value="\n".join(f"{n}: {(v or 0):.0f}{_temp_flag(v)}"
                                          for n, v in temps[:12])[:1024], inline=False)
        if not (fans or power or temps):
            emb.description = "Aucune donnée IPMI."
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Synthèse SMART des disques.")
    @read_check()
    async def smart(self, itx: discord.Interaction):
        await itx.response.defer()
        if not await self._need_influx(itx):
            return
        health = await self.bot.influx.smart_health()
        temps = dict(await self.bot.influx.smart_temps())
        grown = dict(await self.bot.influx.smart_grown())
        emb = discord.Embed(title="💽 SMART", color=fmt.GREEN)
        if not health:
            emb.description = "Aucune donnée SMART (node-exporter smartmon)."
        else:
            lines, bad = [], False
            for dev, h in health:
                ok = bool(h)
                bad = bad or not ok
                extra = []
                if temps.get(dev) is not None:
                    extra.append(f"{temps[dev]:.0f}°C")
                if grown.get(dev):
                    extra.append(f"{int(grown[dev])} grown")
                lines.append(f"{'✅' if ok else '❌'} `{dev}` {'PASS' if ok else 'FAIL'}"
                             + (f" — {' · '.join(extra)}" if extra else ""))
            emb.description = "\n".join(lines)[:4000]
            if bad:
                emb.color = fmt.RED
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Hardware(bot))
