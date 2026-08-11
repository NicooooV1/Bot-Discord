"""Rendu des embeds épinglés de la supervision Aveyron.

Module FRÈRE de `cogs/avy.py`, PAS un cog : il n'a pas de `setup()` et ne doit jamais
entrer dans la liste COGS de `bot/__main__.py`.

Extrait de `cogs/avy.py` le 2026-08-11 : les constructeurs d'embed y pesaient ~330 lignes
sur 1224 et lisaient l'état du cog par-dessous (`self._cluster`, `self._cycle_guests` via
`_node_guests`). Ici ce sont des fonctions PURES : tout ce qu'elles affichent arrive en
argument, donc ce qu'un embed montre se lit dans sa signature. Le cog garde ce qui bouge
(collecte, alertes à verrou, boucle).

⚠️ LES SEUILS VIVENT ICI, pas dans avy.py, et le cog les réimporte. Ils servent aux DEUX
côtés : au rendu (couleur des embeds, et surtout le sommaire de #alertes qui les
DOCUMENTE à l'utilisateur) et aux alertes edge-triggered du cog. Les laisser dans avy.py
et les importer d'ici ferait un cycle d'imports ; les dupliquer laisserait un salon
annoncer « ≥ 85 % » pendant que l'alerte partirait à 90 %.

⚠️ Ces fonctions sont appelées depuis la boucle d'événements : elles ne font AUCUN accès
réseau (c'est le rôle de `Avy._collect` / `_cluster_collect`, exécutés via to_thread).
"""
import datetime
import time

import discord

from ..core import format as fmt
from ..core.pve import Pve

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


def hyperviseur(node, data, cluster, guests, sfx):
    """Même gabarit que NodeChannel.build_node (R820) pour les champs communs
    (titre/description/Uptime/CPU/Charge/RAM/Swap/Disque //footer) : labels et
    ordre identiques — seuls les champs propres à Aveyron (PSI/Quorum/Tunnel WG/
    Dernières tâches) et ceux propres au R820 (Stockages/IPMI/RAID/SMART/PBS, qui
    n'existent pas sans Influx/telegraf côté Aveyron) diffèrent, par nécessité et
    pas par oubli. Harmonisation demandée par Nico 2026-07-18.

    `cluster` = instantané `Avy._cluster_collect` du cycle, `guests` = [(nom, info)]
    des invités du nœud (cf. `Avy._node_guests`, vide si la carte est illisible),
    `sfx` = suffixe complet des noms Aveyron (« -avy »)."""
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
    cl = cluster or {}
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

    if guests:
        lines = [f"{fmt.status_emoji(i.get('status') == 'running')} "
                 f"**{n.removesuffix(sfx)}** "
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


def stockage(node, data):
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


def sauvegardes(node, data, items, guests, sfx, cluster):
    """Couleur d'alerte sur le pire cas (jamais sauvegardé / trop vieux / jobs
    désactivés), comme les autres embeds Aveyron (demande Nico 2026-07-18).

    `items` = archives nas-backup DE CE NŒUD (cf. `Avy._content_by_node`)."""
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
    items = items or []
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
    for n, i in guests:
        short = n.removesuffix(sfx)
        last = by_vmid.get(str(Pve.display_vmid(i.get("vmid") or 0)), 0)
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
    if (cluster or {}).get("jobs_disabled"):
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


def materiel(node, data):
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


def alertes(node, llm_node):
    """Embed épinglé du salon #alertes-<nœud> : sans lui, un salon événementiel
    vide ressemble à un salon cassé (retour Nico 2026-07-17).

    `llm_node` = nœud qui héberge l'assistant IA (cfg.avy_llm_node) : lui seul liste
    les alertes GPU/VRAM/services."""
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
                        if node == llm_node else "")),
        color=fmt.BLURPLE)
    emb.set_footer(text="surveillance toutes les 5 min")
    return emb


def down(node):
    # même gabarit que NodeChannel.build_node (R820) : titre/description/footer
    # identiques dans leur forme, seul le nom du nœud change (harmonisation
    # demandée par Nico 2026-07-18 : « chaque message de pve n'est pas les mêmes »)
    emb = discord.Embed(title=f"🖥️ {node} — hyperviseur (Aveyron)",
                        description="🔴 **API Proxmox injoignable** — état du nœud inconnu.",
                        color=fmt.RED)
    emb.timestamp = discord.utils.utcnow()
    emb.set_footer(text="rafraîchi · propriétaire uniquement")
    return emb


def ia_locale(mon, model):
    """`mon` = instantané `Pve.llm_monitor`, `model` = cfg.avy_llm_model."""
    emb = discord.Embed(title="🤖 Assistant IA locale — ubuntu-llm (Aveyron)",
                        color=fmt.GREEN)
    emb.timestamp = discord.utils.utcnow()

    m = mon.get("model") or {}
    if m:
        params_b = (m.get("n_params") or 0) / 1e9
        emb.add_field(
            name="Modèle",
            value=(f"{model} · {params_b:.1f} Md paramètres\n"
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

    down_svc = [n for n in LLM_CORE_SERVICES if svc.get(n) != "active"]
    if down_svc or not all(v for _, v in checks):
        emb.color = fmt.RED
    elif (gpu.get("temp") or 0) >= GPU_TEMP_ALERT or (disk.get("free") or 0) < LLM_DISK_FREE_ALERT:
        emb.color = fmt.YELLOW
    emb.set_footer(text="rafraîchi toutes les 5 min")
    return emb


def ia_locale_down():
    emb = discord.Embed(title="🤖 Assistant IA locale — ubuntu-llm (Aveyron)",
                        description="🔴 **injoignable** (VM éteinte ou guest-agent muet)",
                        color=fmt.RED)
    emb.timestamp = discord.utils.utcnow()
    emb.set_footer(text="rafraîchi toutes les 5 min")
    return emb
