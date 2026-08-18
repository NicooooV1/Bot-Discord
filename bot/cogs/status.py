"""Status & overview commands: /status /ping /node /ct /cts."""
import asyncio
import logging
import subprocess

import discord
from discord import app_commands
from discord.ext import commands

from ..core import bg
from ..core import format as fmt
from ..core import render
from ..core.permissions import read_check
from ..core.ui import ct_autocomplete
from .graphs import to_local     # même repère de temps que les graphes RRD (cfg.tz)
from .storage import _data_pct   # usage RÉEL du thinpool : une seule définition

log = logging.getLogger("discord-bot.status")

# Limites Discord (embed) : on s'y borne explicitement, un dépassement fait échouer
# l'envoi ENTIER de la fiche — pas seulement le champ fautif.
FIELD_MAX = 1024
FIELDS_MAX = 25
DESC_MAX = 4096

#: dépôt git déployé en production (l'unité systemd tourne depuis ce répertoire)
REPO_DIR = "/opt/discord-bot"


def _git_version(path=REPO_DIR):
    """Commit git déployé (« a1b2c3d »), ou None si l'information est indisponible.

    ⚠️ BLOQUANT (fork + exec) : à appeler depuis un thread, JAMAIS dans la boucle
    d'événements. Lu une seule fois au démarrage — le commit ne peut pas changer sans
    redémarrage du service, et /health n'a pas à payer un fork à chaque appel.

    Tolère tout : git absent du conteneur, répertoire qui n'est pas un dépôt, droits
    refusés. Dans ce cas None, et /health omet simplement la ligne (une supervision ne
    doit pas tomber parce qu'un outil de développement manque).
    """
    try:
        r = subprocess.run(["git", "-C", path, "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("version git indisponible (%s)", e)
        return None
    if r.returncode != 0:
        log.debug("version git indisponible: %s", (r.stderr or "").strip()[:120])
        return None
    return (r.stdout or "").strip() or None


class Status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._version = None      # commit déployé, rempli une fois au démarrage

    async def cog_load(self):
        # Lecture détachée : `git rev-parse` est un fork, il n'a rien à faire dans la
        # boucle d'événements au moment où le bot se connecte. bg.spawn garde une
        # référence forte sur la tâche (sinon le GC peut l'emporter en plein vol).
        async def _read():
            self._version = await asyncio.to_thread(_git_version)
            log.info("version déployée: %s", self._version or "inconnue")

        bg.spawn(_read(), name="Status.version", logger=log)

    def _scope(self):
        """Suffixe de libellé quand un compteur ne couvre QUE le cluster primaire.
        Les métriques d'invités Aveyron vivent dans un autre bucket InfluxDB, donc tout
        décompte issu de `ct_table()` exclut Aveyron : le dire au lieu de mentir."""
        return " · R820" if getattr(self.bot.pve, "avy_enabled", False) else ""

    @app_commands.command(description="Vue d'ensemble du homelab (hôte, CT, stockage, RAID, backups).")
    @read_check()
    async def status(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        emb = discord.Embed(title="🖥️ État du homelab", color=fmt.BLURPLE)
        if not bot.influx.enabled:
            emb.description = "⚠️ InfluxDB non configuré (`INFLUX_TOKEN`) — métriques indisponibles."
        else:
            # Lectures INDÉPENDANTES groupées : 6 allers-retours Flux séquentiels
            # coûtaient leur somme sur un chemin interactif (2026-08-11).
            # ⚠️ influx.raid() renvoie un TUPLE (ctrl, summ) : dépaquetage imbriqué.
            (load, (_, cpu_v), (_, mem_v), cts, st, (ctrl, summ), bs) = await asyncio.gather(
                bot.influx.host_load(),
                bot.influx.host_cpu_series("1h"),
                bot.influx.host_mem_series("1h"),
                bot.influx.ct_table(),
                bot.influx.storages(),
                bot.influx.raid(),
                bot.influx.backup_summary())
            host = []
            if cpu_v:
                host.append(f"CPU {cpu_v[-1]:.0f}%")
            if mem_v:
                host.append(f"RAM {mem_v[-1]:.0f}%")
            if load:
                host.append(f"load {float(load.get('load1') or 0):.2f}")
            emb.add_field(name=f"Hôte `{bot.cfg.pve_node}`", value=" · ".join(host) or "—", inline=False)

            up = [c for c in cts if c["running"]]
            top = "\n".join(f"🟢 **{c['name']}** {c['cpu_pct']:.0f}% CPU · "
                            f"RAM {fmt.pct_of(c['mem'], c['maxmem'])}"
                            for c in up[:3]) or "—"
            # ct_table() ne lit que le bucket « Proxmox » : les invités Aveyron sont
            # écrits par avy_metrics dans leur propre bucket et ne comptent PAS ici.
            # On le dit dans le libellé plutôt que d'annoncer un parc faux (2026-08-11).
            emb.add_field(name=f"Conteneurs ({len(up)} actifs / {len(cts)}){self._scope()}",
                          value=top[:FIELD_MAX], inline=False)

            st_txt = "\n".join(f"`{s['name']}` {s['pct']:.0f}% "
                               f"({fmt.humanize_bytes(s['used'])}/{fmt.humanize_bytes(s['total'])})"
                               for s in st[:4]) or "—"
            emb.add_field(name="Stockage", value=st_txt[:FIELD_MAX], inline=False)

            if ctrl:
                vd = "optimal ✅" if ctrl.get("vd_optimal") else "⚠️ dégradé"
                bbu = "ok" if ctrl.get("batt_good") else "⚠️"
                disks = (f"{int(summ.get('disks_online', 0))}/{int(summ.get('disks_total', 0))}"
                         if summ else "?")
                emb.add_field(name="RAID", value=f"VD {vd} · BBU {bbu} · disques {disks}", inline=True)

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

        async def probe(enabled, fn):
            """État d'un service, ou None quand il n'est pas configuré (➖)."""
            return await asyncio.to_thread(fn) if enabled else None

        pve_ok, influx_ok = await asyncio.gather(
            probe(bot.pve.enabled, bot.pve.reachable),
            probe(bot.influx.enabled, bot.influx.health))

        def svc(name, ok):
            return f"{name} " + ("✅" if ok else ("❌" if ok is False else "➖"))

        rows.append(("crit" if (pve_ok is False or influx_ok is False) else "ok", "Services",
                     " · ".join([svc("PVE", pve_ok), svc("Influx", influx_ok),
                                 svc("Loki", bot.loki.enabled or None)])))

        if bot.influx.enabled:
            # Les 10 lectures Flux ci-dessous sont indépendantes : les grouper ramène le
            # coût de /health à celui de la plus lente au lieu de leur somme (2026-08-11).
            # raid_disks() reste À PART : elle n'est utile que si un contrôleur RAID est
            # présent dans les métriques. ⚠️ raid() renvoie un TUPLE (ctrl, summ).
            ((_, cpu_v), (_, mem_v), load, cts, st, tp, (ctrl, summ),
             health, temps, bs) = await asyncio.gather(
                bot.influx.host_cpu_series("1h"),
                bot.influx.host_mem_series("1h"),
                bot.influx.host_load(),
                bot.influx.ct_table(),
                bot.influx.storages(),
                bot.influx.thinpool(),
                bot.influx.raid(),
                bot.influx.smart_health(),
                bot.influx.ipmi_temps(),
                bot.influx.backup_summary())
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

            up = [c for c in cts if c["running"]]
            hot = [c for c in up if c["disk_pct"] >= 90 or c["ram_pct"] >= 95]
            detail = f"{len(up)} actifs / {len(cts)}"
            if hot:
                detail += " · saturés: " + ", ".join(c["name"] for c in hot[:4])
            # cf. _scope() : ce décompte ignore les invités Aveyron (autre bucket).
            rows.append(("warn" if hot else "ok", f"Conteneurs{self._scope()}", detail))

            if st:
                w = st[0]
                sl = "crit" if w["pct"] >= 92 else ("warn" if w["pct"] >= 85 else "ok")
                rows.append((sl, "Stockage", f"+plein: `{w['name']}` {fmt.pct_of(w['used'], w['total'])}"))

            # NOTER SUR L'USAGE RÉEL (données écrites / pool), jamais sur la surallocation
            # — mêmes seuils 85/90 qu'Alerts._evaluate et /thinpool, via le helper déjà
            # écrit (_data_pct). Avant le 2026-08-11 /health notait l'overcommit : un pool
            # surprovisionné à 193 % (normal en thin provisioning) affichait « ⚠️
            # Avertissements » en permanence, ET un pool réellement plein à 95 % avec un
            # overcommit de 140 % passait pour « ok » — le faux négatif était le pire des
            # deux, c'est la seule condition qui corrompt les volumes.
            # Le garde d'entrée porte sur pool_bytes (comme partout ailleurs) : sans lui la
            # ligne disparaissait dès que le seul champ overcommit_percent manquait.
            if tp and tp.get("pool_bytes"):
                pb = tp["pool_bytes"]
                dp = _data_pct(tp)
                tl = "crit" if dp >= 90 else ("warn" if dp >= 85 else "ok")
                d = f"données **{dp:.0f}%**"
                if tp.get("data_used_bytes") is not None:
                    d += f" ({fmt.humanize_bytes(tp['data_used_bytes'])}/{fmt.humanize_bytes(pb)})"
                oc = tp.get("overcommit_percent")
                if oc is not None:   # informatif seulement : > 100 % est le principe même
                    d += f" · surallocation {oc:.0f}%"
                    if tp.get("allocated_bytes") is not None:
                        d += f" ({fmt.humanize_bytes(tp['allocated_bytes'])} alloués)"
                rows.append((tl, "Thinpool", d))

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

            if health:
                failed = [dev for dev, h in health if not bool(h)]
                rows.append(("crit" if failed else "ok", "SMART",
                             f"{len(health)} disques" + (f" · échec: {failed}" if failed else " OK")))

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

            # « Aucune donnée » et « InfluxDB en panne » se ressemblent trait pour trait
            # dans les lignes ci-dessus (les requêtes renvoient [] dans les deux cas) :
            # sans ce drapeau, /health affichait un vert mensonger avec une supervision
            # morte. getattr = le drapeau est fourni par core/influx (2026-08-11).
            if getattr(bot.influx, "blind", False):
                rows.append(("warn", "InfluxDB",
                             "dernière requête Flux en ÉCHEC — métriques ci-dessus "
                             f"possiblement périmées ({getattr(bot.influx, 'last_error', None) or '?'})"))
        else:
            rows.append(("na", "Métriques", "InfluxDB non configuré (`INFLUX_TOKEN`)."))

        avy = bot.get_cog("Avy")
        if avy is not None and getattr(avy, "enabled", False):
            try:
                rows.extend(await avy.health_rows())
            except Exception:
                # Une panne de lecture Aveyron est une information : la journaliser au
                # lieu de l'avaler, la ligne « lecture impossible » ne dit pas pourquoi.
                log.exception("/health : lecture Aveyron impossible")
                rows.append(("warn", "Aveyron (cluster)", "lecture impossible"))

        # Boucles de fond : une boucle morte laissait /health tout vert alors que la
        # supervision ou les alertes ne tournaient plus (filet bg.guard_cog_loops).
        # ⚠️ NE PAS se contenter de bg.loops_summary() ici : guard_cog_loops enregistre
        # TOUTES les boucles déclarées, y compris celles qu'un cog ne démarre JAMAIS quand
        # sa fonctionnalité est débranchée — Heartbeat.beat sans HEARTBEAT_URL (le cas en
        # production), Avy.refresh sans Aveyron, JellyfinActivity.poll avant provisioning,
        # Requests.gatepoll sans Seerr… `LoopState.healthy` exige `running`, ces boucles-là
        # seraient donc comptées « en échec » et /health resterait « ⚠️ Avertissements » à
        # vie : exactement le faux positif permanent qu'on vient de retirer du thinpool.
        # On ne surveille que les boucles réellement EN SERVICE (démarrées) ou déjà tombées
        # au moins une fois — relance en cours ou échec durable (2026-08-11).
        suivies = [s for s in bg.REGISTRY.values() if s.running or s.failures]
        cassees = sorted(s.name for s in suivies if not s.healthy)
        if suivies:
            rows.append(("warn" if cassees else "ok", "Tâches de fond",
                         f"{len(suivies) - len(cassees)}/{len(suivies)} boucles saines"
                         + (" · en échec: " + ", ".join(cassees[:5]) if cassees else "")))

        # UNION des espaces de noms : /health doit montrer TOUTES les alertes encore
        # maintenues, y compris celles de servarr (VPN, qBittorrent…) qui vivent dans
        # l'espace « servarr: ». alerts_active() rend les clés NUES, donc l'affichage est
        # identique. Les clés périmées, elles, sont purgées au démarrage par Alerts.
        firing = bot.state.alerts_active()
        if firing:
            rows.append(("crit" if any(l == "crit" for l in firing.values()) else "warn",
                         "Alertes actives", ", ".join(f"{k} [{l}]" for k, l in firing.items())))

        # Ce qui TOURNE réellement : sans cette ligne, la seule façon de savoir quelle
        # version est déployée était d'aller lire le dépôt sur l'hôte. En dernier (donc
        # la première rognée si l'embed dépasse 25 champs) : c'est la ligne la moins
        # urgente. Niveau « na » : une version n'est ni saine ni en panne, et elle ne
        # doit pas peser sur le verdict global.
        if self._version:
            rows.append(("na", "Version", f"`{self._version}`"))

        order = {"crit": 3, "warn": 2, "ok": 1, "na": 0}
        worst = max((order.get(l, 0) for l, _, _ in rows), default=1)
        color = fmt.RED if worst == 3 else (fmt.YELLOW if worst == 2 else fmt.GREEN)
        head = ("🔥 Problème critique" if worst == 3
                else "⚠️ Avertissements" if worst == 2 else "✅ Tout est vert")
        icon = {"crit": "🔥", "warn": "⚠️", "ok": "✅", "na": "➖"}
        emb = discord.Embed(title=f"🩺 Santé du homelab — {head}", color=color)
        # Bornes Discord : 25 champs max et 1024 caractères par valeur — au-delà, c'est
        # TOUT l'embed qui est rejeté. Avec les lignes Aveyron on frôle la limite.
        shown = rows[:FIELDS_MAX - 1] if len(rows) > FIELDS_MAX else rows
        for level, name, detail in shown:
            emb.add_field(name=f"{icon.get(level, '•')} {name}",
                          value=(detail or "—")[:FIELD_MAX], inline=False)
        if len(rows) > FIELDS_MAX:
            emb.add_field(name="…", value=f"{len(rows) - len(shown)} lignes supplémentaires "
                                          "(voir /alerts et le salon du cluster).", inline=False)
        emb.timestamp = discord.utils.utcnow()
        await itx.followup.send(embed=emb)

    @app_commands.command(description="Latence du bot et joignabilité des services.")
    @read_check()
    async def ping(self, itx: discord.Interaction):
        await itx.response.defer()
        bot = self.bot
        # latency vaut NaN tant que le premier heartbeat n est pas acquitte (juste
        # apres une (re)connexion) : round(NaN) leve ValueError et /ping repondait
        # « Erreur » precisement au moment ou on veut verifier la connexion (campagne
        # de test 2026-08-18).
        import math
        lat = "?" if math.isnan(bot.latency) else round(bot.latency * 1000)
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
            # Un seul aller-retour : guest_map donne vmid ET type. L'autocomplete propose
            # aussi les VM (« 🖥️VM ») ; or ct_status tape /lxc et LÈVE sur une QEMU
            # (110 HAOS, 111 win11, 112 arch) — /ct était donc cassé pour TOUTES les VM.
            # On route par type comme ct_channels le fait déjà (corrigé 2026-08-11).
            g, unreachable = None, False
            try:
                g = (await bot.pve.aguest_map()).get(name)
            except Exception as e:
                unreachable = True
                log.warning("/ct %s : inventaire PVE indisponible (%s)", name, e)
            if g:
                vmid, gtype = g.get("vmid"), g.get("type")
                if gtype == "qemu":
                    emb.title = f"🖥️ {name}"
                # vmid RÉEL à l'affichage : les vmid Aveyron circulent décalés de +1 000 000.
                shown_id = bot.pve.display_vmid(vmid)
                cur = None
                try:
                    cur = await bot.pve.aguest_status(vmid, gtype)
                except Exception as e:
                    # Invité éteint/injoignable ou lien WG Aveyron coupé (AvyUnreachable) :
                    # fiche dégradée, pas un plantage de toute la commande.
                    log.warning("/ct %s : statut indisponible (%s)", name, e)
                if cur:
                    running = cur.get("status") == "running"
                    emb.description = (f"{fmt.status_emoji(running)} `{cur.get('status')}` "
                                       f"· vmid {shown_id}")
                    emb.add_field(name="Uptime", value=fmt.humanize_duration(cur.get("uptime")))
                    mm = cur.get("maxmem") or 0
                    if mm:
                        emb.add_field(name="RAM", value=fmt.pct_of(cur.get("mem") or 0, mm))
                    emb.add_field(name="CPU", value=f"{(cur.get('cpu') or 0) * 100:.0f}%")
                    md = cur.get("maxdisk") or 0
                    if md:
                        emb.add_field(name="Disque", value=fmt.pct_of(cur.get("disk") or 0, md))
                else:
                    emb.description = f"⚠️ statut indisponible · vmid {shown_id}"
            elif unreachable:
                emb.description = "⚠️ API Proxmox injoignable — fiche partielle."
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
            # Influx horodate en UTC : ramener dans le fuseau d'affichage, sinon le pic
            # de 16 h heure de Paris est étiqueté « 14:00 » (cf. graphs.to_local).
            series = to_local({"CPU": (ts, [(v or 0) * 100 for v in vals])}, bot.cfg.tz)
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
        lines, seen = [], set()
        if bot.influx.enabled:
            for c in await bot.influx.ct_table():
                seen.add(c["name"])
                lines.append(f"{fmt.status_emoji(c['running'])} **{c['name']}** — "
                             f"{c['cpu_pct']:.0f}% CPU · RAM {fmt.pct_of(c['mem'], c['maxmem'])} · "
                             f"{c['disk_pct']:.0f}% disk")
        if not lines and bot.pve.enabled:
            lst = await bot.pve.acall(bot.pve.lxc_list)
            for c in sorted(lst, key=lambda x: x.get("name", "")):
                seen.add(c.get("name"))
                lines.append(f"{fmt.status_emoji(c.get('status') == 'running')} "
                             f"**{c.get('name')}** ({bot.pve.display_vmid(c.get('vmid'))})")
        # Aveyron : ses invités sont écrits par avy_metrics dans le bucket « Aveyron »,
        # que ct_table() ne lit pas — /cts les omettait donc entièrement alors que
        # l'autocomplete de /ct les propose. On complète depuis l'API PVE, seule source
        # qui connaît les DEUX clusters (2026-08-11). Pas de collision de noms : les
        # invités Aveyron gardent leur suffixe -avy ici.
        if getattr(bot.pve, "avy_enabled", False):
            try:
                res = await bot.pve.aresources()
            except Exception as e:
                log.warning("/cts : inventaire Aveyron indisponible (%s)", e)
                res = []
            avy = []
            for r in res:
                nm = r.get("name")
                if (not nm or nm in seen or r.get("type") not in ("lxc", "qemu")
                        or not bot.pve.is_avy_name(nm)):
                    continue
                kind = " 🖥️VM" if r.get("type") == "qemu" else ""
                avy.append(f"{fmt.status_emoji(r.get('status') == 'running')} **{nm}** "
                           f"({bot.pve.display_vmid(r.get('vmid'))}){kind}")
            if avy:
                lines.append("\n**— Aveyron —**")
                lines.extend(sorted(avy))
        desc = "\n".join(lines)
        if len(desc) > DESC_MAX - 96:          # limite Discord : 4096 caractères
            desc = desc[:DESC_MAX - 96].rsplit("\n", 1)[0] + "\n… (liste tronquée)"
        emb = discord.Embed(title="📦 Conteneurs",
                            description=desc or "Aucun conteneur.",
                            color=fmt.BLURPLE)
        await itx.followup.send(embed=emb)


async def setup(bot):
    await bot.add_cog(Status(bot))
