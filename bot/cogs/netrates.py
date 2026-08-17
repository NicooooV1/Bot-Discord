"""Débits réseau par invité, par hyperviseur et vers Internet : /debits.

⚠️ SÉMANTIQUE, à ne pas confondre (c'est la question que pose tout le monde) :
  - la ligne « Uplink » vient des compteurs SNMP de sfp-sfpplus1, la liaison
    MikroTik → Bbox. Elle porte le trafic Internet MAIS AUSSI le hairpin : faute de
    split-DNS, les clients Jellyfin de la maison sortent vers l'IP publique et
    reviennent par ce même lien. Un pic mesuré ici n'est donc pas forcément de
    l'Internet, et le vrai plafond reste celui du lien montant de la Box ;
  - les lignes par invité viennent de netin/netout de PVE, soit le trafic TOTAL de
    leur interface — un rsync entre deux CT du même VLAN y apparaît, alors qu'il ne
    consomme pas un octet d'Internet.
Séparer LAN et Internet PAR INVITÉ demanderait de la comptabilité par flux sur le
routeur (address-list + mangle), ce qui n'existe pas aujourd'hui.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.permissions import read_check

log = logging.getLogger("discord-bot.netrates")

# Interface WAN du MikroTik (uplink vers la Bbox). Cf. mémoire réseau : sfp-sfpplus1.
WAN_IF = "sfp-sfpplus1"


def _fit(lines, budget=1024):
    """Assemble des lignes sans dépasser la limite DURE d'un champ d'embed Discord,
    en annonçant explicitement ce qui déborde (même parti pris que backups._fit_field :
    une troncature muette ferait croire à une liste complète)."""
    reserve = 40
    shown, used = [], 0
    for ln in lines:
        add = len(ln) + (1 if shown else 0)
        if used + add > budget - reserve:
            break
        shown.append(ln)
        used += add
    if not shown and lines:
        shown = [lines[0][:budget - reserve]]
    left = len(lines) - len(shown)
    value = "\n".join(shown)
    if left:
        value += f"\n… +{left} invité(s) non affiché(s)"
    return value[:budget] or "—"


def _fmt_pair(avg, mx):
    """« 1,2 Mo/s (pic 8,4 Mo/s) », ou juste la moyenne si le pic n'apporte rien."""
    a = fmt.humanize_rate(avg or 0)
    if (mx or 0) > (avg or 0) * 1.2:
        return f"{a} (pic {fmt.humanize_rate(mx)})"
    return a


class NetRates(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        description="Débits réseau : Internet (WAN), hyperviseur, et chaque LXC/VM.")
    @app_commands.describe(periode="Fenêtre de mesure (1h par défaut)")
    @app_commands.choices(periode=[
        app_commands.Choice(name="1 heure", value="1h"),
        app_commands.Choice(name="6 heures", value="6h"),
        app_commands.Choice(name="24 heures", value="24h"),
        app_commands.Choice(name="7 jours", value="7d"),
    ])
    @read_check()
    async def debits(self, itx: discord.Interaction, periode: str = "1h"):
        await itx.response.defer()
        bot = self.bot
        emb = discord.Embed(title="📶 Débits réseau", color=fmt.GREEN)

        if not bot.influx.enabled:
            emb.description = "⚠️ InfluxDB non configuré (`INFLUX_TOKEN`)."
            await itx.followup.send(embed=emb)
            return

        emb.description = f"Moyennes et pics sur **{periode}**."

        # --- Internet réel (WAN du routeur) ---------------------------------
        try:
            wan = await bot.influx.wan_rates(periode, WAN_IF)
        except Exception:
            log.warning("débits WAN illisibles", exc_info=True)
            wan = None
        if wan and (wan["down_avg"] or wan["up_avg"] or wan["down_max"] or wan["up_max"]):
            emb.add_field(
                name=f"🌐 Uplink MikroTik → Box — {wan['ifname']}",
                value=(f"⬇️ **{_fmt_pair(wan['down_avg'], wan['down_max'])}**\n"
                       f"⬆️ **{_fmt_pair(wan['up_avg'], wan['up_max'])}**"),
                inline=False)
        else:
            emb.add_field(name="🌐 Uplink MikroTik → Box",
                          value=f"⚠️ pas de données SNMP pour `{WAN_IF}`", inline=False)

        # --- par invité + hyperviseur ---------------------------------------
        try:
            rows = await bot.influx.net_rates(periode)
        except Exception:
            log.warning("débits par invité illisibles", exc_info=True)
            rows = []

        nodes = [r for r in rows if r.get("object") == "nodes"]
        guests = [r for r in rows if r.get("object") in ("lxc", "qemu")]

        if nodes:
            emb.add_field(
                name="🖥️ Hyperviseur (toutes interfaces)",
                value="\n".join(
                    f"**{r['name']}** ⬇️ {_fmt_pair(r['in_avg'], r['in_max'])} · "
                    f"⬆️ {_fmt_pair(r['out_avg'], r['out_max'])}" for r in nodes)[:1024],
                inline=False)

        if guests:
            lines = []
            for r in guests:
                kind = "VM" if r.get("object") == "qemu" else "CT"
                vmid = r.get("vmid") or "?"
                lines.append(
                    f"`{kind}{vmid}` **{r['name']}** ⬇️ {_fmt_pair(r['in_avg'], r['in_max'])}"
                    f" · ⬆️ {_fmt_pair(r['out_avg'], r['out_max'])}")
            emb.add_field(name=f"📦 Invités ({len(guests)}, du plus bavard au plus discret)",
                          value=_fit(lines), inline=False)
        else:
            emb.add_field(name="📦 Invités", value="aucune donnée sur la période",
                          inline=False)

        emb.set_footer(
            text="Par invité = trafic TOTAL de l'interface (LAN + Internet mêlés). "
                 "L'uplink inclut le hairpin des clients de la maison.")
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(NetRates(bot))
