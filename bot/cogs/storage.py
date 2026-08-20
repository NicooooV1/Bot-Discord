"""Storage commands: /storage /thinpool."""
import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.permissions import read_check


def _data_pct(tp):
    """Usage RÉEL du thinpool (données écrites / taille pool), en %. C'est LA métrique
    qui doit rester < 100 % (un thinpool plein = corruption). À ne PAS confondre avec la
    surallocation (overcommit), qui dépasse 100 % en thin provisioning et est normale.

    Rend None quand les DEUX sources manquent : un 0.0 fabriqué affichait « 0 %
    utilisé » et un embed vert sur une collecte morte (2026-08-20)."""
    dp = tp.get("data_percent")
    if dp is not None:
        return float(dp)
    du, pb = tp.get("data_used_bytes"), tp.get("pool_bytes")
    return (du / pb * 100) if (du and pb) else None


class Storage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Utilisation des stockages PVE + thinpool.")
    @read_check()
    async def storage(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        if not bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré (`INFLUX_TOKEN`).")
            return
        emb = discord.Embed(title="💾 Stockage", color=fmt.BLURPLE)
        stores = await bot.influx.storages()
        # borne à 23 champs pour laisser la place au thinpool + à la note de
        # débordement (limite Discord: 25 champs / embed).
        for s in stores[:23]:
            emb.add_field(
                name=f"{s['name']} — {s['pct']:.0f}%",
                value=f"{fmt.pct_bar(s['pct'])}\n{fmt.pct_of(s['used'], s['total'])}",
                inline=True)
        if len(stores) > 23:
            emb.add_field(name="…", value=f"+{len(stores) - 23} autres stockages non affichés",
                          inline=False)
        tp = await bot.influx.thinpool()
        if tp and tp.get("pool_bytes"):
            dp = _data_pct(tp)                         # usage RÉEL (doit rester < 100 %), None si inconnu
            oc = tp.get("overcommit_percent")          # surallocation (info, peut > 100 %)
            if dp is None:
                name = "Thinpool local-lvm — usage réel ?"
                value = "⚠️ usage réel inconnu (métrique absente d'Influx)"
            else:
                warn = " ⚠️" if dp >= 85 else ""       # alarme sur l'USAGE, pas la surallocation
                name = f"Thinpool local-lvm — {dp:.0f}%{warn}"
                value = (f"{fmt.pct_bar(dp)}\n"
                         f"usage réel **{dp:.0f}%** — "
                         f"{fmt.pct_of(tp.get('data_used_bytes'), tp.get('pool_bytes'))}")
            if oc is not None:
                value += f"\nsurprovision {oc:.0f}% (normal en thin provisioning)"
            emb.add_field(name=name, value=value, inline=False)
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Détail d'un thin pool (surprovisionnement).")
    @app_commands.describe(pool="Nom du pool (défaut: local-lvm)")
    @read_check()
    async def thinpool(self, itx: discord.Interaction, pool: str = "local-lvm"):
        await itx.response.defer()
        bot = self.bot
        if not bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré.")
            return
        tp = await bot.influx.thinpool(pool)
        if not tp:
            await itx.followup.send(f"Pas de données pour le pool `{pool}`.")
            return
        oc = tp.get("overcommit_percent")
        dp = _data_pct(tp)                    # usage RÉEL = la métrique qui doit rester < 100 %
        if dp is None:
            # 2026-08-20 : collecte morte ≠ pool vide — jamais « sous contrôle » ni
            # d'embed vert quand la métrique manque.
            emb = discord.Embed(
                title=f"🗄️ Thinpool {pool} — usage réel ?",
                description="⚠️ usage réel inconnu (métrique absente d'Influx) — "
                            "impossible de dire si le pool se remplit.",
                color=fmt.YELLOW)
            emb.add_field(name="Usage réel (données écrites)",
                          value="**?** (pas de donnée)", inline=False)
        else:
            emb = discord.Embed(
                title=f"🗄️ Thinpool {pool} — {dp:.0f}% utilisé",
                description=("⚠️ Usage réel élevé — un thinpool PLEIN corrompt les volumes."
                             if dp >= 85 else "Usage réel sous contrôle."),
                color=fmt.RED if dp >= 90 else fmt.YELLOW if dp >= 85 else fmt.GREEN)
            emb.add_field(name="Usage réel (données écrites)",
                          value=f"**{dp:.0f}%** — "
                                f"{fmt.pct_of(tp.get('data_used_bytes'), tp.get('pool_bytes'))}",
                          inline=False)
        emb.add_field(name="Taille pool", value=fmt.humanize_bytes(tp.get("pool_bytes")))
        emb.add_field(name="Alloué (provisionné)", value=fmt.humanize_bytes(tp.get("allocated_bytes")))
        if oc is not None:
            emb.add_field(name="Surprovisionnement (info)",
                          value=f"**{oc:.0f}%** ({fmt.humanize_bytes(tp.get('allocated_bytes'))} "
                                f"alloués / {fmt.humanize_bytes(tp.get('pool_bytes'))} pool) — "
                                f"NORMAL en thin provisioning tant que l'usage réel reste bas.",
                          inline=False)
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Storage(bot))
