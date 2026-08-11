"""Graph commands: /graph metric x target x range -> matplotlib PNG.

Deux sources selon la cible :
  - R820 (hôte + invités)      -> InfluxDB/telegraf (historique) ;
  - Aveyron (invités -avy et nœuds `avy:<nom>`) -> RRD de l'API PVE distante
    (cpu/mem/disk en fraction·octets, netin/netout déjà en octets/s moyens).
Le bouton 📈 des salons appelle quick_file() : CPU+RAM 24 h en un seul PNG.

Les deux sources n'horodatent PAS pareil (Influx = aware UTC, RRD = naïf local) :
`to_local()` les ramène toutes dans cfg.tz juste avant le rendu — cf. son docstring."""
import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core import render
from ..core.permissions import read_check
from ..core.ui import target_autocomplete

log = logging.getLogger("discord-bot.graphs")

RANGES = [app_commands.Choice(name=r, value=r) for r in ("1h", "6h", "24h", "7d", "30d")]
METRICS = [app_commands.Choice(name=n, value=v) for n, v in
           (("CPU", "cpu"), ("RAM", "ram"), ("Disque", "disk"), ("Réseau", "net"))]

# période /graph -> (timeframe RRD PVE, fenêtre en secondes à conserver)
RRD_TF = {"1h": ("hour", 3600), "6h": ("day", 6 * 3600), "24h": ("day", 86400),
          "7d": ("week", 7 * 86400), "30d": ("month", 30 * 86400)}


def _scale(series, factor):
    ts, vals = series
    return ts, [(v or 0) * factor for v in vals]


_TZ = {}


def _tz(name):
    """ZoneInfo mémoïsée (None si le fuseau est inconnu : on retombe sur l'heure système)."""
    if name not in _TZ:
        try:
            _TZ[name] = ZoneInfo(name)
        except Exception:            # noqa: BLE001 — tzdata absent : heure système
            log.warning("fuseau « %s » inconnu — étiquetage des graphes en heure système", name)
            _TZ[name] = None
    return _TZ[name]


def to_local(series, tzname):
    """{label: (ts, vals)} avec des datetime NAÏFS dans le fuseau d'affichage (cfg.tz).

    PIÈGE (corrigé 2026-08-11) : les deux sources n'étaient pas dans le même repère de
    temps. InfluxDB rend des datetime AWARE en UTC, le RRD PVE des datetime NAÏFS en
    heure locale (`fromtimestamp`), et `render.timeseries` formate l'axe avec un
    `DateFormatter` sans `tz` — donc `rcParams['timezone']` = UTC : les aware étaient
    convertis en UTC, les naïfs affichés tels quels. Un pic de 16 h à Paris s'étiquetait
    « 14:00 » sur un graphe R820 et « 16:00 » sur un graphe Aveyron, impossible à
    corréler avec un log ou une alerte Discord.

    On normalise donc TOUT sur la convention majoritaire du projet (naïf, heure locale,
    comme `fromtimestamp` ailleurs) juste avant le rendu. Idempotent : une série déjà
    naïve (RRD) traverse sans changement, on peut l'appliquer partout sans réfléchir.
    """
    tz = _tz(tzname)
    out = {}
    for label, (ts, vals) in series.items():
        out[label] = ([_naive_local(t, tz) for t in (ts or [])], vals)
    return out


def _naive_local(t, tz):
    if isinstance(t, dt.datetime) and t.tzinfo is not None:
        return (t.astimezone(tz) if tz else t.astimezone()).replace(tzinfo=None)
    return t


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

    async def _avy_target(self, tgt):
        """(kind, ident, label) si la cible est côté Aveyron, sinon None.
        kind = 'node' (tgt « avy:<nœud> ») ou 'guest' (invité -avy).

        ASYNC parce que `guest_map()` (et `avy_nodes()` quand AVY_NODES est vide) sont
        des appels proxmoxer SYNCHRONES : cache froid = un GET requests qui gelait la
        boucle d'événements jusqu'à 15 s (R820) — plus aucune interaction acquittée ni
        heartbeat gateway pendant ce temps (corrigé 2026-08-11)."""
        pve = self.bot.pve
        if not getattr(pve, "avy_enabled", False):
            return None
        if tgt.startswith("avy:"):
            node = tgt[4:]
            if node in await pve.acall(pve.avy_nodes):
                return ("node", node, f"{node} (Aveyron)")
        if pve.is_avy_name(tgt):
            g = (await pve.aguest_map()).get(tgt)
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

        avy = await self._avy_target(tgt)
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
                                           to_local(series, bot.cfg.tz), "graph.png", pct)
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

        file = await asyncio.to_thread(render.timeseries, title, ylabel,
                                       to_local(series, bot.cfg.tz), "graph.png", pct)
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
        avy = await self._avy_target(target)
        if avy is not None:
            kind, ident, label = avy
            rows = await asyncio.to_thread(self._avy_rows, kind, ident, "24h")
            cpu, _, _ = self._avy_build(kind, "cpu", rows, "24h")
            ram, _, _ = self._avy_build(kind, "ram", rows, "24h")
            series = {**cpu, **ram}
            title = f"CPU & RAM — {label} (24h)"
        elif bot.influx.enabled:
            # Les deux séries sont indépendantes : une seule attente au lieu de deux
            # (le bouton 📈 est un chemin interactif, borné à 3 s pour l'accusé).
            if target == bot.cfg.pve_node:
                cpu_s, ram_s = await asyncio.gather(
                    bot.influx.host_cpu_series("24h"), bot.influx.host_mem_series("24h"))
            else:
                cpu_s, ram_s = await asyncio.gather(
                    bot.influx.ct_series(target, "cpu", "24h"),
                    bot.influx.ct_pct_series(target, "mem", "maxmem", "24h"))
                cpu_s = _scale(cpu_s, 100)
            series = {"CPU": cpu_s, "RAM": ram_s}
            title = f"CPU & RAM — {target} (24h)"
        else:
            return None, None
        file = await asyncio.to_thread(render.timeseries, title, "%",
                                       to_local(series, bot.cfg.tz), "graph.png", True)
        if not file:
            return None, None
        emb = discord.Embed(title=title, color=fmt.BLURPLE)
        emb.set_image(url="attachment://graph.png")
        return emb, file


async def setup(bot):
    await bot.add_cog(Graphs(bot))
