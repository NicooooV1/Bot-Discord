"""Supervision des serveurs Aveyron (AVY-PVE / AVY-NAS / AVY-LLM).

Chaque nœud du cluster Aveyron est traité comme un serveur à part entière (choix Nico
2026-07-17) : sa catégorie « 📊 Supervision AVY-X » (créée par provision) contient
#alertes, #stockage et #sauvegardes ; #hyperviseur vit à part, dans « 🔒 Lock AVY-X »
(propriétaire uniquement, choix Nico 2026-07-18 — cf. provision._ensure_avy_lock_
hyperviseur). Ce cog tient les embeds épinglés de ces quatre salons (source : API PVE
distante uniquement — pas d'Influx/Loki là-bas) et pousse dans #alertes des
événements EDGE-TRIGGERED :
  - nœud injoignable (2 cycles consécutifs) puis rétabli ;
  - stockage > 85 % (réarmé sous 80 %) ;
  - invité qui passe de running à stopped (et retour) ;
  - tâche vzdump en échec.

⚠️ Dans guest_map, les invités du R820 portent AUSSI node="pve" (nom du nœud R820) :
tout filtre par nœud doit être combiné à is_avy_name."""
import asyncio
import datetime
import logging
import re
import time

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.permissions import admin_button_ok
from ..core.pve import LlmExecError
from ..views.confirm import ConfirmView

log = logging.getLogger("discord-bot.avy")

STO_ALERT_PCT = 85     # seuil d'alerte stockage
STO_CLEAR_PCT = 80     # réarmement (hystérésis)
DISK_TEMP_ALERT = 70   # °C — alerte température disque (réarmée sous 65)
DISK_TEMP_CLEAR = 65
WEAROUT_ALERT = 20     # % de vie restante (PVE: 100 = neuf)
CERT_ALERT_DAYS = 30   # certificat qui expire bientôt (réarmé > 45 j)
CERT_CLEAR_DAYS = 45
LAT_ALERT_MS = 800     # tunnel WG dégradé (réarmé < 400)
LAT_CLEAR_MS = 400
AUTH_FAIL_MIN = 3      # échecs d'auth PVE par cycle avant alerte
BACKUP_STALE_DAYS = 7  # ⚠️ invité sans sauvegarde récente

# --- assistant IA locale (VM ubuntu-llm, RTX 3090) ---
GPU_TEMP_ALERT = 80    # °C (réarmé < 70)
GPU_TEMP_CLEAR = 70
GPU_VRAM_FREE_ALERT = 512 * 2**20   # < 512 Mio de VRAM libre = plus de marge pour de nouvelles requêtes
GPU_VRAM_FREE_CLEAR = 1536 * 2**20
LLM_DISK_FREE_ALERT = 15 * 2**30    # < 15 Gio libres = ~pas de place pour un nouveau modèle
LLM_DISK_FREE_CLEAR = 25 * 2**30
LLM_CORE_SERVICES = ("llama-server", "litellm", "llm-router")  # llama-server-small = secondaire, pas alerté


def _smart_temp(sm):
    """Température (°C) depuis la réponse SMART PVE : blob texte NVMe ou table
    d'attributs SATA (id 194). None si introuvable."""
    try:
        txt = sm.get("text")
        if txt:
            m = re.search(r"Temperature:\s+(\d+)\s+Celsius", txt)
            return int(m.group(1)) if m else None
        for a in sm.get("attributes") or []:
            if str(a.get("id")).strip() == "194" or "Temperature" in str(a.get("name", "")):
                m = re.search(r"\d+", str(a.get("raw", "")))
                return int(m.group(0)) if m else None
    except Exception:
        pass
    return None


class Avy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = getattr(bot.cfg, "avy_enabled", False)
        self._cluster = {}    # instantané cluster du cycle (quorum, latence, jobs)

    async def cog_load(self):
        self.bot.add_view(AvyNodeView(self))
        if self.enabled:
            self.refresh.start()

    def cog_unload(self):
        if self.enabled:
            self.refresh.cancel()

    # ------------------------------------------------------------------ helpers

    def _sup(self):
        """prov['avy_sup'] = {node: {alertes/hyperviseur/stockage/sauvegardes: id}}."""
        p = self.bot.get_cog("Provision")
        if p is not None:
            return p.prov.get("avy_sup", {}) or {}
        return (self.bot.state.get("prov", {}) or {}).get("avy_sup", {}) or {}

    def node_of_channel(self, channel_id):
        """(node, clé serveur) du salon hyperviseur cliqué, ou (None, None)."""
        for node, chans in self._sup().items():
            if chans.get("hyperviseur") == channel_id:
                return node, self.bot.pve.avy_server_key(node)
        return None, None

    def _node_guests(self, node):
        """[(name, info)] des invités -avy de CE nœud (cf. avertissement d'en-tête)."""
        gm = self.bot.pve.guest_map()
        return sorted(((n, i) for n, i in gm.items()
                       if self.bot.pve.is_avy_name(n) and i.get("node") == node),
                      key=lambda x: x[1].get("vmid") or 0)

    async def _send_alert(self, node, text):
        cid = self._sup().get(node, {}).get("alertes")
        ch = self.bot.get_channel(cid) if cid else None
        if ch is None:
            return
        try:
            await ch.send(text, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    async def _pin_edit(self, channel_id, emb, view=None):
        """Édite le message épinglé du salon (créé+épinglé au premier passage)."""
        if not channel_id:
            return
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            return
        msgs = dict(self.bot.state.get("avy_msgs", {}) or {})
        mid = msgs.get(str(channel_id))
        msg = None
        if mid:
            try:
                msg = await ch.fetch_message(mid)
            except discord.NotFound:
                msg = None
            except discord.HTTPException:
                return
        kw = {"embed": emb}
        if view is not None:
            kw["view"] = view
        try:
            if msg is None:
                msg = await ch.send(**kw)
                try:
                    await msg.pin()
                except discord.HTTPException:
                    pass
                msgs[str(channel_id)] = msg.id
                self.bot.state.set("avy_msgs", msgs)
            else:
                await msg.edit(**kw)
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------ embeds

    def _collect(self, node):
        """Toutes les lectures API d'un nœud (synchrone, appelé via to_thread)."""
        pve = self.bot.pve
        out = {"status": None, "storages": [], "tasks": [], "rows": [],
               "rrd_last": {}, "disks": []}
        out["status"] = pve.avy_node_status(node)          # lève si nœud injoignable
        try:
            out["storages"] = pve.avy_node_storages(node) or []
        except Exception:
            pass
        try:
            out["tasks"] = pve.avy_node_tasks(node, limit=20) or []
        except Exception:
            pass
        # dernier point RRD du nœud : pression PSI CPU/IO (invisible dans status)
        try:
            rows = pve.avy_node_rrd(node, "hour") or []
            out["rrd_last"] = rows[-1] if rows else {}
        except Exception:
            pass
        # disques physiques + température SMART (2-3 disques par nœud)
        try:
            for d in pve.avy_disks(node) or []:
                temp = None
                try:
                    temp = _smart_temp(pve.avy_smart(node, d.get("devpath")) or {})
                except Exception:
                    pass
                out["disks"].append({**d, "temp": temp})
        except Exception:
            pass
        # lignes /cluster/resources des invités DU nœud (métriques internes : cpu, mem,
        # disk, compteurs netin/netout, uptime) — cf. avertissement d'en-tête (node=pve
        # existe des deux côtés, toujours filtrer avec is_avy_name)
        try:
            out["rows"] = [r for r in pve.resources()
                           if r.get("name") and pve.is_avy_name(r["name"])
                           and r.get("node") == node]
        except Exception:
            pass
        return out

    def _cluster_collect(self):
        """Lectures de niveau CLUSTER (une fois par cycle, sync) : quorum/nœuds vus par
        leurs pairs, latence du tunnel, certificats, journal (auth), jobs de backup."""
        pve = self.bot.pve
        out = {"ok": False, "quorate": None, "online": {}, "ping_ms": None,
               "certs": {}, "log": [], "jobs_disabled": None}
        try:
            out["ping_ms"] = pve.avy_ping_ms()
        except Exception:
            return out
        try:
            for e in pve.avy_cluster_status() or []:
                if e.get("type") == "cluster":
                    out["quorate"] = bool(e.get("quorate"))
                elif e.get("type") == "node":
                    out["online"][e.get("name")] = bool(e.get("online"))
            out["ok"] = True
        except Exception:
            pass
        for n in pve.avy_nodes():
            try:
                certs = pve.avy_certificates(n) or []
                exp = min(c.get("notafter") for c in certs if c.get("notafter"))
                out["certs"][n] = (exp - time.time()) / 86400
            except Exception:
                pass
        try:
            out["log"] = pve.avy_cluster_log(200) or []
        except Exception:
            pass
        try:
            jobs = pve.avy_backup_jobs() or []
            out["jobs_disabled"] = bool(jobs) and not any(j.get("enabled") for j in jobs)
        except Exception:
            pass
        return out

    def _emb_hyperviseur(self, node, data):
        """Même gabarit que NodeChannel.build_node (R820) pour les champs communs
        (titre/description/Uptime/CPU/Charge/RAM/Swap/Disque //footer) : labels et
        ordre identiques — seuls les champs propres à Aveyron (PSI/Quorum/Tunnel WG/
        Dernières tâches) et ceux propres au R820 (Stockages/IPMI/RAID/SMART/PBS, qui
        n'existent pas sans Influx/telegraf côté Aveyron) diffèrent, par nécessité et
        pas par oubli. Harmonisation demandée par Nico 2026-07-18."""
        st = data["status"] or {}
        worst = 0.0
        ci = st.get("cpuinfo") or {}
        pv = (st.get("pveversion") or "").replace("pve-manager/", "").split("/")[0]
        emb = discord.Embed(title=f"🖥️ {node} — hyperviseur (Aveyron)")
        emb.timestamp = discord.utils.utcnow()
        emb.description = (f"🟢 `online`" + (f" · **{pv}**" if pv else "")
                           + (f" · {ci['model']}" if ci.get("model") else ""))
        emb.add_field(name="Uptime", value=fmt.humanize_duration(st.get("uptime")))

        cpu_pct = (st.get("cpu") or 0) * 100
        if ci.get("cpus"):
            emb.add_field(name="CPU", value=f"{cpu_pct:.0f} % · {ci.get('cpus', '?')} threads "
                                           f"({ci.get('sockets', '?')} sockets)")
        else:
            emb.add_field(name="CPU", value=f"{cpu_pct:.0f} %")
        la = st.get("loadavg") or []
        if la:
            try:
                l1, cpus = float(la[0]), float(ci.get("cpus") or 0)
            except (TypeError, ValueError):
                l1, cpus = None, 0
            suff = f" ({l1 / cpus * 100:.0f} % de {int(cpus)} threads)" if (l1 and cpus) else ""
            emb.add_field(name="Charge", value=" · ".join(str(x) for x in la[:3]) + suff)

        mem = st.get("memory") or {}
        if mem.get("total"):
            worst = max(worst, (mem.get("used") or 0) / mem["total"] * 100)
            emb.add_field(name="RAM", value=fmt.pct_of(mem.get("used"), mem.get("total")))
        sw = st.get("swap") or {}
        if sw.get("total"):
            emb.add_field(name="Swap", value=fmt.pct_of(sw.get("used") or 0, sw["total"]))
        rf = st.get("rootfs") or {}
        if rf.get("total"):
            worst = max(worst, (rf.get("used") or 0) / rf["total"] * 100)
            emb.add_field(name="Disque /", value=fmt.pct_of(rf.get("used") or 0, rf["total"]))

        kern = ((st.get("current-kernel") or {}).get("release")
                or (st.get("kversion") or "").split(" ")[1:2] or [""])
        kern = kern if isinstance(kern, str) else (kern[0] if kern else "")
        if kern:
            emb.add_field(name="Noyau", value=kern)
        last = data.get("rrd_last") or {}
        pc, pi = last.get("pressurecpusome"), last.get("pressureiosome")
        if pc is not None or pi is not None:
            emb.add_field(name="⚠️ Pression" if max(pc or 0, pi or 0) >= 5 else "Pression",
                          value=f"CPU {pc or 0:.0f} % · IO {pi or 0:.0f} %")
        cl = self._cluster or {}
        if cl.get("quorate") is not None:
            on = sum(1 for v in (cl.get("online") or {}).values() if v)
            tot = len(cl.get("online") or {}) or 3
            emb.add_field(name="Quorum",
                          value=f"{'🟢' if cl['quorate'] else '🔴'} {on}/{tot} nœuds")
            if not cl["quorate"]:
                worst = max(worst, 95)
        if cl.get("ping_ms") is not None:
            emb.add_field(name="Tunnel WG", value=f"{cl['ping_ms']:.0f} ms")
        cert_days = (cl.get("certs") or {}).get(node)
        if cert_days is not None:
            # jusqu'ici cette donnée n'était QUE seuil d'alerte (CERT_ALERT_DAYS) : la
            # rendre visible en continu, pas seulement quand ça tourne au rouge (demande
            # Nico 2026-07-18 : « plus d'informations internes, des détails »).
            flag = "🔴" if cert_days < CERT_ALERT_DAYS else "🟢"
            emb.add_field(name="🔐 Certificat TLS", value=f"{flag} expire dans {cert_days:.0f} j")
            if cert_days < CERT_ALERT_DAYS:
                worst = max(worst, 90)

        guests = self._node_guests(node)
        if guests:
            lines = [f"{fmt.status_emoji(i.get('status') == 'running')} "
                     f"**{n.removesuffix('-' + self.bot.cfg.avy_suffix)}** "
                     f"({(i.get('vmid') or 0) % 1_000_000})"
                     for n, i in guests]
            up = sum(1 for _, i in guests if i.get("status") == "running")
            emb.add_field(name=f"📦 VM/conteneurs — {up}/{len(guests)} up",
                          value="\n".join(lines)[:1024], inline=False)
        recents = [t for t in (data["tasks"] or []) if t.get("endtime")][:3]
        if recents:
            lines = []
            for t in recents:
                ok = str(t.get("status", "")) == "OK"
                when = datetime.datetime.fromtimestamp(t["endtime"]).strftime("%d/%m %H:%M")
                lines.append(f"{'✅' if ok else '⚠️'} `{t.get('type')}` "
                             f"{t.get('id') or ''} · {when}")
            emb.add_field(name="Dernières tâches", value="\n".join(lines)[:1024],
                          inline=False)
        emb.color = fmt.health_color(worst)
        emb.set_footer(text="rafraîchi · propriétaire uniquement")
        return emb

    def _emb_stockage(self, node, data):
        """Barre de remplissage + couleur d'alerte, comme /storage côté R820 (demande
        Nico 2026-07-18 : « plus d'informations internes, des détails »)."""
        emb = discord.Embed(title=f"💽 Stockages — {node} (Aveyron)")
        emb.timestamp = discord.utils.utcnow()
        worst = 0.0
        for s in sorted(data["storages"], key=lambda x: x.get("storage", "")):
            tot, used = s.get("total") or 0, s.get("used") or 0
            if not s.get("active"):
                val = "⚪ inactif"
            elif tot:
                pct = used / tot * 100
                worst = max(worst, pct)
                val = (f"{fmt.pct_bar(pct)}\n{fmt.pct_of(used, tot)}")
            else:
                val = "—"
            emb.add_field(name=f"{s.get('storage')} ({s.get('type')})", value=val, inline=True)
        emb.color = fmt.health_color(worst, warn=STO_CLEAR_PCT, crit=STO_ALERT_PCT)
        emb.set_footer(text="rafraîchi")
        return emb

    def _emb_sauvegardes(self, node, data, content_by_node):
        """Couleur d'alerte sur le pire cas (jamais sauvegardé / trop vieux / jobs
        désactivés), comme les autres embeds Aveyron (demande Nico 2026-07-18)."""
        emb = discord.Embed(title=f"💾 Sauvegardes — {node} (Aveyron)")
        emb.timestamp = discord.utils.utcnow()
        worst = 0.0
        vz = [t for t in (data["tasks"] or []) if t.get("type") == "vzdump"][:5]
        if vz:
            lines = []
            for t in vz:
                ok = str(t.get("status", "")) == "OK"
                end = t.get("endtime")
                when = (datetime.datetime.fromtimestamp(end).strftime("%d/%m %H:%M")
                        if end else "en cours")
                lines.append(f"{'✅' if ok else ('⏳' if not end else '⚠️')} "
                             f"vmid {t.get('id') or 'tous'} · {when}")
            emb.add_field(name="Dernières tâches vzdump", value="\n".join(lines)[:1024],
                          inline=False)
        else:
            emb.add_field(name="Dernières tâches vzdump", value="aucune", inline=False)
        items = content_by_node.get(node) or []
        if items:
            latest = max((i.get("ctime") or 0) for i in items)
            emb.add_field(
                name=f"Archives sur nas-backup — {len(items)}",
                value=(f"dernière : "
                       f"{datetime.datetime.fromtimestamp(latest).strftime('%d/%m %H:%M')} · "
                       f"total {fmt.humanize_bytes(sum(i.get('size') or 0 for i in items))}"),
                inline=False)
        else:
            emb.add_field(name="Archives sur nas-backup", value="aucune", inline=False)
        # âge de la dernière sauvegarde PAR invité (⚠️ au-delà de BACKUP_STALE_DAYS)
        by_vmid = {}
        for it in items:
            v = str(it.get("vmid"))
            by_vmid[v] = max(by_vmid.get(v, 0), it.get("ctime") or 0)
        now = time.time()
        lines = []
        sfx = "-" + self.bot.cfg.avy_suffix
        for n, i in self._node_guests(node):
            short = n.removesuffix(sfx)
            last = by_vmid.get(str(self.bot.pve.display_vmid(i.get("vmid") or 0)), 0)
            if not last:
                lines.append(f"⚠️ **{short}** — jamais sauvegardé")
                worst = max(worst, 90)
                continue
            days = (now - last) / 86400
            flag = "⚠️" if days > BACKUP_STALE_DAYS else "🟢"
            if days > BACKUP_STALE_DAYS:
                worst = max(worst, 85)
            when = (f"il y a {days:.0f} j" if days >= 1
                    else f"il y a {(now - last) / 3600:.0f} h")
            lines.append(f"{flag} **{short}** — {when}")
        if lines:
            emb.add_field(name="Par VM/conteneur", value="\n".join(lines)[:1024], inline=False)
        if (self._cluster or {}).get("jobs_disabled"):
            emb.add_field(
                name="⚠️ Jobs planifiés du cluster",
                value=("Les jobs vzdump configurés côté Proxmox sont **désactivés** : "
                       "aucune sauvegarde automatique ne tourne (constat, le bot n'y "
                       "touche pas)."),
                inline=False)
            worst = max(worst, 85)
        emb.color = fmt.health_color(worst)
        emb.set_footer(text="rafraîchi")
        return emb

    def _emb_materiel(self, node, data):
        """Disques physiques du nœud : modèle, santé SMART, usure, température.
        Résumé en tête + couleur d'alerte, comme /smart et /raid côté R820 (demande
        Nico 2026-07-18 : « plus d'informations internes, des détails »)."""
        emb = discord.Embed(title=f"🔩 Matériel — {node} (Aveyron)")
        emb.timestamp = discord.utils.utcnow()
        disks = data.get("disks") or []
        worst = 0.0
        bad = [d for d in disks if str(d.get("health") or "?").upper() not in ("PASSED", "OK")]
        hot = [d for d in disks if d.get("temp") is not None and d["temp"] >= DISK_TEMP_ALERT]
        worn = [d for d in disks if str(d.get("wearout") or "").isdigit()
                and int(d["wearout"]) <= WEAROUT_ALERT]
        if disks:
            emb.description = (f"❌ **{len(bad)} disque(s) en échec**" if bad
                               else f"✅ {len(disks)} disque(s) PASS")
            if hot:
                emb.description += f" · 🔥 {len(hot)} en surchauffe"
            if worn:
                emb.description += f" · ⚠️ {len(worn)} usé(s)"
        else:
            emb.description = "aucun disque physique remonté"
        if bad or hot:
            worst = 95
        elif worn:
            worst = 85
        for d in disks:
            health = str(d.get("health") or "?")
            ok = health.upper() in ("PASSED", "OK")
            bits = [("🟢" if ok else "🔴") + f" {health}"]
            if d.get("wearout") is not None and str(d["wearout"]).isdigit():
                w = int(d["wearout"])
                bits.append(("⚠️ " if w <= WEAROUT_ALERT else "") + f"usure {100 - w} % "
                            f"({w} % de vie restante)")
            if d.get("temp") is not None:
                bits.append(("🔥 " if d["temp"] >= DISK_TEMP_ALERT else "") + f"{d['temp']} °C")
            if d.get("size"):
                bits.append(fmt.humanize_bytes(d["size"]))
            emb.add_field(name=f"{d.get('devpath')} · {(d.get('model') or '?')[:40]}",
                          value=" · ".join(bits), inline=False)
        emb.color = fmt.health_color(worst)
        emb.set_footer(text="rafraîchi")
        return emb

    def _emb_alertes(self, node):
        """Embed épinglé du salon #alertes-<nœud> : sans lui, un salon événementiel
        vide ressemble à un salon cassé (retour Nico 2026-07-17)."""
        emb = discord.Embed(
            title=f"🚨 Alertes — {node} (Aveyron)",
            description=("Salon **événementiel** : les alertes du serveur apparaissent "
                         "ici dès qu'elles surviennent.\n\n"
                         "• 🔴 nœud injoignable (≥ 2 cycles) / 🟢 rétabli\n"
                         f"• 🔴 stockage ≥ {STO_ALERT_PCT} % (réarmé < {STO_CLEAR_PCT} %)\n"
                         "• 🔴 VM/conteneur `running` → `stopped` / 🟢 redémarré\n"
                         "• 🔴 sauvegarde vzdump en échec\n"
                         "• 🔴 santé SMART anormale / usure ≤ "
                         f"{WEAROUT_ALERT} % / température ≥ {DISK_TEMP_ALERT} °C\n"
                         "• 🔴 (cluster) quorum perdu / nœud vu hors ligne par ses pairs\n"
                         f"• 🔴 (cluster) certificat TLS expirant sous {CERT_ALERT_DAYS} j\n"
                         f"• 🔴 (cluster) ≥ {AUTH_FAIL_MIN} échecs d'authentification "
                         "PVE sur un cycle\n"
                         f"• 🔴 (cluster) tunnel WG dégradé (≥ {LAT_ALERT_MS} ms)"
                         + (f"\n• 🔴 (assistant IA) service arrêté / injoignable\n"
                            f"• 🔴 (assistant IA) GPU ≥ {GPU_TEMP_ALERT} °C\n"
                            f"• 🔴 (assistant IA) VRAM ou disque quasi pleins"
                            if node == self.bot.cfg.avy_llm_node else "")),
            color=fmt.BLURPLE)
        emb.set_footer(text="surveillance toutes les 5 min")
        return emb

    def _emb_down(self, node):
        # même gabarit que NodeChannel.build_node (R820) : titre/description/footer
        # identiques dans leur forme, seul le nom du nœud change (harmonisation
        # demandée par Nico 2026-07-18 : « chaque message de pve n'est pas les mêmes »)
        emb = discord.Embed(title=f"🖥️ {node} — hyperviseur (Aveyron)",
                            description="🔴 **API Proxmox injoignable** — état du nœud inconnu.",
                            color=fmt.RED)
        emb.timestamp = discord.utils.utcnow()
        emb.set_footer(text="rafraîchi · propriétaire uniquement")
        return emb

    def _content_by_node(self):
        """Contenu nas-backup réparti par nœud (une lecture CIFS ~16 s par cycle)."""
        try:
            items = self.bot.pve.avy_pbs_content() or []
        except Exception:
            return {}
        gm = self.bot.pve.guest_map()
        vmid_node = {str((i.get("vmid") or 0) % 1_000_000): i.get("node")
                     for n, i in gm.items() if self.bot.pve.is_avy_name(n)}
        out = {}
        for it in items:
            node = vmid_node.get(str(it.get("vmid")))
            if node:
                out.setdefault(node, []).append(it)
        return out

    # ------------------------------------------------------------------ alertes

    async def _alerts(self, node, data, state):
        """Alertes edge-triggered d'un nœud. `state` = sous-dict persistant du nœud."""
        s = state.setdefault(node, {"down": 0, "down_alerted": False,
                                    "sto": {}, "guests": {}, "seen_err": 0})
        if data is None:                                   # nœud injoignable
            s["down"] += 1
            if s["down"] == 2 and not s["down_alerted"]:
                s["down_alerted"] = True
                await self._send_alert(node, f"🔴 **{node}** (Aveyron) : nœud injoignable")
            return
        if s.pop("down_alerted", False) and s.get("down", 0) >= 2:
            await self._send_alert(node, f"🟢 **{node}** (Aveyron) : nœud rétabli")
        s["down"] = 0
        s["down_alerted"] = False

        for st in data["storages"]:
            name, tot = st.get("storage"), st.get("total") or 0
            if not name or not tot or not st.get("active"):
                continue
            pct = (st.get("used") or 0) / tot * 100
            was = s["sto"].get(name, False)
            if pct >= STO_ALERT_PCT and not was:
                s["sto"][name] = True
                await self._send_alert(
                    node, f"🔴 **{node}** : stockage `{name}` à **{pct:.0f} %**")
            elif pct < STO_CLEAR_PCT and was:
                s["sto"][name] = False
                await self._send_alert(
                    node, f"🟢 **{node}** : stockage `{name}` redescendu ({pct:.0f} %)")

        cur = {n: (i.get("status") or "?") for n, i in self._node_guests(node)}
        prev = s.get("guests") or {}
        sfx = "-" + self.bot.cfg.avy_suffix
        for n, status in cur.items():
            old = prev.get(n)
            if old is None:            # 1er passage : on apprend sans alerter
                continue
            if old == "running" and status != "running":
                await self._send_alert(node, f"🔴 **{n.removesuffix(sfx)}** ({node}) "
                                             f"est passé `{old}` → `{status}`")
            elif old != "running" and status == "running":
                await self._send_alert(node, f"🟢 **{n.removesuffix(sfx)}** ({node}) "
                                             f"est de nouveau `running`")
        s["guests"] = cur

        last = s.get("seen_err", 0)
        newest = last
        for t in data["tasks"] or []:
            if (t.get("type") == "vzdump" and t.get("endtime")
                    and str(t.get("status", "")) != "OK" and t["endtime"] > last):
                newest = max(newest, t["endtime"])
                await self._send_alert(
                    node, f"🔴 **{node}** : sauvegarde vzdump (vmid {t.get('id') or '?'}) "
                          f"en **échec** : `{str(t.get('status'))[:120]}`")
        s["seen_err"] = newest

        # disques physiques : santé SMART, usure, température (edge + hystérésis temp)
        dk = s.setdefault("disks", {})
        for d in data.get("disks") or []:
            dev = str(d.get("devpath") or "?")
            ds = dk.setdefault(dev, {})
            healthy = str(d.get("health") or "").upper() in ("PASSED", "OK", "UNKNOWN", "?")
            if not healthy and not ds.get("health"):
                ds["health"] = True
                await self._send_alert(
                    node, f"🔴 **{node}** : disque `{dev}` santé SMART **{d.get('health')}**")
            elif healthy and ds.get("health"):
                ds["health"] = False
                await self._send_alert(node, f"🟢 **{node}** : disque `{dev}` santé rétablie")
            w = d.get("wearout")
            if w is not None and str(w).isdigit() and int(w) <= WEAROUT_ALERT \
                    and not ds.get("wear"):
                ds["wear"] = True
                await self._send_alert(
                    node, f"🔴 **{node}** : disque `{dev}` usé à {100 - int(w)} % "
                          f"(**{w} %** de vie restante)")
            t = d.get("temp")
            if t is not None:
                if t >= DISK_TEMP_ALERT and not ds.get("temp"):
                    ds["temp"] = True
                    await self._send_alert(
                        node, f"🔴 **{node}** : disque `{dev}` à **{t} °C**")
                elif t < DISK_TEMP_CLEAR and ds.get("temp"):
                    ds["temp"] = False
                    await self._send_alert(
                        node, f"🟢 **{node}** : disque `{dev}` redescendu à {t} °C")

    async def _cluster_alerts(self, cl, state):
        """Alertes de niveau CLUSTER (quorum, nœuds vus par leurs pairs, certificats,
        échecs d'auth sur l'UI PVE, latence tunnel) — routées vers le #alertes du
        premier nœud (pas de salon global Aveyron)."""
        first = self.bot.pve.avy_nodes()[0] if self.bot.pve.avy_nodes() else None
        if first is None:
            return
        s = state.setdefault("_cluster", {})
        if not cl.get("ok"):
            return                                      # API muette : géré par nœud

        q, prev_q = cl.get("quorate"), s.get("quorate")
        if prev_q is not None and q is not None and q != prev_q:
            await self._send_alert(first, "🔴 **cluster Aveyron : QUORUM PERDU**" if not q
                                   else "🟢 **cluster Aveyron : quorum rétabli**")
        if q is not None:
            s["quorate"] = q

        off = s.setdefault("offline", {})
        for n, online in (cl.get("online") or {}).items():
            was_off = off.get(n)
            if was_off is None:
                off[n] = not online
                continue
            if not online and not was_off:
                off[n] = True
                await self._send_alert(first, f"🔴 **{n}** : vu HORS LIGNE par ses pairs")
            elif online and was_off:
                off[n] = False
                await self._send_alert(first, f"🟢 **{n}** : de retour dans le cluster")

        certs = s.setdefault("certs", {})
        for n, days in (cl.get("certs") or {}).items():
            if days < CERT_ALERT_DAYS and not certs.get(n):
                certs[n] = True
                await self._send_alert(
                    first, f"🔴 **{n}** : certificat TLS expire dans **{days:.0f} j**")
            elif days > CERT_CLEAR_DAYS and certs.get(n):
                certs[n] = False

        # échecs d'authentification sur l'UI/API PVE (détection brute-force)
        fails = [e for e in cl.get("log") or []
                 if "authentication failure" in str(e.get("msg", "")).lower()]
        last_ts = s.get("auth_ts")
        if last_ts is None:                              # 1er passage : apprendre
            s["auth_ts"] = max((e.get("time") or 0 for e in cl.get("log") or []),
                               default=0)
        else:
            new = [e for e in fails if (e.get("time") or 0) > last_ts]
            if len(new) >= AUTH_FAIL_MIN:
                users = {str(e.get("user") or "?") for e in new}
                await self._send_alert(
                    first, f"🔴 **cluster Aveyron** : **{len(new)}** échecs "
                           f"d'authentification PVE ce cycle ({', '.join(sorted(users)[:4])})")
            if new:
                s["auth_ts"] = max(e.get("time") or 0 for e in new)

        ms = cl.get("ping_ms")
        if ms is not None:
            if ms >= LAT_ALERT_MS and not s.get("lat"):
                s["lat"] = True
                await self._send_alert(
                    first, f"🔴 **tunnel WG Aveyron dégradé** : API à **{ms:.0f} ms**")
            elif ms < LAT_CLEAR_MS and s.get("lat"):
                s["lat"] = False
                await self._send_alert(
                    first, f"🟢 **tunnel WG Aveyron rétabli** ({ms:.0f} ms)")

    # ------------------------------------------------------- /health, rapport

    async def health_rows(self):
        """(level, name, detail) par nœud Aveyron + 1 ligne cluster, pour /health et
        le rapport quotidien. Lecture LÉGÈRE (pas d'agent/disques/tâches) — le cache
        `self._cluster` (5 min) évite de repayer quorum/latence/certs à chaque appel."""
        if not self.enabled:
            return []
        rows = []
        cl = self._cluster or {}
        if cl.get("ping_ms") is not None:
            on = sum(1 for v in (cl.get("online") or {}).values() if v)
            tot = len(cl.get("online") or {}) or len(self.bot.pve.avy_nodes())
            q = cl.get("quorate")
            lvl = "crit" if q is False else ("warn" if cl["ping_ms"] >= LAT_ALERT_MS else "ok")
            rows.append((lvl, "Aveyron (cluster)",
                        f"quorum {'🟢' if q else '🔴' if q is False else '➖'} {on}/{tot} · "
                        f"tunnel {cl['ping_ms']:.0f} ms"))
        else:
            rows.append(("na", "Aveyron (cluster)", "API injoignable"))
        for node in self.bot.pve.avy_nodes():
            try:
                st = await asyncio.to_thread(self.bot.pve.avy_node_status, node)
            except Exception:
                rows.append(("crit", f"Aveyron — {node}", "injoignable"))
                continue
            cpu = (st.get("cpu") or 0) * 100
            mem, maxmem = (st.get("memory") or {}).get("used", 0), (st.get("memory") or {}).get("total", 0)
            rampct = mem / maxmem * 100 if maxmem else 0
            lvl = "crit" if (cpu >= 95 or rampct >= 95) else ("warn" if (cpu >= 85 or rampct >= 90) else "ok")
            rows.append((lvl, f"Aveyron — {node}", f"CPU {cpu:.0f} % · RAM {rampct:.0f} %"))
        return rows

    # -------------------------------------------------------- assistant IA locale

    def _llm_channel_id(self):
        p = self.bot.get_cog("Provision")
        prov = p.prov if p is not None else (self.bot.state.get("prov", {}) or {})
        return prov.get("avy_llm_channel")

    def _emb_ia_locale(self, mon):
        emb = discord.Embed(title="🤖 Assistant IA locale — ubuntu-llm (Aveyron)",
                            color=fmt.GREEN)
        emb.timestamp = discord.utils.utcnow()

        m = mon.get("model") or {}
        if m:
            params_b = (m.get("n_params") or 0) / 1e9
            emb.add_field(
                name="Modèle",
                value=(f"{self.bot.cfg.avy_llm_model} · {params_b:.1f} Md paramètres\n"
                       f"contexte {int((m.get('n_ctx') or 0) / 1024)}k · "
                       f"{fmt.humanize_bytes(m.get('size_bytes') or 0)} sur disque"),
                inline=False)

        gpu = mon.get("gpu") or {}
        if gpu.get("mem_total"):
            vram_pct = (gpu.get("mem_used") or 0) / gpu["mem_total"] * 100
            temp = gpu.get("temp")
            tflag = "🔥 " if (temp or 0) >= GPU_TEMP_ALERT else ""
            emb.add_field(
                name="GPU — RTX 3090",
                value=(f"{tflag}{temp:.0f} °C · util {gpu.get('util', 0) * 100:.0f} % · "
                       f"{fmt.humanize_bytes(gpu.get('mem_used') or 0)} / "
                       f"{fmt.humanize_bytes(gpu['mem_total'])} VRAM ({vram_pct:.0f} %)\n"
                       f"{gpu.get('power', 0):.0f} W · ventilo {gpu.get('fan', 0) * 100:.0f} %"),
                inline=False)

        svc = mon.get("services") or {}
        lines = []
        for name in LLM_CORE_SERVICES:
            st = svc.get(name, "?")
            lines.append(f"{'🟢' if st == 'active' else '🔴'} {name} ({st})")
        small = svc.get("llama-server-small", "?")
        lines.append(f"{'🟢' if small == 'active' else '⚪'} llama-server-small "
                     f"({small}{' — secondaire, normalement arrêté' if small != 'active' else ''})")
        emb.add_field(name="Services", value="\n".join(lines), inline=False)

        checks = [("llama.cpp", mon.get("llama_health")), ("LiteLLM", mon.get("litellm_alive")),
                  ("llm-router", mon.get("router_health"))]
        emb.add_field(
            name="Santé (endpoints)",
            value=" · ".join(f"{'✅' if v else '❌'} {n}" for n, v in checks),
            inline=True)

        disk = mon.get("disk") or {}
        if disk.get("total"):
            emb.add_field(
                name="Disque (modèles)",
                value=f"{fmt.humanize_bytes(disk['used'])} / {fmt.humanize_bytes(disk['total'])} "
                      f"· {fmt.humanize_bytes(disk['free'])} libres",
                inline=True)

        if mon.get("load1") is not None:
            emb.add_field(name="Charge VM", value=f"{mon['load1']:.2f}", inline=True)

        down = [n for n in LLM_CORE_SERVICES if svc.get(n) != "active"]
        if down or not all(v for _, v in checks):
            emb.color = fmt.RED
        elif (gpu.get("temp") or 0) >= GPU_TEMP_ALERT or (disk.get("free") or 0) < LLM_DISK_FREE_ALERT:
            emb.color = fmt.YELLOW
        emb.set_footer(text="rafraîchi toutes les 5 min")
        return emb

    def _emb_ia_locale_down(self):
        emb = discord.Embed(title="🤖 Assistant IA locale — ubuntu-llm (Aveyron)",
                            description="🔴 **injoignable** (VM éteinte ou guest-agent muet)",
                            color=fmt.RED)
        emb.timestamp = discord.utils.utcnow()
        emb.set_footer(text="rafraîchi toutes les 5 min")
        return emb

    async def refresh_llm(self):
        cid = self._llm_channel_id()
        if not cid:
            return None
        try:
            mon = await asyncio.to_thread(self.bot.pve.llm_monitor)
        except LlmExecError:
            await self._pin_edit(cid, self._emb_ia_locale_down())   # attendu : VM/cluster injoignable
            return None
        except Exception:
            log.exception("supervision IA locale")
            await self._pin_edit(cid, self._emb_ia_locale_down())
            return None
        await self._pin_edit(cid, self._emb_ia_locale(mon))
        return mon

    async def _llm_alerts(self, mon, state):
        node = self.bot.cfg.avy_llm_node
        s = state.setdefault("_llm", {})
        if mon is None:
            if not s.get("down"):
                s["down"] = True
                await self._send_alert(node, "🔴 **assistant IA locale** : VM ou "
                                             "services injoignables")
            return
        if s.pop("down", False):
            await self._send_alert(node, "🟢 **assistant IA locale** : de nouveau joignable")

        svc = mon.get("services") or {}
        down_now = {n for n in LLM_CORE_SERVICES if svc.get(n) != "active"}
        prev_down = set(s.get("svc_down") or [])
        for n in down_now - prev_down:
            await self._send_alert(node, f"🔴 **assistant IA locale** : service "
                                         f"`{n}` arrêté")
        for n in prev_down - down_now:
            await self._send_alert(node, f"🟢 **assistant IA locale** : service "
                                         f"`{n}` de nouveau actif")
        s["svc_down"] = list(down_now)

        gpu = mon.get("gpu") or {}
        temp = gpu.get("temp")
        if temp is not None:
            if temp >= GPU_TEMP_ALERT and not s.get("temp"):
                s["temp"] = True
                await self._send_alert(node, f"🔴 **GPU RTX 3090** à **{temp:.0f} °C**")
            elif temp < GPU_TEMP_CLEAR and s.get("temp"):
                s["temp"] = False
                await self._send_alert(node, f"🟢 **GPU RTX 3090** redescendue à {temp:.0f} °C")

        if gpu.get("mem_total"):
            free = gpu["mem_total"] - (gpu.get("mem_used") or 0)
            if free < GPU_VRAM_FREE_ALERT and not s.get("vram"):
                s["vram"] = True
                await self._send_alert(
                    node, f"🔴 **VRAM RTX 3090** quasi pleine : {fmt.humanize_bytes(free)} libres")
            elif free > GPU_VRAM_FREE_CLEAR and s.get("vram"):
                s["vram"] = False
                await self._send_alert(node, "🟢 **VRAM RTX 3090** de nouveau disponible")

        disk = mon.get("disk") or {}
        if disk.get("free") is not None:
            if disk["free"] < LLM_DISK_FREE_ALERT and not s.get("disk"):
                s["disk"] = True
                await self._send_alert(
                    node, f"🔴 **disque ubuntu-llm** : {fmt.humanize_bytes(disk['free'])} "
                          f"libres seulement")
            elif disk["free"] > LLM_DISK_FREE_CLEAR and s.get("disk"):
                s["disk"] = False
                await self._send_alert(node, "🟢 **disque ubuntu-llm** : espace redevenu confortable")

    # ------------------------------------------------------------------ boucle

    async def refresh_node(self, node):
        """Reconstruit les 3 embeds d'un nœud (+ alertes). Renvoie data ou None."""
        try:
            data = await asyncio.to_thread(self._collect, node)
        except Exception:
            data = None
        chans = self._sup().get(node, {})
        await self._pin_edit(chans.get("alertes"), self._emb_alertes(node))
        if data is None:
            await self._pin_edit(chans.get("hyperviseur"), self._emb_down(node),
                                 AvyNodeView(self))
            return None
        content = getattr(self, "_cycle_content", {})
        await self._pin_edit(chans.get("hyperviseur"),
                             self._emb_hyperviseur(node, data), AvyNodeView(self))
        await self._pin_edit(chans.get("stockage"), self._emb_stockage(node, data))
        await self._pin_edit(chans.get("sauvegardes"),
                             self._emb_sauvegardes(node, data, content))
        await self._pin_edit(chans.get("materiel"), self._emb_materiel(node, data))
        return data

    @tasks.loop(minutes=5)
    async def refresh(self):
        if not self.bot.pve.avy_enabled or not self._sup():
            return
        state = dict(self.bot.state.get("avy_alerts", {}) or {})
        # lectures de niveau cluster (quorum, latence, certs, journal) : une fois par
        # cycle, avant les nœuds — _emb_hyperviseur les lit via self._cluster
        self._cluster = await asyncio.to_thread(self._cluster_collect)
        # une seule énumération CIFS (lente) par cycle, partagée entre les nœuds
        self._cycle_content = await asyncio.to_thread(self._content_by_node)
        for node in self.bot.pve.avy_nodes():
            try:
                data = await self.refresh_node(node)
                await self._alerts(node, data, state)
            except Exception:
                log.exception("supervision Aveyron: nœud %s", node)
        try:
            await self._cluster_alerts(self._cluster, state)
        except Exception:
            log.exception("supervision Aveyron: alertes cluster")
        if getattr(self.bot.pve, "llm_enabled", False):
            try:
                mon = await self.refresh_llm()
                await self._llm_alerts(mon, state)
            except Exception:
                log.exception("supervision Aveyron: assistant IA locale")
        self.bot.state.set("avy_alerts", state)

    @refresh.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


class AvyNodeView(discord.ui.View):
    """Boutons persistants de l'embed #hyperviseur d'un nœud Aveyron : Rafraîchir +
    💾 Backup (vzdump all=1 du nœud vers nas-backup). Gardés par les rôles M/O du
    serveur (AVY-<nœud>) + session 2FA — cf. admin_button_ok(server=)."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="avy:refresh")
    async def b_refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        node, key = self.cog.node_of_channel(itx.channel_id)
        if node is None:
            await itx.response.send_message("Salon non reconnu.", ephemeral=True)
            return
        if not await admin_button_ok(itx, server=key):
            return
        await itx.response.defer()
        await self.cog.refresh_node(node)

    @discord.ui.button(label="Graph", emoji="📈",
                       style=discord.ButtonStyle.secondary, custom_id="avy:graph")
    async def b_graph(self, itx: discord.Interaction, button: discord.ui.Button):
        node, key = self.cog.node_of_channel(itx.channel_id)
        if node is None:
            await itx.response.send_message("Salon non reconnu.", ephemeral=True)
            return
        if not await admin_button_ok(itx, server=key):
            return
        await itx.response.defer(ephemeral=True)
        cog = self.cog.bot.get_cog("Graphs")
        if cog is None:
            await itx.followup.send("Graphes indisponibles.", ephemeral=True)
            return
        try:
            emb, file = await cog.quick_file(f"avy:{node}")
        except Exception as e:
            await itx.followup.send(f"❌ Graphe impossible : `{e}`", ephemeral=True)
            return
        if emb is None:
            await itx.followup.send("Aucune donnée sur 24 h.", ephemeral=True)
            return
        await itx.followup.send(embed=emb, file=file, ephemeral=True)

    @discord.ui.button(label="Sauvegarder", emoji="💾",
                       style=discord.ButtonStyle.secondary, custom_id="avy:backup")
    async def b_backup(self, itx: discord.Interaction, button: discord.ui.Button):
        node, key = self.cog.node_of_channel(itx.channel_id)
        if node is None:
            await itx.response.send_message("Salon non reconnu.", ephemeral=True)
            return
        if not await admin_button_ok(itx, server=key):
            return
        bot = self.cog.bot
        if not bot.pve.actions_enabled:
            await itx.response.send_message("Token d'action non configuré.", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True)
        cv = ConfirmView(itx.user.id)
        emb = discord.Embed(
            title="⚠️ Confirmation",
            description=f"Sauvegarder **toutes les VM/conteneurs du nœud {node}** (Aveyron) "
                        f"vers `nas-backup` ?",
            color=fmt.YELLOW)
        cv.message = await itx.followup.send(embed=emb, view=cv, ephemeral=True, wait=True)
        await cv.wait()
        if not cv.value:
            await itx.followup.send("Annulé.", ephemeral=True)
            return
        who = f"{itx.user}({itx.user.id})"
        try:
            upid = await asyncio.to_thread(bot.pve.avy_backup_node, node)
        except Exception as e:
            bot.audit.record(user=who, action="backup", target=f"avy-noeud/{node}",
                             result=f"error:{e}")
            await itx.followup.send(f"❌ Échec : `{e}`", ephemeral=True)
            return
        bot.audit.record(user=who, action="backup", target=f"avy-noeud/{node}",
                         result="submitted", upid=str(upid))
        await itx.followup.send(
            f"💾 Sauvegarde du nœud **{node}** lancée (suivi dans #sauvegardes).",
            ephemeral=True)


async def setup(bot):
    await bot.add_cog(Avy(bot))
