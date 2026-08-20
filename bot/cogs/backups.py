"""Backup freshness command: /backups."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core.pve import PbsPermissionError
from ..core.permissions import read_check

log = logging.getLogger("discord-bot.backups")

# Rang de criticité d'une ligne « Par conteneur ». ⚠️ se compare avec la MÊME séquence
# que celle produite plus bas : « ⚠️ » fait deux points de code (U+26A0 U+FE0F).
_CRIT, _WARN = "❌", "⚠️"


def _rank(line):
    if line.startswith(_CRIT):
        return 0
    if line.startswith(_WARN):
        return 1
    return 2


def _fit_field(lines, budget=1024):
    """Assemble des lignes dans un champ d'embed sans dépasser la limite DURE de Discord,
    en annonçant ce qui déborde.

    2026-08-11 : `backup_ages()` trie par âge décroissant, or un invité SANS sauvegarde
    porte la sentinelle `age_seconds = -1` (écrite par avy_metrics) : il finissait donc
    systématiquement dans la partie coupée par l'ancien `[:1024]`. L'embed passait au
    ROUGE sans qu'aucune ligne rouge ne soit visible, et la troncature muette coupait au
    milieu d'une ligne."""
    lines = sorted(lines, key=_rank)          # tri STABLE : l'ordre par âge est conservé
    reserve = 40                              # place pour le « … +N non affichés »
    shown, used = [], 0
    for ln in lines:
        add = len(ln) + (1 if shown else 0)
        if used + add > budget - reserve:
            break
        shown.append(ln)
        used += add
    if not shown and lines:                   # une seule ligne géante : on la tronque
        shown = [lines[0][:budget - reserve]]
    left = len(lines) - len(shown)
    value = "\n".join(shown)
    if left:
        value += f"\n… +{left} invité(s) non affiché(s)"
    return value[:budget]


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
                nob = int(summ.get("guests_without_backup") or 0)
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
                    lines.append(f"{_CRIT} **{name}** — aucune sauvegarde")
                    emb.color = fmt.RED
                else:
                    flag = _WARN if age > 108000 else "✅"  # > 30 h
                    lines.append(f"{flag} **{name}** — il y a {fmt.humanize_duration(age)}{szs}")
            if lines:
                # tri par CRITICITÉ avant la coupe : sinon les ❌ (les seules lignes qui
                # justifient l'embed rouge) sont les premières à disparaître.
                emb.add_field(name="Par conteneur", value=_fit_field(lines), inline=False)
            if not summ and not lines:
                # 2026-08-20 : Influx configuré mais MUET ≠ sauvegardes saines — la
                # couleur GREEN d'office rendait un embed vert quasi vide quand la
                # collecte était en panne. Gris + le dire, jamais un feu vert.
                emb.description = ("⚠️ Aucune mesure de sauvegarde depuis 2 h "
                                   "(collecte en panne ?)")
                emb.color = fmt.GREY
        else:
            emb.description = "⚠️ InfluxDB non configuré (`INFLUX_TOKEN`)."
        if bot.pve.enabled:
            try:
                content = await bot.pve.apbs_content()
                total = sum(int(i.get("size", 0) or 0) for i in content)
                emb.set_footer(text=f"PBS « {bot.cfg.pve_pbs_storage} » : {len(content)} snapshots "
                                    f"· {fmt.humanize_bytes(total)}")
            except PbsPermissionError as e:
                # déjà journalisé UNE fois par pve.pbs_content : ici on l'affiche à
                # l'utilisateur au lieu d'un pied de page silencieusement absent
                emb.set_footer(text=f"⚠️ {e}")
            except Exception:
                # le pied de page est optionnel, mais un PBS injoignable mérite une trace
                log.warning("PBS : contenu du datastore illisible", exc_info=True)
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Backups(bot))
