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
from ..core.gates import GatedView
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
# Discord refuse un embed de plus de 25 champs (400 Bad Request = embed JAMAIS publié) :
# les listes « un champ par disque / par stockage » ne sont pas bornées côté Proxmox, on
# garde une marge pour les champs fixes de l'embed. (2026-08-11)
MAX_LIST_FIELDS = 20

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


def _num(v, unit, scale=1, fmt_spec=".0f"):
    """Formate une métrique éventuellement ABSENTE : « 42 °C » ou « ? °C ».

    ⚠️ Le script de supervision embarqué dans la VM ubuntu-llm construit toujours toutes
    ses clés et y met None quand la métrique Prometheus manque : `dict.get(clé, 0)` ne
    rend PAS le défaut (la clé existe), d'où les `None * 100` / `f"{None:.0f}"` qui
    levaient un TypeError en plein rendu d'embed. On affiche « ? » et non 0 : une
    métrique absente ne doit pas se maquiller en valeur nulle réelle (2026-08-11)."""
    if v is None:
        return f"? {unit}"
    try:
        return f"{v * scale:{fmt_spec}} {unit}"
    except (TypeError, ValueError):
        return f"? {unit}"


class Avy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.enabled = getattr(bot.cfg, "avy_enabled", False)
        self._cluster = {}    # instantané cluster du cycle (quorum, latence, jobs)
        self._cycle_guests = None   # carte des invités du cycle (cf. _node_guests)

    async def cog_load(self):
        self.bot.add_view(AvyNodeView(self))
        if self.enabled:
            self._warn_missing_gestion()
            self.refresh.start()

    def _warn_missing_gestion(self):
        """Un nœud d'AVY_NODES sans ligne « AVY-X:G:M:O » dans GESTION_SERVERS rend ses
        boutons INUTILISABLES : is_admin est fail-closed sur une clé inconnue (sinon un
        porteur de « Gestion R820 » piloterait une machine d'Aveyron). Le dire au
        démarrage plutôt que de laisser un refus incompréhensible au premier clic
        (2026-08-11).

        On ne lit QUE la liste statique AVY_NODES : la découverte en ligne coûterait un
        appel réseau (donc un démarrage retardé si Aveyron est injoignable) pour un
        simple message de journal."""
        try:
            known = getattr(self.bot.cfg, "gestion_servers", {}) or {}
            nodes = getattr(self.bot.cfg, "avy_nodes", None) or []
            missing = [k for k in (self.bot.pve.avy_server_key(n) for n in nodes)
                       if k not in known]
        except Exception:
            log.warning("supervision Aveyron: vérification GESTION_SERVERS impossible",
                        exc_info=True)
            return
        if missing:
            log.warning("supervision Aveyron: %s absent(s) de GESTION_SERVERS — les "
                        "boutons des salons de ce(s) serveur(s) seront REFUSÉS (déclarer "
                        "« %s:G:M:O » dans config.env)",
                        ", ".join(missing), missing[0])

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
        """[(name, info)] des invités -avy de CE nœud (cf. avertissement d'en-tête).

        ⚠️ La carte est celle PRÉ-CHARGÉE par refresh_node (via aguest_map, donc hors
        boucle d'événements) : `guest_map()` est SYNCHRONE et, dès que son cache de 30 s
        a expiré, tape /cluster/resources — appelé d'ici (2 embeds + les alertes, tous
        dans une coroutine) il bloquerait tout le bot le temps de la requête. Pré-chargement
        en échec = liste vide, jamais de lecture réseau depuis la boucle d'événements ;
        les appelants savent se dégrader (2026-08-11)."""
        gm = self._cycle_guests
        if gm is None:
            return []
        return sorted(((n, i) for n, i in gm.items()
                       if self.bot.pve.is_avy_name(n) and i.get("node") == node),
                      key=lambda x: x[1].get("vmid") or 0)

    async def _send_alert(self, node, text) -> bool:
        """Envoie une alerte dans #alertes-<nœud>. Renvoie True SEULEMENT si le message
        est parti.

        ⚠️ Toutes les alertes de ce cog sont à verrou (edge-triggered) : armer le verrou
        sans savoir si l'envoi a réussi marque la condition « déjà annoncée » et elle
        n'est plus jamais réannoncée tant qu'elle ne s'est pas résorbée. Les appelants
        n'arment donc leur verrou que sur True. Et un échec durable (salon supprimé, bot
        privé de SEND_MESSAGES) doit laisser une trace : sans elle l'alerting Aveyron
        peut être mort sans que rien ne le dise nulle part (2026-08-11)."""
        cid = self._sup().get(node, {}).get("alertes")
        ch = self.bot.get_channel(cid) if cid else None
        if ch is None:
            log.warning("alerte Aveyron perdue (salon #alertes de %s introuvable, id=%s) : %s",
                        node, cid, text)
            return False
        try:
            await ch.send(text, allowed_mentions=discord.AllowedMentions.none())
            return True
        except discord.HTTPException:
            log.warning("alerte Aveyron non envoyée (%s) : %s", node, text, exc_info=True)
            return False

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
            # un salon dont les embeds ne se mettent plus à jour (permission retirée,
            # message supprimé pendant l'édition) ressemble à un salon à jour : le dire
            # au moins dans les logs (2026-08-11)
            log.warning("supervision Aveyron: message épinglé non mis à jour (salon %s)",
                        channel_id, exc_info=True)

    # ------------------------------------------------------------------ embeds

    def _collect(self, node):
        """Toutes les lectures API d'un nœud (synchrone, appelé via to_thread)."""
        pve = self.bot.pve
        out = {"status": None, "storages": [], "tasks": [],
               "rrd_last": {}, "disks": []}
        out["status"] = pve.avy_node_status(node)          # lève si nœud injoignable
        # ⚠️ une lecture ratée ici se lit comme « rien à signaler » dans les embeds ET
        # neutralise les alertes correspondantes (stockage plein, vzdump en échec) :
        # elle doit au moins laisser une trace (2026-08-11)
        try:
            out["storages"] = pve.avy_node_storages(node) or []
        except Exception:
            log.warning("supervision Aveyron: stockages du nœud %s illisibles", node,
                        exc_info=True)
        try:
            out["tasks"] = pve.avy_node_tasks(node, limit=20) or []
        except Exception:
            log.warning("supervision Aveyron: tâches du nœud %s illisibles", node,
                        exc_info=True)
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
        # ⚠️ Il y avait ici une lecture /cluster/resources filtrée par nœud (out["rows"]).
        # PERSONNE ne la lisait, mais elle coûtait un aller-retour R820 + un aller-retour
        # Aveyron PAR NŒUD ET PAR CYCLE (et un de plus à chaque clic sur 🔄), sur le lien
        # le plus fragile de l'infra. Supprimée le 2026-08-11 : les métriques internes des
        # invités passent par _node_guests(), servi par le cache 30 s de guest_map().
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
            # out["ok"] reste False -> _cluster_alerts sort sans rien faire : quorum perdu
            # et nœud hors ligne ne seraient plus signalés du tout, en silence (2026-08-11)
            log.warning("supervision Aveyron: statut du cluster illisible", exc_info=True)
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
        emb.description = ("🟢 `online`" + (f" · **{pv}**" if pv else "")
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
        stos = sorted(data["storages"], key=lambda x: x.get("storage", ""))
        for n, s in enumerate(stos):
            tot, used = s.get("total") or 0, s.get("used") or 0
            if not s.get("active"):
                val = "⚪ inactif"
            elif tot:
                pct = used / tot * 100
                worst = max(worst, pct)   # la couleur tient compte de TOUS les stockages
                val = (f"{fmt.pct_bar(pct)}\n{fmt.pct_of(used, tot)}")
            else:
                val = "—"
            # au-delà de MAX_LIST_FIELDS champs Discord rejette l'embed entier (400) :
            # mieux vaut une liste tronquée qu'un salon figé (2026-08-11)
            if n < MAX_LIST_FIELDS:
                emb.add_field(name=f"{s.get('storage')} ({s.get('type')})", value=val,
                              inline=True)
        if len(stos) > MAX_LIST_FIELDS:
            emb.add_field(name="…", value=f"+ {len(stos) - MAX_LIST_FIELDS} autre(s) "
                                          f"stockage(s) non affiché(s)", inline=False)
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
        # un champ par disque : un nœud à 24 baies ferait sauter la limite des 25 champs
        # d'un embed (400 Bad Request = embed jamais publié). Le résumé ci-dessus, lui,
        # compte TOUS les disques. (2026-08-11)
        for d in disks[:MAX_LIST_FIELDS]:
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
        if len(disks) > MAX_LIST_FIELDS:
            emb.add_field(name="…", value=f"+ {len(disks) - MAX_LIST_FIELDS} disque(s) "
                                          f"non affiché(s)", inline=False)
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
        """Contenu nas-backup réparti par nœud (une lecture CIFS ~16 s par cycle).

        ⚠️ guest_map() DOIT rester dans le try : c'était le seul appel non gardé du corps
        de refresh(), et il tape /cluster/resources sur le R820. Un jeton PVEAuditor
        révoqué (401 -> ResourceException, qui n'est PAS un OSError et ne déclenche donc
        pas le backoff de discord.py) faisait remonter l'exception jusqu'à la tasks.loop
        et TUAIT la supervision Aveyron pour de bon — plus un seul embed, plus une seule
        alerte, jusqu'au redémarrage du bot (2026-08-11)."""
        try:
            items = self.bot.pve.avy_pbs_content() or []
            gm = self.bot.pve.guest_map()
        except Exception:
            log.warning("supervision Aveyron: contenu nas-backup indisponible ce cycle",
                        exc_info=True)
            return {}
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
        """Alertes edge-triggered d'un nœud. `state` = sous-dict persistant du nœud.

        ⚠️ Règle de tout ce bloc (2026-08-11) : un verrou (ou un curseur) ne s'arme QUE si
        `_send_alert` a confirmé l'envoi. Armé d'avance, un message perdu (salon supprimé,
        bot privé de SEND_MESSAGES, 429) marquait la condition « déjà annoncée » et la
        panne n'était PLUS JAMAIS signalée tant qu'elle ne s'était pas d'abord résorbée."""
        s = state.setdefault(node, {"down": 0, "down_alerted": False,
                                    "sto": {}, "guests": {}})
        if data is None:                                   # nœud injoignable
            s["down"] += 1
            # `>= 2` et non `== 2` : si l'envoi échoue au 2e cycle, le compteur continue
            # de grimper et la condition ne se représenterait jamais.
            if s["down"] >= 2 and not s["down_alerted"]:
                if await self._send_alert(
                        node, f"🔴 **{node}** (Aveyron) : nœud injoignable"):
                    s["down_alerted"] = True
            return
        if s.get("down_alerted"):
            # on garde le verrou ET le compteur tant que le « rétabli » n'est pas parti
            if await self._send_alert(node, f"🟢 **{node}** (Aveyron) : nœud rétabli"):
                s["down_alerted"] = False
                s["down"] = 0
        else:
            s["down"] = 0

        for st in data["storages"]:
            name, tot = st.get("storage"), st.get("total") or 0
            if not name or not tot or not st.get("active"):
                continue
            pct = (st.get("used") or 0) / tot * 100
            was = s["sto"].get(name, False)
            if pct >= STO_ALERT_PCT and not was:
                if await self._send_alert(
                        node, f"🔴 **{node}** : stockage `{name}` à **{pct:.0f} %**"):
                    s["sto"][name] = True
            elif pct < STO_CLEAR_PCT and was:
                if await self._send_alert(
                        node, f"🟢 **{node}** : stockage `{name}` redescendu ({pct:.0f} %)"):
                    s["sto"][name] = False

        cur = {n: (i.get("status") or "?") for n, i in self._node_guests(node)}
        prev = s.get("guests") or {}
        sfx = "-" + self.bot.cfg.avy_suffix
        if not cur and prev:
            # carte des invités illisible ce cycle : ne PAS écraser le curseur, sinon la
            # transition running -> stopped survenue pendant la panne serait apprise comme
            # état initial et jamais annoncée.
            log.warning("supervision Aveyron: aucun invité lu pour %s, curseur conservé",
                        node)
        else:
            newg = {}
            for n, status in cur.items():
                old = prev.get(n)
                if old is None:            # 1er passage : on apprend sans alerter
                    newg[n] = status
                    continue
                ok = True
                if old == "running" and status != "running":
                    ok = await self._send_alert(
                        node, f"🔴 **{n.removesuffix(sfx)}** ({node}) "
                              f"est passé `{old}` → `{status}`")
                elif old != "running" and status == "running":
                    ok = await self._send_alert(
                        node, f"🟢 **{n.removesuffix(sfx)}** ({node}) "
                              f"est de nouveau `running`")
                # curseur figé sur l'ancien état si l'annonce n'est pas partie : elle
                # repartira au cycle suivant
                newg[n] = status if ok else old
            s["guests"] = newg

        # ⚠️ Pas de "seen_err": 0 par défaut : au tout premier passage (state.json vide,
        # nouveau nœud), TOUTE tâche vzdump non-OK du tampon PVE — jusqu'à 20 tâches
        # archivées, parfois vieilles de plusieurs jours — satisfait `endtime > 0` et
        # partait d'un coup dans #alertes. On apprend d'abord, et SEULEMENT sur une
        # lecture réussie : `tasks` vide peut aussi vouloir dire « lecture en échec »
        # (_collect avale l'exception), et armer à 0 relancerait la salve. (2026-08-11)
        if "seen_err" not in s:
            if data["tasks"]:
                s["seen_err"] = max((t.get("endtime") or 0) for t in data["tasks"])
        else:
            last = s.get("seen_err", 0)
            newest = last
            for t in data["tasks"] or []:
                st_txt = str(t.get("status") or "")
                # un statut VIDE n'est pas une preuve d'échec (tâche encore en cours de
                # publication côté PVE) : ne pas l'annoncer comme telle
                if (t.get("type") == "vzdump" and t.get("endtime")
                        and st_txt and st_txt != "OK" and t["endtime"] > last):
                    if await self._send_alert(
                            node,
                            f"🔴 **{node}** : sauvegarde vzdump (vmid {t.get('id') or '?'}) "
                            f"en **échec** : `{st_txt[:120]}`"):
                        # curseur avancé seulement sur envoi confirmé : un échec vzdump
                        # perdu n'apparaît dans AUCUN embed persistant, l'information
                        # disparaîtrait pour de bon
                        newest = max(newest, t["endtime"])
            s["seen_err"] = newest

        # disques physiques : santé SMART, usure, température (edge + hystérésis temp)
        dk = s.setdefault("disks", {})
        for d in data.get("disks") or []:
            dev = str(d.get("devpath") or "?")
            ds = dk.setdefault(dev, {})
            healthy = str(d.get("health") or "").upper() in ("PASSED", "OK", "UNKNOWN", "?")
            if not healthy and not ds.get("health"):
                if await self._send_alert(
                        node,
                        f"🔴 **{node}** : disque `{dev}` santé SMART **{d.get('health')}**"):
                    ds["health"] = True
            elif healthy and ds.get("health"):
                if await self._send_alert(
                        node, f"🟢 **{node}** : disque `{dev}` santé rétablie"):
                    ds["health"] = False
            w = d.get("wearout")
            if w is not None and str(w).isdigit() and int(w) <= WEAROUT_ALERT \
                    and not ds.get("wear"):
                if await self._send_alert(
                        node, f"🔴 **{node}** : disque `{dev}` usé à {100 - int(w)} % "
                              f"(**{w} %** de vie restante)"):
                    ds["wear"] = True
            t = d.get("temp")
            if t is not None:
                if t >= DISK_TEMP_ALERT and not ds.get("temp"):
                    if await self._send_alert(
                            node, f"🔴 **{node}** : disque `{dev}` à **{t} °C**"):
                        ds["temp"] = True
                elif t < DISK_TEMP_CLEAR and ds.get("temp"):
                    if await self._send_alert(
                            node, f"🟢 **{node}** : disque `{dev}` redescendu à {t} °C"):
                        ds["temp"] = False

    async def _cluster_alerts(self, cl, state):
        """Alertes de niveau CLUSTER (quorum, nœuds vus par leurs pairs, certificats,
        échecs d'auth sur l'UI PVE, latence tunnel) — routées vers le #alertes du
        premier nœud (pas de salon global Aveyron)."""
        # ⚠️ avy_nodes() est SYNCHRONE et peut retomber sur nodes_online() (réseau) quand
        # AVY_NODES n'est pas configuré : hors boucle d'événements, et une seule fois
        # (l'appel était fait deux fois) — 2026-08-11
        nodes = await asyncio.to_thread(self.bot.pve.avy_nodes)
        first = nodes[0] if nodes else None
        if first is None:
            return
        s = state.setdefault("_cluster", {})
        if not cl.get("ok"):
            return                                      # API muette : géré par nœud

        # même règle que _alerts : le curseur/verrou ne bouge que si l'annonce est partie
        q, prev_q = cl.get("quorate"), s.get("quorate")
        if prev_q is not None and q is not None and q != prev_q:
            if await self._send_alert(
                    first, "🔴 **cluster Aveyron : QUORUM PERDU**" if not q
                    else "🟢 **cluster Aveyron : quorum rétabli**"):
                s["quorate"] = q
        elif q is not None:
            s["quorate"] = q

        off = s.setdefault("offline", {})
        for n, online in (cl.get("online") or {}).items():
            was_off = off.get(n)
            if was_off is None:
                off[n] = not online
                continue
            if not online and not was_off:
                if await self._send_alert(
                        first, f"🔴 **{n}** : vu HORS LIGNE par ses pairs"):
                    off[n] = True
            elif online and was_off:
                if await self._send_alert(
                        first, f"🟢 **{n}** : de retour dans le cluster"):
                    off[n] = False

        certs = s.setdefault("certs", {})
        for n, days in (cl.get("certs") or {}).items():
            if days < CERT_ALERT_DAYS and not certs.get(n):
                if await self._send_alert(
                        first, f"🔴 **{n}** : certificat TLS expire dans **{days:.0f} j**"):
                    certs[n] = True
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
            sent = True
            if len(new) >= AUTH_FAIL_MIN:
                users = {str(e.get("user") or "?") for e in new}
                sent = await self._send_alert(
                    first, f"🔴 **cluster Aveyron** : **{len(new)}** échecs "
                           f"d'authentification PVE ce cycle ({', '.join(sorted(users)[:4])})")
            # curseur avancé seulement si rien n'était à annoncer ou si l'annonce est
            # partie : sinon une salve de brute-force passerait à la trappe
            if new and sent:
                s["auth_ts"] = max(e.get("time") or 0 for e in new)

        ms = cl.get("ping_ms")
        if ms is not None:
            if ms >= LAT_ALERT_MS and not s.get("lat"):
                if await self._send_alert(
                        first, f"🔴 **tunnel WG Aveyron dégradé** : API à **{ms:.0f} ms**"):
                    s["lat"] = True
            elif ms < LAT_CLEAR_MS and s.get("lat"):
                if await self._send_alert(
                        first, f"🟢 **tunnel WG Aveyron rétabli** ({ms:.0f} ms)"):
                    s["lat"] = False

    # ------------------------------------------------------- /health, rapport

    async def health_rows(self):
        """(level, name, detail) par nœud Aveyron + 1 ligne cluster, pour /health et
        le rapport quotidien. Lecture LÉGÈRE (pas d'agent/disques/tâches) — le cache
        `self._cluster` (5 min) évite de repayer quorum/latence/certs à chaque appel."""
        if not self.enabled:
            return []
        rows = []
        cl = self._cluster or {}
        # avy_nodes() est synchrone (et peut faire un appel réseau si AVY_NODES est vide) :
        # une seule lecture, hors boucle d'événements (2026-08-11)
        nodes = await asyncio.to_thread(self.bot.pve.avy_nodes)
        if cl.get("ping_ms") is not None:
            on = sum(1 for v in (cl.get("online") or {}).values() if v)
            tot = len(cl.get("online") or {}) or len(nodes)
            q = cl.get("quorate")
            lvl = "crit" if q is False else ("warn" if cl["ping_ms"] >= LAT_ALERT_MS else "ok")
            rows.append((lvl, "Aveyron (cluster)",
                        f"quorum {'🟢' if q else '🔴' if q is False else '➖'} {on}/{tot} · "
                        f"tunnel {cl['ping_ms']:.0f} ms"))
        else:
            rows.append(("na", "Aveyron (cluster)", "API injoignable"))
        for node in nodes:
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
            # ⚠️ cf. _num : ces six clés existent TOUJOURS mais valent None quand la
            # métrique Prometheus manque (2026-08-11)
            emb.add_field(
                name="GPU — RTX 3090",
                value=(f"{tflag}{_num(temp, '°C')} · util {_num(gpu.get('util'), '%', 100)} · "
                       f"{fmt.humanize_bytes(gpu.get('mem_used') or 0)} / "
                       f"{fmt.humanize_bytes(gpu['mem_total'])} VRAM ({vram_pct:.0f} %)\n"
                       f"{_num(gpu.get('power'), 'W')} · "
                       f"ventilo {_num(gpu.get('fan'), '%', 100)}"),
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
        # ⚠️ Le rendu de l'embed ne doit pas emporter les ALERTES avec lui : une métrique
        # inattendue (clé à None, format surprise) faisait remonter l'exception jusqu'au
        # try du cycle et sautait _llm_alerts — service arrêté, GPU brûlant ou disque
        # plein n'étaient plus signalés du tout (2026-08-11).
        try:
            await self._pin_edit(cid, self._emb_ia_locale(mon))
        except Exception:
            log.exception("supervision IA locale: rendu de l'embed #ia-locale")
        return mon

    async def _llm_alerts(self, mon, state):
        # même règle que _alerts : verrou/curseur armés seulement sur envoi confirmé
        node = self.bot.cfg.avy_llm_node
        s = state.setdefault("_llm", {})
        if mon is None:
            if not s.get("down"):
                if await self._send_alert(node, "🔴 **assistant IA locale** : VM ou "
                                                "services injoignables"):
                    s["down"] = True
            return
        if s.get("down"):
            if await self._send_alert(
                    node, "🟢 **assistant IA locale** : de nouveau joignable"):
                s["down"] = False

        svc = mon.get("services") or {}
        down_now = {n for n in LLM_CORE_SERVICES if svc.get(n) != "active"}
        prev_down = set(s.get("svc_down") or [])
        announced = set(prev_down)      # état réellement annoncé dans Discord
        for n in down_now - prev_down:
            if await self._send_alert(node, f"🔴 **assistant IA locale** : service "
                                            f"`{n}` arrêté"):
                announced.add(n)
        for n in prev_down - down_now:
            if await self._send_alert(node, f"🟢 **assistant IA locale** : service "
                                            f"`{n}` de nouveau actif"):
                announced.discard(n)
        s["svc_down"] = sorted(announced)

        gpu = mon.get("gpu") or {}
        temp = gpu.get("temp")
        if temp is not None:
            if temp >= GPU_TEMP_ALERT and not s.get("temp"):
                if await self._send_alert(
                        node, f"🔴 **GPU RTX 3090** à **{temp:.0f} °C**"):
                    s["temp"] = True
            elif temp < GPU_TEMP_CLEAR and s.get("temp"):
                if await self._send_alert(
                        node, f"🟢 **GPU RTX 3090** redescendue à {temp:.0f} °C"):
                    s["temp"] = False

        if gpu.get("mem_total"):
            free = gpu["mem_total"] - (gpu.get("mem_used") or 0)
            if free < GPU_VRAM_FREE_ALERT and not s.get("vram"):
                if await self._send_alert(
                        node, f"🔴 **VRAM RTX 3090** quasi pleine : "
                              f"{fmt.humanize_bytes(free)} libres"):
                    s["vram"] = True
            elif free > GPU_VRAM_FREE_CLEAR and s.get("vram"):
                if await self._send_alert(
                        node, "🟢 **VRAM RTX 3090** de nouveau disponible"):
                    s["vram"] = False

        disk = mon.get("disk") or {}
        if disk.get("free") is not None:
            if disk["free"] < LLM_DISK_FREE_ALERT and not s.get("disk"):
                if await self._send_alert(
                        node, f"🔴 **disque ubuntu-llm** : "
                              f"{fmt.humanize_bytes(disk['free'])} libres seulement"):
                    s["disk"] = True
            elif disk["free"] > LLM_DISK_FREE_CLEAR and s.get("disk"):
                if await self._send_alert(
                        node, "🟢 **disque ubuntu-llm** : espace redevenu confortable"):
                    s["disk"] = False

    # ------------------------------------------------------------------ boucle

    async def refresh_node(self, node):
        """Reconstruit les 3 embeds d'un nœud (+ alertes). Renvoie data ou None."""
        # La carte des invités est lue UNE fois, hors boucle d'événements : _node_guests()
        # (2 embeds + les alertes) passait sinon par guest_map() SYNCHRONE, qui tape
        # /cluster/resources dès que son cache de 30 s a expiré et bloque tout le bot le
        # temps de la requête (2026-08-11).
        try:
            self._cycle_guests = await self.bot.pve.aguest_map()
        except Exception:
            log.warning("supervision Aveyron: carte des invités indisponible (%s)",
                        node, exc_info=True)
            self._cycle_guests = None
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
        # (_content_by_node ne lève plus : toutes ses lectures sont gardées)
        self._cycle_content = await asyncio.to_thread(self._content_by_node)
        # avy_nodes() est synchrone : hors boucle d'événements comme le reste
        for node in await asyncio.to_thread(self.bot.pve.avy_nodes):
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


class AvyNodeView(GatedView):
    """Boutons persistants de l'embed #hyperviseur d'un nœud Aveyron : Rafraîchir +
    📈 Graph + 💾 Backup (vzdump all=1 du nœud vers nas-backup).

    Porte : tier "mod" borné au SERVEUR DU SALON — chaque nœud d'Aveyron est un serveur
    à part entière (rôles G/M/O propres), donc la clé dépend de l'interaction et se
    résout dans `resolve_server`, pas dans un `gate_server` figé. Le 2FA de session est
    appliqué par GatedView (les clics de boutons ne passent pas par GatedTree).
    Migré de `admin_button_ok(server=)` vers GatedView le 2026-08-11 : même tier, même
    clé, mais la porte ne peut plus être oubliée sur un bouton ajouté plus tard."""

    gate = "mod"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def interaction_check(self, interaction) -> bool:
        """Salon inconnu = REFUS AVANT la porte.

        ⚠️ `resolve_server` renvoie None quand `node_of_channel` ne reconnaît pas le salon
        (prov['avy_sup'] vide au démarrage, salon recréé à la main, message épinglé
        survivant à une reprovision). Or None ne veut pas dire « clé inconnue » pour
        `is_admin` : il veut dire « serveur PRIMAIRE », donc repli sur le rôle
        « Gestion R820 » — exactement le repli silencieux que la migration devait fermer
        (le fail-closed d'is_admin ne s'applique qu'à une clé PRÉSENTE et inconnue).
        On tranche donc ici, avant `super()`, ce qui n'ouvre rien : cela ne fait que
        refuser plus tôt, avec le même message que les handlers (2026-08-11)."""
        try:
            node, key = self.cog.node_of_channel(interaction.channel_id)
        except Exception:  # noqa: BLE001 — une erreur d'évaluation = refus, jamais accès
            log.exception("AvyNodeView: salon %s non résolu — refus",
                          interaction.channel_id)
            node = key = None
        if node is None or not key:
            try:
                await interaction.response.send_message("Salon non reconnu.",
                                                        ephemeral=True)
            except discord.HTTPException:
                pass
            return False
        return await super().interaction_check(interaction)

    async def resolve_server(self, interaction):
        """Clé GESTION_SERVERS du nœud auquel appartient le salon cliqué (« AVY-PVE »…).
        Jamais None quand la porte s'exécute : `interaction_check` a déjà refusé les
        salons non reconnus."""
        _node, key = self.cog.node_of_channel(interaction.channel_id)
        return key

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="avy:refresh")
    async def b_refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        node, _key = self.cog.node_of_channel(itx.channel_id)  # porte : resolve_server
        if node is None:
            await itx.response.send_message("Salon non reconnu.", ephemeral=True)
            return
        await itx.response.defer()
        await self.cog.refresh_node(node)

    @discord.ui.button(label="Graph", emoji="📈",
                       style=discord.ButtonStyle.secondary, custom_id="avy:graph")
    async def b_graph(self, itx: discord.Interaction, button: discord.ui.Button):
        node, _key = self.cog.node_of_channel(itx.channel_id)  # porte : resolve_server
        if node is None:
            await itx.response.send_message("Salon non reconnu.", ephemeral=True)
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
        node, _key = self.cog.node_of_channel(itx.channel_id)  # porte : resolve_server
        if node is None:
            await itx.response.send_message("Salon non reconnu.", ephemeral=True)
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
