"""Graph commands: /graph metric x target x range -> matplotlib PNG.

Deux sources selon la cible :
  - R820 (hôte + invités)      -> InfluxDB/telegraf (historique) ;
  - Aveyron (invités -avy et nœuds `avy:<nom>`) -> RRD de l'API PVE distante
    (cpu/mem/disk en fraction·octets, netin/netout déjà en octets/s moyens).
Le bouton 📈 des salons appelle quick_file() : CPU+RAM 24 h en un seul PNG."""
import asyncio
import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core import render
from ..core.permissions import read_check
from ..core.ui import target_autocomplete

RANGES = [app_commands.Choice(name=r, value=r) for r in ("1h", "6h", "24h", "7d", "30d")]
METRICS = [app_commands.Choice(name=n, value=v) for n, v in
           (("CPU", "cpu"), ("RAM", "ram"), ("Disque", "disk"), ("Réseau", "net"))]

# période /graph -> (timeframe RRD PVE, fenêtre en secondes à conserver)
RRD_TF = {"1h": ("hour", 3600), "6h": ("day", 6 * 3600), "24h": ("day", 86400),
          "7d": ("week", 7 * 86400), "30d": ("month", 30 * 86400)}


def _scale(series, factor):
    ts, vals = series
    return ts, [(v or 0) * factor for v in vals]


def _rrd_series(rows, cutoff, fn):
    """(times, values) depuis des lignes RRD ; fn(row) -> valeur ou None (trous RRD)."""
    ts, vals = [], []
    for r in rows or []:
        t = r.get("time")
        if t is None or t < cutoff:
            continue
        try:
            v = fn(r)
        except Exception:
            v = None
        ts.append(dt.datetime.fromtimestamp(t))
        vals.append(v)
    return ts, vals


class Graphs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------- source RRD

    def _avy_target(self, tgt):
        """(kind, ident, label) si la cible est côté Aveyron, sinon None.
        kind = 'node' (tgt « avy:<nœud> ») ou 'guest' (invité -avy)."""
        pve = self.bot.pve
        if not getattr(pve, "avy_enabled", False):
            return None
        if tgt.startswith("avy:"):
            node = tgt[4:]
            if node in pve.avy_nodes():
                return ("node", node, f"{node} (Aveyron)")
        if pve.is_avy_name(tgt):
            g = pve.guest_map().get(tgt)
            if g:
                return ("guest", g, tgt.removesuffix("-" + self.bot.cfg.avy_suffix)
                        + " (Aveyron)")
        return None

    def _avy_rows(self, kind, ident, rng):
        tf, _ = RRD_TF[rng]
        if kind == "node":
            return self.bot.pve.avy_node_rrd(ident, tf)
        return self.bot.pve.avy_guest_rrd(ident["vmid"], ident.get("type"), tf)

    def _avy_build(self, kind, m, rows, rng):
        """{label: (ts, vals)}, ylabel, pct — séries d'une métrique depuis le RRD."""
        cutoff = dt.datetime.now().timestamp() - RRD_TF[rng][1]
        if m == "cpu":
            return ({"CPU": _rrd_series(rows, cutoff,
                                        lambda r: (r["cpu"] * 100) if r.get("cpu") is not None else None)},
                    "%", True)
        if m == "ram":
            if kind == "node":
                fn = (lambda r: r["memused"] / r["memtotal"] * 100
                      if r.get("memused") is not None and r.get("memtotal") else None)
            else:
                fn = (lambda r: r["mem"] / r["maxmem"] * 100
                      if r.get("mem") is not None and r.get("maxmem") else None)
            return ({"RAM": _rrd_series(rows, cutoff, fn)}, "%", True)
        if m == "disk":
            if kind == "node":
                fn = (lambda r: r["rootused"] / r["roottotal"] * 100
                      if r.get("rootused") is not None and r.get("roottotal") else None)
            else:
                fn = (lambda r: r["disk"] / r["maxdisk"] * 100
                      if r.get("disk") and r.get("maxdisk") else None)
            return ({"Disque": _rrd_series(rows, cutoff, fn)}, "%", True)
        # net : netin/netout du RRD sont déjà des débits moyens (octets/s)
        return ({"RX": _rrd_series(rows, cutoff, lambda r: r.get("netin")),
                 "TX": _rrd_series(rows, cutoff, lambda r: r.get("netout"))},
                "o/s", False)

    # ------------------------------------------------------------- /graph

    @app_commands.command(description="Graphe d'une métrique pour un conteneur, une VM ou un hôte (R820 & Aveyron).")
    @app_commands.describe(metric="Métrique", target="Conteneur, VM ou hôte", range="Période")
    @app_commands.choices(metric=METRICS, range=RANGES)
    @app_commands.autocomplete(target=target_autocomplete)
    @read_check()
    async def graph(self, itx: discord.Interaction,
                    metric: app_commands.Choice[str],
                    target: str = None,
                    range: app_commands.Choice[str] = None):
        await itx.response.defer()
        bot = self.bot
        rng = range.value if range else "24h"
        tgt = target or bot.cfg.pve_node
        m = metric.value

        avy = self._avy_target(tgt)
        if avy is not None:
            kind, ident, label = avy
            title = f"{metric.name} — {label} ({rng})"
            try:
                rows = await asyncio.to_thread(self._avy_rows, kind, ident, rng)
            except Exception as e:
                await itx.followup.send(f"❌ RRD indisponible : `{e}`")
                return
            series, ylabel, pct = self._avy_build(kind, m, rows, rng)
            file = await asyncio.to_thread(render.timeseries, title, ylabel,
                                           series, "graph.png", pct)
            if not file:
                await itx.followup.send(f"Aucune donnée pour {metric.name} / {label} sur {rng}.")
                return
            emb = discord.Embed(title=title, color=fmt.BLURPLE)
            emb.set_image(url="attachment://graph.png")
            await itx.followup.send(embed=emb, file=file)
            return

        if not bot.influx.enabled:
            await itx.followup.send("InfluxDB non configuré (`INFLUX_TOKEN`).")
            return
        is_host = (tgt == bot.cfg.pve_node)
        title = f"{metric.name} — {tgt} ({rng})"
        ylabel, pct, series = "%", True, {}

        if m == "cpu":
            series["CPU"] = (await bot.influx.host_cpu_series(rng) if is_host
                             else _scale(await bot.influx.ct_series(tgt, "cpu", rng), 100))
        elif m == "ram":
            series["RAM"] = (await bot.influx.host_mem_series(rng) if is_host
                             else await bot.influx.ct_pct_series(tgt, "mem", "maxmem", rng))
        elif m == "disk":
            series["Disque"] = (await bot.influx.host_disk_series("/", rng) if is_host
                                else await bot.influx.ct_pct_series(tgt, "disk", "maxdisk", rng))
        elif m == "net":
            pct, ylabel = False, "o/s"
            if not is_host:
                await itx.followup.send("Le débit réseau par conteneur n'est pas exporté ; "
                                        "disponible pour l'hôte uniquement.")
                return
            (rxt, rxv), (txt, txv) = await bot.influx.host_net_series("vmbr0", rng)
            series = {"RX": (rxt, rxv), "TX": (txt, txv)}

        file = await asyncio.to_thread(render.timeseries, title, ylabel, series, "graph.png", pct)
        if not file:
            await itx.followup.send(f"Aucune donnée pour {metric.name} / {tgt} sur {rng}.")
            return
        emb = discord.Embed(title=title, color=fmt.BLURPLE)
        emb.set_image(url="attachment://graph.png")
        await itx.followup.send(embed=emb, file=file)

    # -------------------------------------------------- bouton 📈 des salons

    async def quick_file(self, target):
        """CPU + RAM 24 h en un PNG pour n'importe quelle cible (invité R820 via
        Influx, invité -avy ou nœud « avy:X » via RRD). (embed, file) ou (None, None)."""
        bot = self.bot
        avy = self._avy_target(target)
        if avy is not None:
            kind, ident, label = avy
            rows = await asyncio.to_thread(self._avy_rows, kind, ident, "24h")
            cpu, _, _ = self._avy_build(kind, "cpu", rows, "24h")
            ram, _, _ = self._avy_build(kind, "ram", rows, "24h")
            series = {**cpu, **ram}
            title = f"CPU & RAM — {label} (24h)"
        elif bot.influx.enabled:
            if target == bot.cfg.pve_node:
                series = {"CPU": await bot.influx.host_cpu_series("24h"),
                          "RAM": await bot.influx.host_mem_series("24h")}
            else:
                series = {"CPU": _scale(await bot.influx.ct_series(target, "cpu", "24h"), 100),
                          "RAM": await bot.influx.ct_pct_series(target, "mem", "maxmem", "24h")}
            title = f"CPU & RAM — {target} (24h)"
        else:
            return None, None
        file = await asyncio.to_thread(render.timeseries, title, "%", series,
                                       "graph.png", True)
        if not file:
            return None, None
        emb = discord.Embed(title=title, color=fmt.BLURPLE)
        emb.set_image(url="attachment://graph.png")
        return emb, file


async def setup(bot):
    await bot.add_cog(Graphs(bot))
