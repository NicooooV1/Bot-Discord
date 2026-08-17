"""InfluxDB v2 (Flux) read layer. All queries verified against the live `Proxmox`
bucket. Synchronous client (influxdb-client) — every call is wrapped in
asyncio.to_thread by `aq()` so the discord.py event loop is never blocked.

Schema reminders (load-bearing):
- measurement `system`: tag `object` in {lxc,nodes,storages}; the REAL name is in tag
  `host`. NO `vmid` tag on `system` (name<->vmid mapping comes from the PVE API).
- field `cpu` is fractional (x100 for %). storages: fields used/total/avail.
- host OS stats via Telegraf: `cpu`(usage_idle), `mem`(used_percent), `telegraf_system`(load*).
- custom: pve_thinpool, pve_raid / pve_raid_disk / pve_raid_summary, pve_backup_summary,
  pve_backup_age. node-exporter (metric_version=1): measurement=metric name, field=`gauge`.
"""
import asyncio
import logging
import time

log = logging.getLogger("discord-bot.influx")

RANGE_AGG = {"1h": "30s", "6h": "2m", "24h": "5m", "7d": "30m", "30d": "2h"}

# Champs autorisés de la mesure `system` (invités PVE). Les périodes viennent de
# RANGE_AGG. Ces deux listes blanches ferment par CONSTRUCTION l'interpolation de
# `field`/`num`/`den`/`rng` dans le corps des requêtes Flux : `range(start:-{rng})` est
# hors littéral de chaîne, aucun échappement n'y protégerait quoi que ce soit. Aucun de
# ces paramètres n'est atteignable par l'utilisateur AUJOURD'HUI (choix Discord validés
# côté serveur) — la liste blanche est là pour que l'ajout d'une période libre
# (« /graph range:3d ») reste sûr (2026-08-11).
_FIELDS = {"cpu", "mem", "maxmem", "disk", "maxdisk", "uptime", "status"}


def _esc(v):
    """Neutralise une valeur issue de l'utilisateur avant interpolation dans une
    chaîne Flux entre guillemets : on échappe l'antislash puis le guillemet (comme
    loki._esc) et on retire les sauts de ligne qui casseraient la requête."""
    return (str(v).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\r", "").replace("\n", ""))


class Influx:
    def __init__(self, cfg):
        self.cfg = cfg
        self._qapi = None
        # état de la DERNIÈRE requête : sans lui, « Influx en panne » et « aucune donnée »
        # sont indiscernables pour les ~40 sites qui testent la vérité du résultat, et la
        # boucle d'alertes affiche un ✅ vert mensonger quand la supervision est morte
        # (2026-08-11). On NE lève PAS et on continue de renvoyer [] : tous ces appelants
        # supposent une liste.
        self._fail_count = 0
        self._last_error = None
        self._last_error_ts = 0.0
        self._last_ok_ts = 0.0
        if cfg.influx_enabled:
            try:
                from influxdb_client import InfluxDBClient
                self._client = InfluxDBClient(
                    url=cfg.influx_url, token=cfg.influx_token,
                    org=cfg.influx_org, timeout=10_000)
                self._qapi = self._client.query_api()
            except Exception:
                log.exception("InfluxDB client init failed")
                self._qapi = None

    @property
    def enabled(self):
        return self._qapi is not None

    @property
    def _b(self):
        return self.cfg.influx_bucket

    @property
    def blind(self):
        """Vrai quand la dernière requête Flux a ÉCHOUÉ (Influx arrêté, jeton révoqué,
        bucket renommé, timeout) — à distinguer de « la requête a renvoyé zéro ligne ».

        À consulter par la boucle d'alertes AVANT de conclure « tout est au vert » : sans
        ça, un CT103 tombé la nuit rend la supervision aveugle en silence et /alerts
        répond ✅ (feu vert mensonger). `enabled` reste la question séparée « InfluxDB
        est-il configuré ? »."""
        return self._fail_count > 0

    @property
    def last_error(self):
        """Message de la dernière requête en échec (None si la dernière a réussi)."""
        return self._last_error if self._fail_count else None

    # --- low level ---
    def _query(self, flux):
        if not self.enabled:
            return []
        try:
            tables = self._qapi.query(flux)
        except Exception as e:
            self._fail_count += 1
            self._last_error = f"{type(e).__name__}: {e}"[:200]
            self._last_error_ts = time.time()
            log.exception("flux query failed:\n%s", flux)
            return []
        self._fail_count = 0
        self._last_ok_ts = time.time()
        out = []
        for t in tables:
            for r in t.records:
                out.append(dict(r.values))
        return out

    async def aq(self, flux):
        return await asyncio.to_thread(self._query, flux)

    # --- write (line protocol) ---
    def _write(self, records):
        if not self.enabled:
            return False
        try:
            from influxdb_client.client.write_api import SYNCHRONOUS
            self._client.write_api(write_options=SYNCHRONOUS).write(
                bucket=self._b, org=self.cfg.influx_org, record=records)
            return True
        except Exception:
            log.exception("influx write failed")
            return False

    async def write(self, records):
        """records = ligne(s) line-protocol. Écrit dans le bucket Proxmox."""
        return await asyncio.to_thread(self._write, records)

    # --- NAS Synology (nas_pbs_storage = capacité ; synology* = santé SNMP) ---
    async def nas_capacity(self):
        rows = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-10m) '
            f'|> filter(fn:(r)=> r._measurement=="nas_pbs_storage" '
            f'and (r._field=="total" or r._field=="used" or r._field=="avail")) '
            f'|> last() |> pivot(rowKey:["source"], columnKey:["_field"], valueColumn:"_value")')
        return rows[0] if rows else None

    async def nas_health(self):
        """Santé NAS via SNMP. Renvoie {} tant que SNMP n'est pas activé dans DSM."""
        async def piv(meas, keys):
            rk = ", ".join(f'"{k}"' for k in keys)
            return await self.aq(
                f'from(bucket:"{self._b}") |> range(start:-15m) '
                f'|> filter(fn:(r)=> r._measurement=="{meas}") |> last() '
                f'|> pivot(rowKey:[{rk}], columnKey:["_field"], valueColumn:"_value")')
        sysr = await piv("synology", ["source"])
        out = {"system": sysr[0] if sysr else None,
               "disks": await piv("synology_disk", ["disk_id"]),
               "raid": await piv("synology_raid", ["raid_name"])}
        out["smart"] = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-15m) '
            f'|> filter(fn:(r)=> r._measurement=="synology_smart" and r._field=="raw" '
            f'and (r.attr_name=="Reallocated_Sector_Ct" or r.attr_name=="Current_Pending_Sector")) '
            f'|> last() |> group() '
            f'|> pivot(rowKey:["dev","attr_name"], columnKey:["_field"], valueColumn:"_value")')
        return out

    # --- Jellyfin (sessions / qui est connecté / bibliothèque) pour le salon #jellyfin ---
    async def jellyfin_stats(self):
        s = await self.aq(f'from(bucket:"{self._b}") |> range(start:-3m) '
                          f'|> filter(fn:(r)=> r._measurement=="jellyfin_sessions") |> last() '
                          f'|> pivot(rowKey:["host"], columnKey:["_field"], valueColumn:"_value")')
        up = await self.aq(f'from(bucket:"{self._b}") |> range(start:-3m) '
                           f'|> filter(fn:(r)=> r._measurement=="jellyfin_up") |> last()')
        lib = await self.aq(f'from(bucket:"{self._b}") |> range(start:-1h) '
                            f'|> filter(fn:(r)=> r._measurement=="jellyfin_library") |> last() '
                            f'|> pivot(rowKey:["host"], columnKey:["_field"], valueColumn:"_value")')
        upv = None
        if up and up[0].get("_value") not in (None, ""):
            try:
                upv = int(float(up[0]["_value"]))
            except (TypeError, ValueError):
                upv = None
        return {"sessions": s[0] if s else {}, "up": upv, "library": lib[0] if lib else {}}

    async def jellyfin_who(self):
        """Sessions actives MAINTENANT (fenêtre courte) : user/client/method/bitrate."""
        return await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-2m) '
            f'|> filter(fn:(r)=> r._measurement=="jellyfin_session" and r._field=="bitrate_bps") '
            f'|> last() |> group() '
            f'|> pivot(rowKey:["user","client","method"], columnKey:["_field"], valueColumn:"_value")')

    def health(self):
        if not self.enabled:
            return False
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    @staticmethod
    def _series(rows):
        return [r.get("_time") for r in rows], [r.get("_value") for r in rows]

    @staticmethod
    def _agg(rng):
        return RANGE_AGG.get(rng, "5m")

    @staticmethod
    def _rng(rng):
        """Période SÛRE pour `range(start:-…)`. Même esprit de repli que _agg (qui, lui,
        était déjà protégé par RANGE_AGG.get) : une période inconnue redevient « 24h »
        au lieu d'être injectée telle quelle dans le corps de la requête (2026-08-11)."""
        return rng if rng in RANGE_AGG else "24h"

    # --- live tables ---
    async def ct_table(self):
        """All running LXC with cpu%, ram%, disk%, uptime (one row per CT)."""
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-3m)
  |> filter(fn:(r)=> r._measurement=="system" and (r.object=="lxc" or r.object=="qemu")
        and (r._field=="cpu" or r._field=="mem" or r._field=="maxmem"
          or r._field=="disk" or r._field=="maxdisk" or r._field=="status"
          or r._field=="uptime"))
  |> last()
  |> pivot(rowKey:["host"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        out = []
        for r in rows:
            maxmem = r.get("maxmem") or 0
            maxdisk = r.get("maxdisk") or 0
            out.append({
                "name": r.get("host"),
                "status": r.get("status"),
                "running": (r.get("status") == "running") or (r.get("uptime") or 0) > 0,
                "cpu_pct": (r.get("cpu") or 0) * 100.0,
                "mem": r.get("mem") or 0,
                "maxmem": maxmem,
                "ram_pct": (r.get("mem") or 0) / maxmem * 100.0 if maxmem else 0.0,
                "disk": r.get("disk") or 0,
                "maxdisk": maxdisk,
                "disk_pct": (r.get("disk") or 0) / maxdisk * 100.0 if maxdisk else 0.0,
                "uptime": r.get("uptime") or 0,
            })
        out.sort(key=lambda x: x["cpu_pct"], reverse=True)
        return out

    async def storages(self):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-5m)
  |> filter(fn:(r)=> r._measurement=="system" and r.object=="storages"
        and (r._field=="used" or r._field=="total" or r._field=="avail"))
  |> last()
  |> pivot(rowKey:["host"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        out = []
        for r in rows:
            total = r.get("total") or 0
            out.append({"name": r.get("host"), "used": r.get("used") or 0,
                        "total": total, "avail": r.get("avail") or 0,
                        "pct": (r.get("used") or 0) / total * 100.0 if total else 0.0})
        out.sort(key=lambda x: x["pct"], reverse=True)
        return out

    async def thinpool(self, pool="local-lvm"):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-15m)
  |> filter(fn:(r)=> r._measurement=="pve_thinpool" and r.pool=="{_esc(pool)}"
        and (r._field=="pool_bytes" or r._field=="data_used_bytes"
          or r._field=="allocated_bytes" or r._field=="overcommit_percent"
          or r._field=="data_percent" or r._field=="meta_percent"))
  |> last()
  |> pivot(rowKey:["pool"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        return rows[0] if rows else None

    # --- hardware ---
    async def raid(self):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-20m)
  |> filter(fn:(r)=> r._measurement=="pve_raid"
        and (r._field=="vd_optimal" or r._field=="batt_good" or r._field=="rebuild_percent"))
  |> last()
  |> pivot(rowKey:["controller"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        ctrl = rows[0] if rows else None
        summ_flux = f'''
from(bucket:"{self._b}")
  |> range(start:-20m)
  |> filter(fn:(r)=> r._measurement=="pve_raid_summary"
        and (r._field=="disks_online" or r._field=="disks_total"))
  |> last() |> pivot(rowKey:["_measurement"], columnKey:["_field"], valueColumn:"_value")'''
        srows = await self.aq(summ_flux)
        summ = srows[0] if srows else None
        return ctrl, summ

    async def raid_disks(self):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-20m)
  |> filter(fn:(r)=> r._measurement=="pve_raid_disk"
        and (r._field=="grown_defects" or r._field=="media_errors"
          or r._field=="other_errors" or r._field=="online"))
  |> last()
  |> pivot(rowKey:["slot"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        rows.sort(key=lambda r: int(r.get("slot", 0)))
        return rows

    async def gauge_table(self, measurement, label_keys=("name", "device", "chip", "sensor")):
        """Generic node-exporter reader: measurement=metric, field=gauge -> [(label, value)]."""
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-10m)
  |> filter(fn:(r)=> r._measurement=="{measurement}" and r._field=="gauge")
  |> last()'''
        rows = await self.aq(flux)
        out = []
        for r in rows:
            label = next((str(r[k]) for k in label_keys if r.get(k)), measurement)
            out.append((label, r.get("_value")))
        out.sort(key=lambda x: x[0])
        return out

    async def ipmi_temps(self):
        return await self.gauge_table("node_ipmi_temperature_celsius")

    async def ipmi_fans(self):
        return await self.gauge_table("node_ipmi_speed_rpm")

    async def ipmi_power(self):
        return await self.gauge_table("node_ipmi_power_watts")

    async def hwmon_temps(self):
        return await self.gauge_table("node_hwmon_temp_celsius")

    # Les séries smartmon ne portent NI `name` NI `device` : le tag `disk` vaut
    # « /dev/bus/0 » pour TOUS les disques (accès megaraid) et seul `type`
    # (« megaraid,0 » … « megaraid,15 ») les distingue. Sans ce label_keys, gauge_table
    # retombait sur le nom de la mesure -> 16 lignes identiques, et le dict(...) de
    # /smart écrasait tout sur une seule entrée (corrigé 2026-07-15).
    _SMART_LABELS = ("type", "disk")

    async def smart_health(self):
        return await self.gauge_table("smartmon_device_smart_healthy",
                                      label_keys=self._SMART_LABELS)

    async def smart_temps(self):
        return await self.gauge_table("smartmon_temperature_celsius_raw_value",
                                      label_keys=self._SMART_LABELS)

    async def smart_grown(self):
        return await self.gauge_table("smartmon_grown_defects_count_raw_value",
                                      label_keys=self._SMART_LABELS)

    # --- backups ---
    async def backup_summary(self):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-2h)
  |> filter(fn:(r)=> r._measurement=="pve_backup_summary")
  |> last() |> pivot(rowKey:["_measurement"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        return rows[0] if rows else None

    async def backup_ages(self):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-2h)
  |> filter(fn:(r)=> r._measurement=="pve_backup_age"
        and (r._field=="age_seconds" or r._field=="has_backup"
          or r._field=="size_bytes" or r._field=="snapshots"))
  |> last() |> pivot(rowKey:["vmid","name"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        rows.sort(key=lambda r: (r.get("age_seconds") or 0), reverse=True)
        return rows

    # --- débits réseau (par invité + WAN routeur) ---
    async def net_rates(self, rng="1h"):
        """Débits réseau par invité (moyenne ET pic) sur la période.

        `netin`/`netout` remontés par PVE sont des COMPTEURS CUMULÉS depuis le
        démarrage de l'invité : sans `derivative()` on afficherait des octets totaux,
        pas un débit. `nonNegative` écarte les remises à zéro lors d'un redémarrage,
        qui produiraient sinon un pic négatif aberrant.

        ⚠️ Ce sont les débits TOTAUX de l'interface : LAN et Internet confondus.
        Seul `wan_rates()` mesure réellement ce qui sort vers Internet.

        Renvoie [{name, vmid, object, in_avg, in_max, out_avg, out_max}, …].
        """
        rng = self._rng(rng)

        def flux(agg):
            return f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="system"
        and (r.object=="lxc" or r.object=="qemu" or r.object=="nodes")
        and (r._field=="netin" or r._field=="netout"))
  |> derivative(unit:1s, nonNegative:true)
  |> group(columns:["host","vmid","object","_field"])
  |> {agg}()'''

        acc = {}
        for agg, suffix in (("mean", "avg"), ("max", "max")):
            for r in await self.aq(flux(agg)):
                host = r.get("host")
                if not host:
                    continue
                e = acc.setdefault(host, {"name": host, "vmid": r.get("vmid"),
                                          "object": r.get("object")})
                direction = "in" if r.get("_field") == "netin" else "out"
                try:
                    e[f"{direction}_{suffix}"] = float(r.get("_value") or 0)
                except (TypeError, ValueError):
                    e[f"{direction}_{suffix}"] = 0.0
        rows = list(acc.values())
        for e in rows:
            for k in ("in_avg", "in_max", "out_avg", "out_max"):
                e.setdefault(k, 0.0)
        rows.sort(key=lambda e: e["in_avg"] + e["out_avg"], reverse=True)
        return rows

    async def wan_rates(self, rng="1h", ifname="sfp-sfpplus1"):
        """Débit réel vers Internet, mesuré sur l'interface WAN du MikroTik.

        Même logique de dérivée que net_rates : `in_octets`/`out_octets` sont des
        compteurs SNMP cumulés. `ifname` par défaut = l'uplink vers la Box.
        Attention au sens : `in_octets` sur le WAN = ce qui DESCEND depuis Internet.
        """
        rng = self._rng(rng)
        safe_if = _esc(ifname)

        def flux(agg):
            return f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="mikrotik_interface"
        and r.ifname=="{safe_if}"
        and (r._field=="in_octets" or r._field=="out_octets"))
  |> derivative(unit:1s, nonNegative:true)
  |> group(columns:["_field"])
  |> {agg}()'''

        out = {"ifname": ifname}
        for agg, suffix in (("mean", "avg"), ("max", "max")):
            for r in await self.aq(flux(agg)):
                direction = "down" if r.get("_field") == "in_octets" else "up"
                try:
                    out[f"{direction}_{suffix}"] = float(r.get("_value") or 0)
                except (TypeError, ValueError):
                    out[f"{direction}_{suffix}"] = 0.0
        for k in ("down_avg", "down_max", "up_avg", "up_max"):
            out.setdefault(k, 0.0)
        return out

    # --- time series (for graphs) ---
    async def ct_series(self, host, field, rng="24h"):
        if field not in _FIELDS:
            log.warning("ct_series: champ %r hors liste blanche, requête abandonnée", field)
            return [], []
        rng = self._rng(rng)
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="system" and (r.object=="lxc" or r.object=="qemu")
        and r.host=="{_esc(host)}" and r._field=="{field}")
  |> aggregateWindow(every:{self._agg(rng)}, fn:mean, createEmpty:false)
  |> keep(columns:["_time","_value"])'''
        return self._series(await self.aq(flux))

    # --- réseau / WAN / uptime / services / coût (nouveaux flux) ---
    async def router_status(self):
        h = await self.aq(f'from(bucket:"{self._b}") |> range(start:-3m) '
                          f'|> filter(fn:(r)=> r._measurement=="mikrotik") |> last() '
                          f'|> pivot(rowKey:["device"], columnKey:["_field"], valueColumn:"_value")')
        ifs = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-3m) '
            f'|> filter(fn:(r)=> r._measurement=="mikrotik_interface" '
            f'and (r._field=="oper_status" or r._field=="in_errors" or r._field=="out_errors")) '
            f'|> last() |> pivot(rowKey:["ifname"], columnKey:["_field"], valueColumn:"_value")')
        return {"host": h[0] if h else {}, "ifs": ifs}

    async def wan_status(self):
        ping = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-3m) '
            f'|> filter(fn:(r)=> r._measurement=="ping" '
            f'and (r._field=="average_response_ms" or r._field=="percent_packet_loss")) '
            f'|> last() |> pivot(rowKey:["url"], columnKey:["_field"], valueColumn:"_value")')
        sp = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-45m) '
            f'|> filter(fn:(r)=> r._measurement=="wan_speedtest") |> last() '
            f'|> pivot(rowKey:["engine"], columnKey:["_field"], valueColumn:"_value")')
        return {"ping": ping, "speedtest": sp[0] if sp else {}}

    async def vhost_uptime(self):
        return await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-4m) '
            f'|> filter(fn:(r)=> r._measurement=="http_response" and exists r.site '
            f'and (r._field=="http_response_code" or r._field=="response_time")) '
            f'|> last() |> pivot(rowKey:["site"], columnKey:["_field"], valueColumn:"_value")')

    async def service_health(self):
        net = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-3m) '
            f'|> filter(fn:(r)=> r._measurement=="net_response" '
            f'and (r._field=="result_code" or r._field=="response_time")) '
            f'|> last() |> pivot(rowKey:["service"], columnKey:["_field"], valueColumn:"_value")')
        http = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-3m) '
            f'|> filter(fn:(r)=> r._measurement=="http_response" and exists r.service '
            f'and (r._field=="result_code" or r._field=="http_response_code" or r._field=="response_time")) '
            f'|> last() |> pivot(rowKey:["service"], columnKey:["_field"], valueColumn:"_value")')
        return {"net": net, "http": http}

    async def power_cost(self):
        r = await self.aq(f'from(bucket:"{self._b}") |> range(start:-45m) '
                          f'|> filter(fn:(r)=> r._measurement=="power_cost") |> last() '
                          f'|> pivot(rowKey:["_measurement"], columnKey:["_field"], valueColumn:"_value")')
        return r[0] if r else None

    async def disk_forecast(self, path):
        """Jours avant saturation d'un disque hôte (projection linéaire sur l'écart RÉEL
        entre les deux points, ~7-8 j), avec les tailles réelles (used/total en octets)
        pour l'affichage — pas seulement le % (demande Nico 2026-07-20 : « les vraies
        tailles de données type 3.3To / 3.8To »)."""
        esc = _esc(path)
        pct_filt = (f'|> filter(fn:(r)=> r._measurement=="disk" '
                    f'and r.host=="{self.cfg.pve_node}" and r.path=="{esc}" '
                    f'and r._field=="used_percent")')
        size_filt = (f'|> filter(fn:(r)=> r._measurement=="disk" '
                     f'and r.host=="{self.cfg.pve_node}" and r.path=="{esc}" '
                     f'and (r._field=="used" or r._field=="total")) '
                     f'|> last() |> pivot(rowKey:["_measurement"], columnKey:["_field"], valueColumn:"_value")')
        now = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-10m) {pct_filt} |> last()')
        past = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-8d, stop:-6d) {pct_filt} |> first()')
        size = await self.aq(f'from(bucket:"{self._b}") |> range(start:-10m) {size_filt}')

        def v(rows):
            try:
                return float(rows[0]["_value"]) if rows else None
            except (TypeError, ValueError, KeyError):
                return None
        cur, old = v(now), v(past)
        if cur is None:
            return None
        used_bytes = total_bytes = None
        if size:
            try:
                used_bytes = float(size[0].get("used"))
                total_bytes = float(size[0].get("total"))
            except (TypeError, ValueError):
                pass
        out = {"current": cur, "used_bytes": used_bytes, "total_bytes": total_bytes}
        if old is None or cur <= old:
            out["days"] = None                        # pas de croissance mesurable
            return out
        # Le dénominateur était figé à 7 j alors que `first()` sur [-8d,-6d] renvoie le
        # point le PLUS ANCIEN de la fenêtre, mesuré il y a ~8 j : le taux sortait
        # surestimé de ~14 % et la date de saturation avancée d'autant (bascule en rouge
        # trop tôt, seuils 30/90 j de provision). On calcule l'écart réel à partir des
        # horodatages ; repli sur 7 j si `_time` manque, plutôt que pas de projection
        # du tout (2026-08-11).
        try:
            elapsed = (now[0]["_time"] - past[0]["_time"]).total_seconds() / 86400.0
        except (KeyError, IndexError, TypeError, AttributeError):
            elapsed = None
        if not elapsed or elapsed <= 0:
            elapsed = 7.0
        rate = (cur - old) / elapsed                 # %/jour
        out["rate"] = rate
        out["days"] = (100 - cur) / rate if rate > 0 else None
        return out

    async def ct_net_rate(self, name):
        """Débit réseau instantané (octets/s) d'un CT via node_network_* (dérivée)."""
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-5m)
  |> filter(fn:(r)=> (r._measurement=="node_network_receive_bytes_total"
        or r._measurement=="node_network_transmit_bytes_total")
        and r.ct=="{_esc(name)}" and r.device!="lo")
  |> derivative(unit:1s, nonNegative:true)
  |> group(columns:["_measurement"]) |> last()'''
        rx = tx = 0.0
        for r in await self.aq(flux):
            try:
                v = float(r.get("_value") or 0)
            except (TypeError, ValueError):
                continue
            if "receive" in (r.get("_measurement") or ""):
                rx += v
            else:
                tx += v
        return rx, tx

    async def ct_peak_24h(self, name):
        """Pic CPU% et RAM% sur 24 h (la tendance que l'ancien graphique montrait)."""
        esc = _esc(name)
        base = (f'from(bucket:"{self._b}") |> range(start:-24h) '
                f'|> filter(fn:(r)=> r._measurement=="system" '
                f'and (r.object=="lxc" or r.object=="qemu") and r.host=="{esc}"')
        cpu = await self.aq(base + ' and r._field=="cpu") |> max()')
        memmax = await self.aq(base + ' and r._field=="mem") |> max()')
        mm = await self.aq(
            f'from(bucket:"{self._b}") |> range(start:-5m) '
            f'|> filter(fn:(r)=> r._measurement=="system" '
            f'and (r.object=="lxc" or r.object=="qemu") and r.host=="{esc}" '
            f'and r._field=="maxmem") |> last()')

        def val(rows):
            if rows and rows[0].get("_value") not in (None, ""):
                try:
                    return float(rows[0]["_value"])
                except (TypeError, ValueError):
                    return None
            return None
        cpu_max = val(cpu)
        mem_peak = val(memmax)
        maxmem = val(mm)
        return {
            "cpu_max_pct": cpu_max * 100 if cpu_max is not None else None,
            "ram_peak_pct": (mem_peak / maxmem * 100) if (mem_peak and maxmem) else None,
            "ram_peak_bytes": mem_peak,
        }

    async def host_cpu_series(self, rng="24h"):
        rng = self._rng(rng)
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="cpu" and r.host=="{self.cfg.pve_node}"
        and r.cpu=="cpu-total" and r._field=="usage_idle")
  |> aggregateWindow(every:{self._agg(rng)}, fn:mean, createEmpty:false)
  |> map(fn:(r)=>({{ r with _value: 100.0 - r._value }}))
  |> keep(columns:["_time","_value"])'''
        return self._series(await self.aq(flux))

    async def host_mem_series(self, rng="24h"):
        rng = self._rng(rng)
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="mem" and r.host=="{self.cfg.pve_node}"
        and r._field=="used_percent")
  |> aggregateWindow(every:{self._agg(rng)}, fn:mean, createEmpty:false)
  |> keep(columns:["_time","_value"])'''
        return self._series(await self.aq(flux))

    async def host_load(self):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-5m)
  |> filter(fn:(r)=> r._measurement=="telegraf_system" and r.host=="{self.cfg.pve_node}"
        and (r._field=="load1" or r._field=="load5" or r._field=="load15"))
  |> last() |> pivot(rowKey:["host"], columnKey:["_field"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        return rows[0] if rows else None

    async def host_net_series(self, instance="vmbr0", rng="24h"):
        rng = self._rng(rng)
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="nics" and r.object=="nodes"
        and r.instance=="{_esc(instance)}" and (r._field=="receive" or r._field=="transmit"))
  |> aggregateWindow(every:{self._agg(rng)}, fn:mean, createEmpty:false)
  |> derivative(unit:1s, nonNegative:true)'''
        rows = await self.aq(flux)
        rx_t, rx_v, tx_t, tx_v = [], [], [], []
        for r in rows:
            if r.get("_field") == "receive":
                rx_t.append(r["_time"]); rx_v.append(r["_value"])
            elif r.get("_field") == "transmit":
                tx_t.append(r["_time"]); tx_v.append(r["_value"])
        return (rx_t, rx_v), (tx_t, tx_v)

    async def host_disk_series(self, path="/", rng="24h"):
        rng = self._rng(rng)
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="disk" and r.host=="{self.cfg.pve_node}"
        and r.path=="{_esc(path)}" and r._field=="used_percent")
  |> aggregateWindow(every:{self._agg(rng)}, fn:mean, createEmpty:false)
  |> keep(columns:["_time","_value"])'''
        return self._series(await self.aq(flux))

    async def ct_pct_series(self, host, num, den, rng="24h"):
        # num/den finissent DANS un map() (`r.{num} / r.{den}`) : liste blanche obligatoire
        if num not in _FIELDS or den not in _FIELDS:
            log.warning("ct_pct_series: champs %r/%r hors liste blanche, requête abandonnée",
                        num, den)
            return [], []
        rng = self._rng(rng)
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-{rng})
  |> filter(fn:(r)=> r._measurement=="system" and (r.object=="lxc" or r.object=="qemu") and r.host=="{_esc(host)}"
        and (r._field=="{num}" or r._field=="{den}"))
  |> aggregateWindow(every:{self._agg(rng)}, fn:mean, createEmpty:false)
  |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")
  |> map(fn:(r)=>({{ _time: r._time, _value: r.{num} / r.{den} * 100.0 }}))
  |> filter(fn:(r)=> exists r._value)
  |> keep(columns:["_time","_value"])'''
        return self._series(await self.aq(flux))

    # --- per-CT in-guest metrics (node_exporter scraped by host telegraf, tag ct=) ---
    async def ct_sysinfo(self, ct):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-5m)
  |> filter(fn:(r)=> r.ct=="{_esc(ct)}" and r._field=="gauge"
        and (r._measurement=="node_load1" or r._measurement=="node_load5"
          or r._measurement=="node_load15" or r._measurement=="node_memory_MemTotal_bytes"
          or r._measurement=="node_memory_MemAvailable_bytes"
          or r._measurement=="node_memory_SwapTotal_bytes" or r._measurement=="node_memory_SwapFree_bytes"
          or r._measurement=="node_procs_running" or r._measurement=="node_procs_blocked"
          or r._measurement=="node_boot_time_seconds"))
  |> last()
  |> keep(columns:["_measurement","_value"])'''
        rows = await self.aq(flux)
        return {r["_measurement"]: r["_value"] for r in rows if r.get("_value") is not None}

    async def ct_filesystems(self, ct):
        flux = f'''
from(bucket:"{self._b}")
  |> range(start:-5m)
  |> filter(fn:(r)=> r.ct=="{_esc(ct)}" and r._field=="gauge"
        and (r._measurement=="node_filesystem_size_bytes" or r._measurement=="node_filesystem_avail_bytes")
        and r.fstype != "tmpfs" and r.fstype != "devtmpfs" and r.fstype != "overlay")
  |> last()
  |> pivot(rowKey:["mountpoint"], columnKey:["_measurement"], valueColumn:"_value")'''
        rows = await self.aq(flux)
        out = []
        for r in rows:
            size = r.get("node_filesystem_size_bytes")
            avail = r.get("node_filesystem_avail_bytes") or 0
            if size:
                out.append({"mount": r.get("mountpoint"), "size": size, "avail": avail,
                            "used": size - avail, "pct": (size - avail) / size * 100.0})
        out.sort(key=lambda x: x["pct"], reverse=True)
        return out
