"""Collecteur de métriques Aveyron -> InfluxDB (bucket dédié « Aveyron », même
instance/org que le R820, jeton scopé à ce seul bucket).

Le cluster Aveyron n'a PAS de telegraf/node_exporter (aucun accès infra, cf. règle
« Nico gère seul Aveyron, le bot ne fait que lire ») : ce cog joue le rôle que
telegraf tient pour le R820, mais en lisant l'API PVE déjà utilisée par avy.py et en
écrivant lui-même les points. But : que Grafana affiche Aveyron avec les mêmes
dashboards/queries que le R820 (mesure `system`, tags `object`/`host`/`node`, mêmes
noms de champs), sans rien installer sur Aveyron.

2026-07-18, demande Nico : « regrouper dans un nouveau dossier sur Grafana toutes les
données comme déjà fait pour le R820 »."""
import asyncio
import logging
import time

from discord.ext import commands, tasks

from .avy import _smart_temp
from ..core.pve import AvyUnreachable

log = logging.getLogger("discord-bot.avymetrics")

# Fraîcheur exigée de l'instantané resources() distant avant d'en tirer des métriques
# (le cycle est de 2 min ; l'instantané est rafraîchi juste avant d'être lu).
AVY_ROWS_MAX_AGE = 60
# L'énumération CIFS de nas-backup coûte ~16 s à travers le tunnel WG, pour une donnée
# (âge de la dernière sauvegarde) qui bouge une fois par jour : inutile de la repayer
# toutes les 2 min. (2026-08-11)
PBS_CACHE_TTL = 900


def _write_failed(conf, data, exc):
    """Rappel d'échec du pipeline d'écriture InfluxDB (mode batching).

    ⚠️ En batching, `write()` met en file et rend la main : l'erreur HTTP est levée dans
    le thread de fond et n'atteint JAMAIS le try/except de `collect()`. Sans ce rappel,
    un jeton révoqué, un bucket renommé ou un disque plein videraient les dashboards
    Grafana « Aveyron » sans une ligne dans les logs (2026-08-11)."""
    log.error("métriques Aveyron: écriture InfluxDB refusée (%s) : %s", conf, exc)


class AvyMetrics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = bool(getattr(bot.cfg, "avy_enabled", False)
                            and getattr(bot.cfg, "avy_influx_token", ""))
        self._client = None
        self._write_api = None
        self._pbs_cache = None       # {vmid: ctime} de la dernière énumération réussie
        self._pbs_cache_ts = 0.0
        if self.enabled:
            try:
                from influxdb_client import InfluxDBClient
                self._client = InfluxDBClient(
                    url=bot.cfg.influx_url, token=bot.cfg.avy_influx_token,
                    org=bot.cfg.influx_org, timeout=20_000)
                # On GARDE le mode batching (réessais automatiques offerts par le client,
                # précieux sur un InfluxDB qui redémarre) mais on branche le rappel
                # d'erreur : sans lui les échecs d'écriture sont totalement invisibles.
                try:
                    self._write_api = self._client.write_api(error_callback=_write_failed)
                except TypeError:      # client trop ancien pour les rappels : on dégrade
                    log.warning("collecteur métriques Aveyron: rappels d'écriture non "
                                "supportés par influxdb-client, échecs non journalisés")
                    self._write_api = self._client.write_api()
                log.info("collecteur métriques Aveyron: client InfluxDB initialisé (bucket %s)",
                         bot.cfg.avy_influx_bucket)
            except Exception:
                log.exception("collecteur métriques Aveyron: init InfluxDB échouée")
                self.enabled = False

    async def cog_load(self):
        if self.enabled:
            self.collect.start()

    def cog_unload(self):
        if self.enabled:
            self.collect.cancel()
        # ⚠️ Fermer le write_api AVANT le client : close() vide le tampon de batch, et le
        # flush tardif du __del__ taperait sinon sur un client déjà fermé (2026-08-11).
        if self._write_api is not None:
            try:
                self._write_api.close()
            except Exception:
                log.warning("métriques Aveyron: fermeture du write_api", exc_info=True)
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                log.warning("métriques Aveyron: fermeture du client InfluxDB",
                            exc_info=True)

    # ------------------------------------------------------------------ collecte

    def _backups(self, pve):
        """{vmid réel: ctime de l'archive la plus récente} — ou None si INCONNU.

        ⚠️ Deux pièges encodés ici (2026-08-11) :
        1. l'énumération CIFS de nas-backup coûte ~16 s à travers le tunnel WG (cf.
           Pve.avy_pbs_content) ; la repayer toutes les 2 min saturait le lien le plus
           fragile de l'infra pour une donnée qui se compte en JOURS — d'où le cache
           PBS_CACHE_TTL. Le cog avy.py, lui, ne la paie qu'une fois par cycle de 5 min ;
        2. une lecture en échec renvoie None et NON un dict vide : un dict vide se
           traduirait par « jamais sauvegardé » pour tout le cluster.
        Le cache n'est jamais peuplé sur exception, et ne court-circuite pas le chemin à
        la demande `Pve.pbs_content(vmid)` des commandes utilisateur."""
        now = time.time()
        if self._pbs_cache is not None and now - self._pbs_cache_ts <= PBS_CACHE_TTL:
            return self._pbs_cache
        table = {}
        try:
            for it in pve.avy_pbs_content() or []:
                v = str(it.get("vmid"))
                table[v] = max(table.get(v, 0), it.get("ctime") or 0)
        except AvyUnreachable:
            return None          # cluster injoignable : déjà signalé par le coupe-circuit
        except Exception:
            log.exception("métriques Aveyron: contenu sauvegardes")
            return None
        self._pbs_cache, self._pbs_cache_ts = table, now
        return table

    def _points_sync(self):
        """Tout le travail réseau (PVE + construction des points) — appelé via
        asyncio.to_thread, comme le reste des accès pve.* dans ce bot."""
        from influxdb_client import Point
        pve = self.bot.pve
        pts = []
        now = time.time()

        for node in pve.avy_nodes():
            try:
                st = pve.avy_node_status(node)
            except AvyUnreachable:
                break            # cluster entier coupé (coupe-circuit) : inutile d'itérer
            except Exception:
                log.warning("métriques Aveyron: nœud %s injoignable", node)
                continue
            mem = st.get("memory") or {}
            p = (Point("system").tag("object", "node").tag("host", node).tag("node", node)
                 .field("cpu", float(st.get("cpu") or 0))
                 .field("mem", int(mem.get("used") or 0))
                 .field("maxmem", int(mem.get("total") or 0))
                 .field("uptime", int(st.get("uptime") or 0)))
            la = st.get("loadavg") or []
            if la:
                try:
                    p.field("load1", float(la[0]))
                except (TypeError, ValueError):
                    pass
            sw = st.get("swap") or {}
            if sw.get("total"):
                p.field("swap", int(sw.get("used") or 0)).field("maxswap", int(sw["total"]))
            rf = st.get("rootfs") or {}
            if rf.get("total"):
                p.field("rootused", int(rf.get("used") or 0)).field("roottotal", int(rf["total"]))
            # pression PSI du nœud (dernier point RRD) — invisible dans /status
            try:
                rrd = pve.avy_node_rrd(node, "hour") or []
                last = rrd[-1] if rrd else {}
                if last.get("pressurecpusome") is not None:
                    p.field("pressurecpusome", float(last["pressurecpusome"]))
                if last.get("pressureiosome") is not None:
                    p.field("pressureiosome", float(last["pressureiosome"]))
            except Exception:
                log.warning("métriques Aveyron: RRD nœud %s indisponible", node)
            pts.append(p)

            # certificat TLS du nœud (jours avant expiration)
            try:
                certs = pve.avy_certificates(node) or []
                exp = min((c.get("notafter") for c in certs if c.get("notafter")), default=None)
                if exp:
                    pts.append(Point("avy_cert").tag("node", node)
                              .field("days_left", round((exp - now) / 86400, 1)))
            except Exception:
                log.warning("métriques Aveyron: certificat %s indisponible", node)

            try:
                for s in pve.avy_node_storages(node) or []:
                    if not s.get("total"):
                        continue
                    pts.append(Point("system").tag("object", "storage")
                              .tag("host", s.get("storage", "?")).tag("node", node)
                              .field("disk", int(s.get("used") or 0))
                              .field("maxdisk", int(s.get("total") or 0)))
            except Exception:
                log.exception("métriques Aveyron: storages %s", node)

            try:
                for d in pve.avy_disks(node) or []:
                    temp = None
                    try:
                        temp = _smart_temp(pve.avy_smart(node, d.get("devpath")) or {})
                    except Exception:
                        pass
                    healthy = str(d.get("health") or "").upper() in ("PASSED", "OK")
                    p = (Point("pve_disk_smart").tag("node", node)
                         .tag("device", d.get("devpath", "?"))
                         .field("healthy", 1 if healthy else 0)
                         .field("size_bytes", int(d.get("size") or 0)))
                    w = d.get("wearout")
                    if w is not None and str(w).isdigit():
                        p.field("wearout_pct", 100 - int(w))
                    if temp is not None:
                        p.field("temp_c", float(temp))
                    pts.append(p)
            except Exception:
                log.exception("métriques Aveyron: disques %s", node)

        # cluster : quorum, nœuds vus par leurs pairs, latence du tunnel WG, jobs backup
        try:
            pc = Point("avy_cluster")
            quorate, online = None, {}
            for e in pve.avy_cluster_status() or []:
                if e.get("type") == "cluster":
                    quorate = bool(e.get("quorate"))
                elif e.get("type") == "node":
                    online[e.get("name")] = bool(e.get("online"))
            if quorate is not None:
                pc.field("quorate", 1 if quorate else 0)
            if online:
                pc.field("nodes_online", sum(1 for v in online.values() if v))
                pc.field("nodes_total", len(online))
            try:
                pc.field("ping_ms", round(pve.avy_ping_ms(), 1))
            except Exception:
                pass
            try:
                jobs = pve.avy_backup_jobs() or []
                pc.field("backup_jobs_enabled", sum(1 for j in jobs if j.get("enabled")))
                pc.field("backup_jobs_total", len(jobs))
            except Exception:
                pass
            pts.append(pc)
        except AvyUnreachable:
            pass                 # cluster injoignable : déjà signalé par le coupe-circuit
        except Exception:
            log.exception("métriques Aveyron: statut cluster")

        sfx = "-" + self.bot.cfg.avy_suffix
        try:
            rows = [r for r in pve.resources()
                   if r.get("name") and pve.is_avy_name(r["name"])]
        except Exception:
            rows = []
            log.exception("métriques Aveyron: resources()")
        # ⚠️ _avy_resources() sert DÉLIBÉRÉMENT le dernier instantané réussi pendant
        # AVY_STALE_MAX (15 min) : cette tolérance existe pour que provision n'archive
        # pas tous les salons AVY sur un blip WireGuard — surtout pas pour alimenter des
        # métriques. Écrits sans horodatage explicite, ces points figés donnent dans
        # Grafana un plateau plat au lieu d'un trou, gardent un invité crashé à
        # status=1 et un débit calculé à 0 o/s. On lit la fraîcheur à la source plutôt
        # que de la déduire (une liste de nœuds vide ne prouve rien). (2026-08-11)
        # ts == 0 = aucun instantané distant n'a jamais réussi : _avy_resources() a
        # renvoyé [] et il n'y a donc rien de périmé à écarter.
        ts = getattr(pve, "_avy_last_ts", 0) or 0
        if rows and ts and time.time() - ts > AVY_ROWS_MAX_AGE:
            log.warning("métriques Aveyron: instantané des invités périmé (%.0f s) — "
                        "aucun point invité écrit ce cycle", time.time() - ts)
            rows = []
        backups = self._backups(pve)

        for r in rows:
            real_name = r["name"].removesuffix(sfx)
            real_vmid = pve.display_vmid(r["vmid"])
            running = r.get("status") == "running"
            p = (Point("system").tag("object", r.get("type", "?"))
                 .tag("host", real_name).tag("node", r.get("node", "?"))
                 .field("status", 1 if running else 0)
                 .field("cpu", float(r.get("cpu") or 0))
                 .field("mem", int(r.get("mem") or 0))
                 .field("maxmem", int(r.get("maxmem") or 0))
                 .field("uptime", int(r.get("uptime") or 0)))
            if r.get("maxdisk"):
                p.field("disk", int(r.get("disk") or 0)).field("maxdisk", int(r["maxdisk"]))
            if r.get("netin") is not None:
                p.field("netin", int(r["netin"])).field("netout", int(r.get("netout") or 0))
            # pression PSI par invité (dernier point RRD) — révèle qui sature, invisible
            # dans resources() ; coûte 1 appel RRD/invité actif par cycle (~19, acceptable
            # sur un cycle de 2 min)
            if running:
                try:
                    g = pve.avy_guest_rrd(r["vmid"], r.get("type"), "hour") or []
                    last = g[-1] if g else {}
                    if last.get("pressurecpusome") is not None:
                        p.field("pressurecpusome", float(last["pressurecpusome"]))
                    if last.get("pressureiosome") is not None:
                        p.field("pressureiosome", float(last["pressureiosome"]))
                except Exception:
                    pass
            pts.append(p)

            # ⚠️ backups vaut None quand l'énumération a ÉCHOUÉ : « inconnu » n'est pas
            # « jamais sauvegardé ». Écrire has_backup=0 pour tout le cluster à chaque
            # hoquet du stockage (5xx, timeout des ~16 s d'énumération CIFS, ACL) est un
            # faux positif qui se lit ensuite comme une alerte dans Grafana (2026-08-11).
            if backups is not None:
                last = backups.get(str(real_vmid))
                pb = Point("pve_backup_age").tag("name", real_name)
                if last:
                    pb.field("age_seconds", int(now - last)).field("has_backup", 1)
                else:
                    pb.field("age_seconds", -1).field("has_backup", 0)
                pts.append(pb)

            # OS + systèmes de fichiers (VM avec guest-agent actif uniquement)
            if running and r.get("type") == "qemu":
                try:
                    ag = pve.avy_agent_info(r["vmid"])
                except Exception:
                    ag = None
                if ag:
                    for mp, used, total in ag.get("fs", [])[:5]:
                        if total:
                            pts.append(Point("avy_guest_fs").tag("name", real_name)
                                      .tag("mountpoint", mp)
                                      .field("used_bytes", int(used))
                                      .field("total_bytes", int(total)))


        return pts

    @tasks.loop(minutes=2)
    async def collect(self):
        try:
            pts = await asyncio.to_thread(self._points_sync)
        except Exception:
            log.exception("métriques Aveyron: collecte échouée")
            return
        if not pts:
            return
        try:
            # en mode batching ce write ne fait que sérialiser et mettre en file : il ne
            # remonte QUE les erreurs locales (point invalide). Les échecs HTTP sont
            # journalisés par _write_failed, dans le thread de fond du client.
            await asyncio.to_thread(
                self._write_api.write, self.bot.cfg.avy_influx_bucket,
                self.bot.cfg.influx_org, pts)
        except Exception:
            log.exception("métriques Aveyron: mise en file de l'écriture InfluxDB échouée")

    @collect.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()




async def setup(bot):
    await bot.add_cog(AvyMetrics(bot))
