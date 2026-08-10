"""Status & overview commands: /status /ping /node /ct /cts."""
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ..core import format as fmt
from ..core import render
from ..core.permissions import read_check
from ..core.ui import ct_autocomplete


class Status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Vue d'ensemble du homelab (hôte, CT, stockage, RAID, backups).")
    @read_check()
    async def status(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        emb = discord.Embed(title="🖥️ État du homelab", color=fmt.BLURPLE)
        if not bot.influx.enabled:
            emb.description = "⚠️ InfluxDB non configuré (`INFLUX_TOKEN`) — métriques indisponibles."
        else:
            load = await bot.influx.host_load()
            _, cpu_v = await bot.influx.host_cpu_series("1h")
            _, mem_v = await bot.influx.host_mem_series("1h")
            host = []
            if cpu_v:
                host.append(f"CPU {cpu_v[-1]:.0f}%")
            if mem_v:
                host.append(f"RAM {mem_v[-1]:.0f}%")
            if load:
                host.append(f"load {float(load.get('load1') or 0):.2f}")
            emb.add_field(name=f"Hôte `{bot.cfg.pve_node}`", value=" · ".join(host) or "—", inline=False)

            cts = await bot.influx.ct_table()
            up = [c for c in cts if c["running"]]
            top = "\n".join(f"🟢 **{c['name']}** {c['cpu_pct']:.0f}% CPU · "
                            f"RAM {fmt.pct_of(c['mem'], c['maxmem'])}"
                            for c in up[:3]) or "—"
            emb.add_field(name=f"Conteneurs ({len(up)} actifs / {len(cts)})", value=top, inline=False)

            st = await bot.influx.storages()
            st_txt = "\n".join(f"`{s['name']}` {s['pct']:.0f}% "
                               f"({fmt.humanize_bytes(s['used'])}/{fmt.humanize_bytes(s['total'])})"
                               for s in st[:4]) or "—"
            emb.add_field(name="Stockage", value=st_txt, inline=False)

            ctrl, summ = await bot.influx.raid()
            if ctrl:
                vd = "optimal ✅" if ctrl.get("vd_optimal") else "⚠️ dégradé"
                bbu = "ok" if ctrl.get("batt_good") else "⚠️"
                disks = (f"{int(summ.get('disks_online', 0))}/{int(summ.get('disks_total', 0))}"
                         if summ else "?")
                emb.add_field(name="RAID", value=f"VD {vd} · BBU {bbu} · disques {disks}", inline=True)

            bs = await bot.influx.backup_summary()
            if bs:
                emb.add_field(name="Sauvegardes",
                              value=f"+ancienne: {fmt.humanize_duration(bs.get('oldest_age_seconds'))}\n"
                                    f"sans backup: {int(bs.get('guests_without_backup', 0))}",
                              inline=True)
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Bilan de santé consolidé (services, hôte, RAID, SMART, température, backups, alertes).")
    @read_check()
    async def health(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        rows = []  # (level, name, detail); level in ok/warn/crit/na

        pve_ok = await asyncio.to_thread(bot.pve.reachable) if bot.pve.enabled else None
        influx_ok = await asyncio.to_thread(bot.influx.health) if bot.influx.enabled else None

        def svc(name, ok):
            return f"{name} " + ("✅" if ok else ("❌" if ok is False else "➖"))

        rows.append(("crit" if (pve_ok is False or influx_ok is False) else "ok", "Services",
                     " · ".join([svc("PVE", pve_ok), svc("Influx", influx_ok),
                                 svc("Loki", bot.loki.enabled or None)])))

        if bot.influx.enabled:
            _, cpu_v = await bot.influx.host_cpu_series("1h")
            _, mem_v = await bot.influx.host_mem_series("1h")
            load = await bot.influx.host_load()
            cpu = cpu_v[-1] if cpu_v else None
            ram = mem_v[-1] if mem_v else None
            l1 = float(load["load1"]) if load and load.get("load1") is not None else None
            if cpu is None and ram is None:
                rows.append(("na", "Hôte", "—"))
            else:
                hl = "ok"
                if (cpu is not None and cpu >= 95) or (ram is not None and ram >= 95):
                    hl = "crit"
                elif (cpu is not None and cpu >= 85) or (ram is not None and ram >= 90):
                    hl = "warn"
                hd = f"CPU {cpu:.0f}% · RAM {ram:.0f}%" if cpu is not None and ram is not None \
                    else (f"CPU {cpu:.0f}%" if cpu is not None else f"RAM {ram:.0f}%")
                if l1 is not None:
                    hd += f" · load {l1:.2f}"
                rows.append((hl, "Hôte", hd))

            cts = await bot.influx.ct_table()
            up = [c for c in cts if c["running"]]
            hot = [c for c in up if c["disk_pct"] >= 90 or c["ram_pct"] >= 95]
            detail = f"{len(up)} actifs / {len(cts)}"
            if hot:
                detail += " · saturés: " + ", ".join(c["name"] for c in hot[:4])
            rows.append(("warn" if hot else "ok", "Conteneurs", detail))

            st = await bot.influx.storages()
            if st:
                w = st[0]
                sl = "crit" if w["pct"] >= 92 else ("warn" if w["pct"] >= 85 else "ok")
                rows.append((sl, "Stockage", f"+plein: `{w['name']}` {fmt.pct_of(w['used'], w['total'])}"))

            tp = await bot.influx.thinpool()
            if tp and tp.get("overcommit_percent") is not None:
                oc = tp["overcommit_percent"]
                dp = tp.get("data_percent")
                pb = tp.get("pool_bytes") or 0
                tl = "crit" if oc >= 200 else ("warn" if oc >= 150 else "ok")
                d = f"overcommit {oc:.0f}%"
                if pb and tp.get("allocated_bytes") is not None:
                    d += f" ({fmt.humanize_bytes(tp['allocated_bytes'])} alloués / {fmt.humanize_bytes(pb)})"
                if dp is not None:
                    d += f" · données {dp:.0f}%"
                    if pb and tp.get("data_used_bytes") is not None:
                        d += f" ({fmt.humanize_bytes(tp['data_used_bytes'])})"
                rows.append((tl, "Thinpool", d))

            ctrl, summ = await bot.influx.raid()
            if ctrl:
                disks = await bot.influx.raid_disks()
                gd = max((int(d.get("grown_defects", 0) or 0) for d in disks), default=0)
                offline = [d.get("slot") for d in disks if not d.get("online")]
                bad = (not ctrl.get("vd_optimal")) or (not ctrl.get("batt_good")) or offline
                on = int(summ.get("disks_online", 0)) if summ else 0
                tot = int(summ.get("disks_total", 0)) if summ else 0
                rows.append(("crit" if bad else ("warn" if gd >= 150 else "ok"), "RAID",
                             f"VD {'optimal' if ctrl.get('vd_optimal') else 'dégradé'} · "
                             f"BBU {'ok' if ctrl.get('batt_good') else '⚠️'} · {on}/{tot} · grown max {gd}"))

            health = await bot.influx.smart_health()
            if health:
                failed = [dev for dev, h in health if not bool(h)]
                rows.append(("crit" if failed else "ok", "SMART",
                             f"{len(health)} disques" + (f" · échec: {failed}" if failed else " OK")))

            temps = await bot.influx.ipmi_temps()
            vals = [(n, v) for n, v in temps if v is not None]
            if vals:
                inlet = [v for n, v in vals if "inlet" in str(n).lower()]
                allmax = max(v for _, v in vals)
                if inlet:
                    mi = max(inlet)
                    tl = "crit" if mi >= 40 else ("warn" if mi >= 32 else "ok")
                    rows.append((tl, "Température",
                                 f"entrée d'air {mi:.0f}°C (seuils 32/40°C) · max {allmax:.0f}°C"))
                else:
                    rows.append(("ok", "Température", f"max {allmax:.0f}°C"))

            bs = await bot.influx.backup_summary()
            if bs:
                age = bs.get("oldest_age_seconds")
                nob = int(bs.get("guests_without_backup", 0))
                bl = "ok"
                if age is not None and age >= 180000:
                    bl = "crit"
                elif nob > 0 or (age is not None and age >= 108000):
                    bl = "warn"
                rows.append((bl, "Sauvegardes",
                             f"+ancienne {fmt.humanize_duration(age)} · sans backup {nob}"))
        else:
            rows.append(("na", "Métriques", "InfluxDB non configuré (`INFLUX_TOKEN`)."))

        avy = bot.get_cog("Avy")
        if avy is not None and getattr(avy, "enabled", False):
            try:
                rows.extend(await avy.health_rows())
            except Exception:
                rows.append(("warn", "Aveyron (cluster)", "lecture impossible"))

        firing = {k: v.get("level") for k, v in (bot.state.get("alerts", {}) or {}).items()
                  if v.get("level")}
        if firing:
            rows.append(("crit" if any(l == "crit" for l in firing.values()) else "warn",
                         "Alertes actives", ", ".join(f"{k} [{l}]" for k, l in firing.items())))

        order = {"crit": 3, "warn": 2, "ok": 1, "na": 0}
        worst = max((order.get(l, 0) for l, _, _ in rows), default=1)
        color = fmt.RED if worst == 3 else (fmt.YELLOW if worst == 2 else fmt.GREEN)
        head = ("🔥 Problème critique" if worst == 3
                else "⚠️ Avertissements" if worst == 2 else "✅ Tout est vert")
        icon = {"crit": "🔥", "warn": "⚠️", "ok": "✅", "na": "➖"}
        emb = discord.Embed(title=f"🩺 Santé du homelab — {head}", color=color)
        for level, name, detail in rows:
            emb.add_field(name=f"{icon.get(level, '•')} {name}", value=detail or "—", inline=False)
        emb.timestamp = discord.utils.utcnow()
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Latence du bot et joignabilité des services.")
    @read_check()
    async def ping(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        lat = round(bot.latency * 1000)
        pve_ok = await asyncio.to_thread(bot.pve.reachable) if bot.pve.enabled else None
        influx_ok = await asyncio.to_thread(bot.influx.health) if bot.influx.enabled else None

        def mark(x):
            return "✅" if x else ("❌" if x is False else "➖ non configuré")

        emb = discord.Embed(title="🏓 Ping", color=fmt.BLURPLE)
        emb.add_field(name="Gateway Discord", value=f"{lat} ms")
        emb.add_field(name="Proxmox API", value=mark(pve_ok))
        emb.add_field(name="InfluxDB", value=mark(influx_ok))
        if getattr(bot.pve, "avy_enabled", False):
            avy = bot.get_cog("Avy")
            ms = (avy._cluster or {}).get("ping_ms") if avy else None
            emb.add_field(name="Proxmox API (Aveyron, tunnel WG)",
                          value=f"{ms:.0f} ms" if ms is not None else "➖")
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Détail de l'hôte Proxmox.")
    @read_check()
    async def node(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        if not bot.pve.enabled:
            await itx.followup.send("Proxmox API non configurée.")
            return
        st = await asyncio.to_thread(bot.pve.host_status)
        emb = discord.Embed(title=f"🖥️ Hôte {bot.cfg.pve_node}", color=fmt.BLURPLE)
        emb.add_field(name="Uptime", value=fmt.humanize_duration(st.get("uptime")))
        la = st.get("loadavg") or []
        if la:
            emb.add_field(name="Load", value=" / ".join(str(x) for x in la))
        mem = st.get("memory") or {}
        if mem:
            emb.add_field(name="RAM",
                          value=f"{fmt.humanize_bytes(mem.get('used'))}/{fmt.humanize_bytes(mem.get('total'))}")
        ci = st.get("cpuinfo") or {}
        if ci:
            emb.add_field(name="CPU", value=f"{ci.get('cpus', '?')}× — {ci.get('model', '')[:48]}")
        if st.get("pveversion"):
            emb.set_footer(text=st["pveversion"])
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Fiche d'un conteneur (status + graphe CPU 6h).")
    @app_commands.describe(name="Nom du conteneur")
    @app_commands.autocomplete(name=ct_autocomplete)
    @read_check()
    async def ct(self, itx: discord.Interaction, name: str):
        await itx.response.defer()
        bot = self.bot
        emb = discord.Embed(title=f"📦 {name}", color=fmt.BLURPLE)
        file = None
        if bot.pve.enabled:
            vmid = await asyncio.to_thread(bot.pve.vmid_of, name)
            if vmid:
                cur = await asyncio.to_thread(bot.pve.ct_status, vmid)
                running = cur.get("status") == "running"
                emb.description = f"{fmt.status_emoji(running)} `{cur.get('status')}` · vmid {vmid}"
                emb.add_field(name="Uptime", value=fmt.humanize_duration(cur.get("uptime")))
                mm = cur.get("maxmem") or 0
                if mm:
                    emb.add_field(name="RAM", value=fmt.pct_of(cur.get("mem") or 0, mm))
                emb.add_field(name="CPU", value=f"{(cur.get('cpu') or 0) * 100:.0f}%")
                md = cur.get("maxdisk") or 0
                if md:
                    emb.add_field(name="Disque", value=fmt.pct_of(cur.get("disk") or 0, md))
            else:
                emb.description = "Conteneur introuvable."
        if bot.influx.enabled:
            d = await bot.influx.ct_sysinfo(name)
            if d:
                bits = []
                if "node_load1" in d:
                    bits.append(f"load {d['node_load1']:.2f}")
                mt, ma = d.get("node_memory_MemTotal_bytes"), d.get("node_memory_MemAvailable_bytes")
                if mt:
                    bits.append("RAM réelle " + fmt.pct_of(mt - (ma or 0), mt))
                if bits:
                    emb.add_field(name="In-guest", value=" · ".join(bits), inline=False)
            ts, vals = await bot.influx.ct_series(name, "cpu", "6h")
            series = {"CPU": (ts, [(v or 0) * 100 for v in vals])}
            file = await asyncio.to_thread(render.timeseries, f"CPU — {name}", "%", series, "ct.png", True)
        if file:
            emb.set_image(url="attachment://ct.png")
            await itx.followup.send(embed=emb, file=file)
        else:
            await itx.followup.send(embed=emb)

    @app_commands.command(description="Liste de tous les conteneurs.")
    @read_check()
    async def cts(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        lines = []
        if bot.influx.enabled:
            for c in await bot.influx.ct_table():
                lines.append(f"{fmt.status_emoji(c['running'])} **{c['name']}** — "
                             f"{c['cpu_pct']:.0f}% CPU · RAM {fmt.pct_of(c['mem'], c['maxmem'])} · "
                             f"{c['disk_pct']:.0f}% disk")
        if not lines and bot.pve.enabled:
            lst = await asyncio.to_thread(bot.pve.lxc_list)
            for c in sorted(lst, key=lambda x: x.get("name", "")):
                lines.append(f"{fmt.status_emoji(c.get('status') == 'running')} "
                             f"**{c.get('name')}** ({c.get('vmid')})")
        emb = discord.Embed(title="📦 Conteneurs",
                            description="\n".join(lines)[:4000] or "Aucun conteneur.",
                            color=fmt.BLURPLE)
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Status(bot))
