"""Constructeurs d'embeds thématiques du provisioning (extraits de provision.py).

POURQUOI CE MODULE
------------------
`provision.py` pesait 2081 lignes — 10 % du projet. Ces onze constructeurs n'ont rien
à voir avec l'orchestration des salons : ce sont des fonctions quasi PURES
« données InfluxDB -> discord.Embed », sans état, sans permission, sans appel Discord.
Les sortir rend provision.py lisible et ces rendus testables sans instancier le cog.
Ce n'est PAS un cog : rien à ajouter à la liste COGS de `__main__.py`.

CONTRAT COMMUN — c'est lui qui autorise les tables BUILDERS / SYNO_BUILDERS en fin de
fichier :

    async def emb_xxx(bot, h=None) -> (discord.Embed, pending)

  - `bot` est passé EXPLICITEMENT (influx, state, cfg, get_cog) : plus de `self`, donc
    aucune dépendance au cog Provision ;
  - `h` = relevé SNMP du NAS mutualisé sur le cycle (cf. `nas_health`). Les
    constructeurs qui n'en ont pas besoin l'IGNORENT : la signature est commune pour
    que l'appelant n'ait pas à distinguer les cas (c'était un `if key == "nas"` dans
    `refresh_topical`) ;
  - `pending` = baselines de delta à persister UNIQUEMENT après une écriture confirmée :
    un refresh raté ne doit pas consommer la variation du cycle (#21). C'est l'APPELANT
    qui les écrit dans le state, jamais ces fonctions.
"""
import discord

from ..core import format as fmt


def _pdelta(cur, prev, unit="", dec=0):
    """Delta d'évolution depuis le dernier refresh (flèche + valeur)."""
    if prev is None:
        return ""
    d = cur - prev
    if round(d, dec) == 0:
        return " (=)"
    return f" (▲ +{d:.{dec}f}{unit})" if d > 0 else f" (▼ {d:.{dec}f}{unit})"


async def emb_materiel(bot, h=None):
    inf = bot.influx
    pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)
    emb = discord.Embed(title="🔧 Matériel", color=fmt.BLURPLE)
    emb.timestamp = discord.utils.utcnow()
    power = await inf.ipmi_power()
    if power:
        emb.add_field(name="Alimentation (W)",
                      value="\n".join(f"{n}: {(v or 0):.0f}" for n, v in power)[:1024] or "—")
    pc = await inf.power_cost()
    if pc:
        try:
            emb.add_field(
                name="💶 Coût électrique",
                value=(f"{float(pc.get('watts', 0)):.0f} W · "
                       f"**{float(pc.get('eur_day', 0)):.2f} €/jour** · "
                       f"~{float(pc.get('eur_month', 0)):.0f} €/mois "
                       f"({float(pc.get('kwh_day', 0)):.1f} kWh/j @ "
                       f"{float(pc.get('tariff_eur_kwh', 0)):.3f} €/kWh)"),
                inline=False)
        except (TypeError, ValueError):
            pass
    fans = await inf.ipmi_fans()
    if fans:
        emb.add_field(name="Ventilateurs (RPM)",
                      value="\n".join(f"{n}: {int(v or 0)}" for n, v in fans[:12])[:1024] or "—")
    temps = [(n, v) for n, v in (await inf.ipmi_temps() or []) if v is not None]
    if temps:
        mx = max(v for _, v in temps)
        emb.add_field(name="Températures (°C)",
                      value="\n".join(f"{n}: {v:.0f}" for n, v in temps[:12])[:1024])
        if mx >= 40:
            emb.color = fmt.RED if mx >= 60 else fmt.YELLOW
        _p = bot.state.get("prov_prev_mat_temp")
        emb.add_field(name="Δ temp max (depuis refresh)",
                      value=f"{mx:.0f}°C{_pdelta(mx, _p, '°C')} · seuils ⚠️≥40 / 🔥≥60",
                      inline=False)
        pending["prov_prev_mat_temp"] = mx
    ctrl, summ = await inf.raid()
    if summ:
        disks = await inf.raid_disks()
        gd = max((int(d.get("grown_defects", 0) or 0) for d in disks), default=0)
        emb.add_field(
            name="RAID",
            value=(f"{int(summ.get('disks_online', 0))}/{int(summ.get('disks_total', 0))} online"
                   + (" · optimal ✅" if (ctrl or {}).get("vd_optimal") else " · ⚠️ dégradé")
                   + f" · grown max {gd}"))
        if ctrl and not ctrl.get("vd_optimal"):
            emb.color = fmt.RED
    health = await inf.smart_health()
    if health:
        bad = [d for d, h in health if not bool(h)]
        emb.add_field(name="SMART",
                      value=(f"{len(health) - len(bad)}/{len(health)} sains"
                             + (f" · ❌ {bad}" if bad else " ✅"))[:1024])
        if bad:
            emb.color = fmt.RED
    emb.set_footer(text="rafraîchi")
    return emb, pending


async def emb_sauvegardes(bot, h=None):
    inf = bot.influx
    pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)
    emb = discord.Embed(title="🛟 Sauvegardes (PBS)", color=fmt.GREEN)
    emb.timestamp = discord.utils.utcnow()
    summ = await inf.backup_summary()
    ages = await inf.backup_ages() or []
    if summ:
        nob = int(summ.get("guests_without_backup", 0))
        emb.add_field(name="Plus ancienne",
                      value=fmt.humanize_duration(summ.get("oldest_age_seconds")))
        emb.add_field(name="Sans sauvegarde",
                      value=(f"{nob} / {len(ages)} guests" if ages else str(nob)))
        if nob:
            emb.color = fmt.YELLOW
        _p = bot.state.get("prov_prev_nob")
        emb.add_field(name="Δ sans-backup (depuis refresh)",
                      value=f"{nob}{_pdelta(nob, _p)}", inline=False)
        pending["prov_prev_nob"] = nob
        # volume réel des sauvegardes sur le NAS (logique vs dédupliqué)
        logical = float(summ.get("total_logical_bytes", 0) or 0)
        real = float(summ.get("real_used_bytes", 0) or 0)
        dedup = float(summ.get("dedup_factor", 0) or 0)
        snaps = int(summ.get("snapshots_total", 0) or 0)
        if logical:
            emb.add_field(
                name="📦 Volume sauvegardé (sur le NAS)",
                value=(f"{fmt.humanize_bytes(logical)} logique → **{fmt.humanize_bytes(real)}** "
                       f"réel sur disque · dédup **×{dedup:.1f}** · {snaps} snapshots"),
                inline=False)
    lines = []
    for r in ages:
        age = r.get("age_seconds")
        name = r.get("name") or r.get("vmid")
        sz = r.get("size_bytes")
        szs = f" · {fmt.humanize_bytes(sz)}" if sz else ""
        if age is None or age < 0 or not r.get("has_backup"):
            lines.append(f"❌ **{name}** — aucune")
            emb.color = fmt.RED
        else:
            flag = "⚠️" if age > 108000 else "✅"
            lines.append(f"{flag} **{name}** — {fmt.humanize_duration(age)}{szs}")
    if lines:
        emb.add_field(name="Par guest", value="\n".join(lines)[:1024], inline=False)
    emb.set_footer(text="rafraîchi")
    return emb, pending


async def emb_stockage(bot, h=None):
    inf = bot.influx
    pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)
    emb = discord.Embed(title="🗄️ Stockage", color=fmt.BLURPLE)
    emb.timestamp = discord.utils.utcnow()
    tp = await inf.thinpool()
    if tp and tp.get("pool_bytes"):
        poolb = tp.get("pool_bytes")
        du = tp.get("data_used_bytes")
        oc = tp.get("overcommit_percent")
        # USAGE RÉEL (données écrites / pool) = le VRAI risque : un thinpool plein
        # corrompt les volumes. C'est LUI qui doit rester < 100 % et pilote la couleur.
        dp = tp.get("data_percent")
        if dp is None:
            dp = (du / poolb * 100) if (du and poolb) else 0.0
        dp = float(dp)
        emb.add_field(name=f"Thinpool local-lvm — {dp:.0f}% utilisé",
                      value=fmt.pct_of(du, poolb, decimals=1))
        if dp >= 90:
            emb.color = fmt.RED
        elif dp >= 85:
            emb.color = fmt.YELLOW
        # surallocation = INFO (dépasse 100 % en thin provisioning, c'est normal)
        if oc is not None:
            alloc = tp.get("allocated_bytes")
            oc_ctx = (f" ({fmt.humanize_bytes(alloc)} alloués / {fmt.humanize_bytes(poolb)} pool)"
                      if alloc is not None else "")
            emb.add_field(name="Surprovisionnement (info)", value=f"{oc:.0f} %{oc_ctx}")
    st = await inf.storages()
    if st:
        lines = [f"{'⚠️' if s.get('pct', 0) >= 85 else '•'} {s['name']} — "
                 f"{fmt.pct_of(s.get('used', 0), s.get('total', 0))}"
                 for s in st[:12]]
        emb.add_field(name="Storages Proxmox", value="\n".join(lines)[:1024], inline=False)
        if any(s.get("pct", 0) >= 90 for s in st):
            emb.color = fmt.RED
        mxp = max((s.get("pct", 0) for s in st), default=0)
        _p = bot.state.get("prov_prev_sto_pct")
        emb.add_field(name="Δ remplissage max (depuis refresh)",
                      value=f"{mxp:.0f}%{_pdelta(mxp, _p, '%')}", inline=False)
        pending["prov_prev_sto_pct"] = mxp
    # prévision de saturation (projection linéaire ~7 j) pour les disques qui grossissent
    fc_lines = []
    for path in ("/mnt/media", "/"):
        try:
            fc = await inf.disk_forecast(path)
        except Exception:
            fc = None
        if fc and fc.get("days") is not None:
            d = fc["days"]
            icon = "🔴" if d < 30 else ("🟠" if d < 90 else "🟢")
            when = "moins d'1 jour" if d < 1 else f"~{d:.0f} jours"
            # tailles réelles en plus du % (demande Nico 2026-07-20), ex. « 3,3 To / 3,8 To »
            ub, tb = fc.get("used_bytes"), fc.get("total_bytes")
            sizes = f" ({fmt.humanize_bytes(ub)} / {fmt.humanize_bytes(tb)})" if (ub and tb) else ""
            fc_lines.append(f"{icon} `{path}` {fc['current']:.0f}%{sizes} → plein dans **{when}**")
            if d < 30:
                emb.color = fmt.RED
    if fc_lines:
        emb.add_field(name="📈 Prévision de saturation",
                      value="\n".join(fc_lines), inline=False)
    emb.set_footer(text="rafraîchi")
    return emb, pending


async def emb_seedbox(bot, h=None):
    """Seedbox : ratio, upload, VPN/PF, sync, et top torrents uploaders."""
    inf = bot.influx
    b = bot.cfg.influx_bucket
    pending = {}          # baselines de delta à persister APRÈS écriture réussie (#21)

    def piv(meas, keys):
        rk = ", ".join(f'"{k}"' for k in keys)
        return (f'from(bucket:"{b}") |> range(start:-6m) '
                f'|> filter(fn:(r)=> r._measurement=="{meas}") |> last() '
                f'|> pivot(rowKey:[{rk}], columnKey:["_field"], valueColumn:"_value")')

    qbr = await inf.aq(piv("qbittorrent", ["host"]))
    vpnr = await inf.aq(piv("servarr_vpn", ["host", "country"]))
    syncr = await inf.aq(piv("servarr_sync", ["host"]))
    qb = qbr[0] if qbr else {}
    vpn = vpnr[0] if vpnr else {}
    sync = syncr[0] if syncr else {}

    # Stats C411 = EXACTEMENT celles de #ratio : même source de vérité, partagée via
    # Servarr.c411_snapshot() (relève officielle tracker > estimation /setratio+qBit >
    # saisie manuelle) — jamais les chiffres locaux qBittorrent (ratio de session,
    # upload/download cumulés du client) qui en divergent.
    srv = bot.get_cog("Servarr")
    snap = await srv.c411_snapshot() if srv else None
    if snap:
        ratio, up_to, dl_go = snap["ratio"], snap["up_to"], snap["dl_go"]
        bonus, src = snap["bonus_go"], snap["source"]
    else:   # repli si le cog Servarr n'est pas chargé
        c411 = bot.state.get("c411") or {}
        ratio = float(c411.get("ratio", 0) or 0) or float(qb.get("ratio", 0) or 0)
        up_to = float(c411.get("up_to", 0) or 0)
        dl_go = float(c411.get("dl_go", 0) or 0)
        bonus = float(c411.get("bonus_go", 0) or 0)
        src = "manuel"
    color = fmt.GREEN if ratio >= 1 else (fmt.YELLOW if ratio >= 0.8 else fmt.RED)
    emb = discord.Embed(title="🧲 Seedbox — ratio & torrents", color=color)
    emb.timestamp = discord.utils.utcnow()
    if not qb:
        emb.description = "Aucune donnée (collecteur `servarr-metrics` muet ?)."
        emb.color = fmt.RED
        emb.set_footer(text="rafraîchi")
        return emb, pending

    emb.add_field(name=f"Ratio C411 ({src})", value=f"**{ratio:.2f}**")
    b_label = "crédit" if src == "officiel" else "bonus"
    up_bonus = f"\n(dont {bonus:.1f} Go {b_label})" if bonus >= 0.1 else ""
    emb.add_field(name="⬆️ Upload C411", value=f"{up_to:.3f} To{up_bonus}")
    emb.add_field(name="⬇️ Download C411", value=f"{dl_go:.1f} Go")
    emb.add_field(name="Débit ↑ / ↓",
                  value=f"{fmt.humanize_rate(qb.get('up_speed',0) or 0)} / "
                        f"{fmt.humanize_rate(qb.get('dl_speed',0) or 0)}")
    emb.add_field(name="Torrents",
                  value=f"{int(qb.get('seeding',0) or 0)} seed / {int(qb.get('torrents_total',0) or 0)}")
    emb.add_field(name="Pairs", value=str(int(qb.get("peers", 0) or 0)))
    ul = int(qb.get("alltime_ul", 0) or 0)
    _p = bot.state.get("prov_prev_seed_ul")
    d = ul - int(_p) if _p is not None else 0
    emb.add_field(name="📈 Uploadé depuis le refresh",
                  value=(f"▲ {fmt.humanize_bytes(d)}" if d > 0 else "—"), inline=False)
    pending["prov_prev_seed_ul"] = ul

    if vpn:
        up = int(vpn.get("up", 0) or 0)
        pf = int(vpn.get("pf_ok", 0) or 0)
        extra = (f" · 🔁 sync {fmt.status_emoji(int(sync.get('usersync_up',0) or 0))}"
                 if sync else "")
        emb.add_field(
            name="VPN & sync", inline=False,
            value=f"{fmt.status_emoji(up)} {vpn.get('country','?')} "
                  f"(`{vpn.get('egress','?')}`) · Port-forward "
                  f"{'✅' if pf else '❌'} :{int(vpn.get('listen_port',0) or 0)}{extra}")

    top_flux = (
        f'from(bucket:"{b}") |> range(start:-8m) '
        f'|> filter(fn:(r)=> r._measurement=="qbit_torrent" '
        f'and (r._field=="uploaded" or r._field=="upspeed")) |> last() '
        f'|> pivot(rowKey:["name"], columnKey:["_field"], valueColumn:"_value") '
        f'|> group() |> sort(columns:["uploaded"], desc:true) |> limit(n:10)')
    tops = await inf.aq(top_flux)
    if tops:
        lines = []
        for i, t in enumerate(tops, 1):
            up = t.get("uploaded", 0) or 0
            sp = t.get("upspeed", 0) or 0
            nm = str(t.get("name", "?"))[:46]
            arrow = f" ↑{fmt.humanize_rate(sp)}" if sp > 0 else ""
            lines.append(f"`{i:>2}.` **{fmt.humanize_bytes(up)}**{arrow} · {nm}")
        emb.add_field(name="🏆 Top uploaders", value="\n".join(lines)[:1024], inline=False)
    emb.set_footer(text="rafraîchi")
    return emb, pending


async def emb_nas(bot, h=None):
    """NAS Synology DS216se : capacité (via l'API PVE) + santé SNMP (si activé).

    `h` = relevé nas_health() déjà en main (refresh_topical le partage entre les
    5 embeds du cycle) ; None = on le récupère nous-mêmes (bouton Rafraîchir)."""
    pending = {}
    inf = bot.influx
    cap = await inf.nas_capacity()
    h = await nas_health(bot, h)
    sysd = (h or {}).get("system") or {}
    color = fmt.GREEN
    emb = discord.Embed(title="🗄️ NAS Synology — DS216se", color=color)
    emb.timestamp = discord.utils.utcnow()
    # --- capacité (toujours dispo, lue côté hyperviseur) ---
    if cap:
        total = float(cap.get("total", 0) or 0)
        used = float(cap.get("used", 0) or 0)
        pct = (used / total * 100) if total else 0
        emb.add_field(
            name="Capacité (volume de sauvegarde)",
            value=f"{fmt.pct_bar(pct)}\n{fmt.pct_of(used, total, decimals=1)}",
            inline=False)
        if pct >= 90:
            color = fmt.RED
        elif pct >= 75:
            color = fmt.YELLOW
    else:
        emb.add_field(name="Capacité", value="— (collecteur muet ?)", inline=False)
    # --- santé (SNMP) ---
    if sysd:
        # _syno_i partout (2026-08-11) : ces conversions étaient les SEULES du cog à
        # ne pas passer par le helper — un champ SNMP textuel levait un ValueError
        # qui, avant l'isolation par salon de refresh_topical, emportait aussi
        # #reseau et #services.
        t = _syno_i(sysd, "system_temp_c")
        st = _syno_i(sysd, "system_status")
        fan = _syno_i(sysd, "system_fan_status")
        emb.add_field(name="Système",
                      value=f"{'🟢' if st == 1 else '🔴'} état · {t}°C · "
                            f"ventilo {'🟢' if fan == 1 else '🔴'}")
        dlines = []
        for d in ((h or {}).get("disks") or []):
            ds = _syno_i(d, "status")
            dlines.append(f"{'🟢' if ds == 1 else '🔴'} {d.get('disk_id', '?')} — {d.get('temp_c', '?')}°C")
            if ds != 1:
                color = fmt.RED
        if dlines:
            emb.add_field(name="Disques", value="\n".join(dlines)[:1024], inline=False)
        bad = 0
        for r in ((h or {}).get("smart") or []):
            bad += _syno_i(r, "raw")
        if bad:
            emb.add_field(name="⚠️ Secteurs défectueux (SMART)",
                          value=f"**{bad}** — `sda` à surveiller / remplacer", inline=False)
            color = fmt.RED
        for rd in ((h or {}).get("raid") or []):
            rs = _syno_i(rd, "status")
            emb.add_field(name="RAID",
                          value=f"{'🟢 Normal' if rs == 1 else ('🟠 Dégradé' if rs == 11 else '🔴 Critique')}"
                                f" ({rd.get('raid_name', '?')})")
            if rs != 1:
                color = fmt.RED
    else:
        emb.add_field(
            name="Santé disques / SMART / RAID / température",
            value="⚠️ Indisponible — **active SNMP** sur le NAS (DSM → Panneau de config → "
                  "Terminal & SNMP → cocher SNMP, communauté `homelab`). Ça se remplira tout seul.",
            inline=False)
    emb.color = color
    emb.set_footer(text="capacité via PVE · santé via SNMP · rafraîchi")
    return emb, pending


# ------------------------------------------------ NAS Synology (serveur SYNO)
# Toutes les valeurs viennent des mesures synology* d'InfluxDB, alimentées par
# l'input SNMP de Telegraf sur l'hyperviseur. Le bot ne parle jamais au NAS
# directement, et n'a aucun moyen d'agir dessus : supervision en lecture seule.
async def nas_health(bot, h=None):
    """Relevé SNMP du NAS, mutualisé sur un cycle (2026-08-11).

    `influx.nas_health()` enchaîne 4 requêtes Flux et n'a AUCUN cache ; les
    5 embeds du NAS (#nas + les 4 salons SYNO) l'appelaient chacun le leur, soit
    20 requêtes identiques par cycle de 2 min. `refresh_topical` en fait UN et le
    passe aux builders — ça borne aussi la cohérence : les 5 embeds d'un même cycle
    décrivent le même instantané."""
    return h if h is not None else await bot.influx.nas_health()


def _syno_i(d, key, default=0):
    """Champ SNMP -> entier (Influx renvoie des flottants, parfois des chaînes)."""
    try:
        return int(float((d or {}).get(key, default) or default))
    except (TypeError, ValueError):
        return default


def _syno_muet():
    """Message unique quand SNMP ne répond plus, pour ne pas laisser un salon vide
    sans explication (cause la plus fréquente : SNMP décoché dans DSM)."""
    emb = discord.Embed(
        title="🗄️ NAS Synology — pas de données",
        description="Aucune métrique SNMP depuis plus de 15 minutes.\n"
                    "À vérifier : DSM → Panneau de configuration → **Terminal & SNMP** "
                    "(SNMP v2c coché, communauté `homelab`), et que le NAS est allumé.",
        color=fmt.GREY)
    emb.timestamp = discord.utils.utcnow()
    return emb, {}


async def emb_syno_sante(bot, h=None):
    h = await nas_health(bot, h)
    sysd = (h or {}).get("system") or {}
    if not sysd:
        return _syno_muet()
    st = _syno_i(sysd, "system_status")
    power = _syno_i(sysd, "power_status")
    sfan = _syno_i(sysd, "system_fan_status")
    cfan = _syno_i(sysd, "cpu_fan_status")
    temp = _syno_i(sysd, "system_temp_c")
    ok = lambda v: "🟢" if v == 1 else "🔴"
    color = fmt.GREEN if all(v == 1 for v in (st, power, sfan, cfan)) else fmt.RED
    if color == fmt.GREEN and temp >= 50:
        color = fmt.YELLOW

    emb = discord.Embed(
        title=f"🗄️ {sysd.get('model') or 'Synology'} — santé",
        color=color)
    emb.timestamp = discord.utils.utcnow()
    emb.add_field(name="État", value=f"{ok(st)} système\n{ok(power)} alimentation")
    emb.add_field(name="Refroidissement",
                  value=f"{ok(sfan)} ventilateur\n{ok(cfan)} ventilateur CPU")
    emb.add_field(name="Température", value=f"**{temp} °C**")

    up = _syno_i(sysd, "uptime")          # Timeticks = centièmes de seconde
    load = _syno_i(sysd, "load1_x100") / 100.0
    idle = _syno_i(sysd, "cpu_idle_pct", 100)
    emb.add_field(name="Charge",
                  value=f"CPU {max(0, 100 - idle)} % · charge {load:.2f}")
    total_kb = _syno_i(sysd, "mem_total_kb")
    if total_kb:
        libre_kb = (_syno_i(sysd, "mem_avail_kb")
                    + _syno_i(sysd, "mem_buffer_kb")
                    + _syno_i(sysd, "mem_cached_kb"))
        used = max(0, total_kb - libre_kb) * 1024
        emb.add_field(name="Mémoire",
                      value=fmt.pct_of(used, total_kb * 1024, decimals=1))
    if up:
        emb.add_field(name="Allumé depuis", value=fmt.humanize_duration(up / 100))

    maj = _syno_i(sysd, "upgrade_available")
    if maj == 1:
        emb.add_field(name="Mise à jour DSM",
                      value="🟠 Une mise à jour est disponible", inline=False)
    # dsm_version contient déjà « DSM 7.1-… » : ne pas préfixer une seconde fois.
    emb.set_footer(text=f"{sysd.get('dsm_version') or 'DSM ?'} · SNMP · lecture seule")
    return emb, {}


async def emb_syno_disques(bot, h=None):
    h = await nas_health(bot, h)
    disks = (h or {}).get("disks") or []
    if not disks:
        return _syno_muet()
    # SMART brut par disque : c'est LA valeur qui dit si un disque se dégrade.
    smart = {}
    for r in (h.get("smart") or []):
        dev = (r.get("dev") or "").rsplit("/", 1)[-1]
        smart.setdefault(dev, {})[r.get("attr_name")] = _syno_i(r, "raw")

    emb = discord.Embed(title="🗄️ NAS Synology — disques", color=fmt.GREEN)
    emb.timestamp = discord.utils.utcnow()
    pire = 0                                   # 0 = sain, 1 = à surveiller, 2 = grave
    for d in sorted(disks, key=lambda x: str(x.get("disk_id"))):
        status = _syno_i(d, "status")
        health = _syno_i(d, "health_status")
        bad = _syno_i(d, "bad_sector")
        temp = _syno_i(d, "temp_c")
        # status 1 = Normal (2+ = souci) ; health 1 = Normal, 2 = Warning, 3+ = Critical.
        niveau = 2 if (status != 1 or health >= 3) else (1 if (health == 2 or bad) else 0)
        pire = max(pire, niveau)
        mark = ("🟢", "🟠", "🔴")[niveau]
        lignes = [f"{d.get('disk_model') or '?'} · **{temp} °C**",
                  f"⚠️ **{bad}** secteur(s) défectueux" if bad
                  else "aucun secteur défectueux"]
        # « Disk 1 » -> sda, « Disk 2 » -> sdb : DSM numérote les baies à partir de 1
        # et nomme les périphériques dans le même ordre.
        num = "".join(c for c in str(d.get("disk_id") or "") if c.isdigit())
        devsm = smart.get(f"sd{chr(ord('a') + int(num) - 1)}", {}) if num else {}
        if devsm:
            lignes.append(f"SMART : {devsm.get('Reallocated_Sector_Ct', 0)} réalloué(s), "
                          f"{devsm.get('Current_Pending_Sector', 0)} en attente")
        emb.add_field(name=f"{mark} {d.get('disk_id') or '?'}",
                      value="\n".join(lignes), inline=False)
    emb.color = (fmt.GREEN, fmt.YELLOW, fmt.RED)[pire]
    emb.set_footer(text="SNMP · un secteur réalloué isolé et stable n'est pas alarmant ; "
                        "c'est sa progression qui compte")
    return emb, {}


async def emb_syno_volumes(bot, h=None):
    h = await nas_health(bot, h)
    raids = (h or {}).get("raid") or []
    if not raids:
        return _syno_muet()
    emb = discord.Embed(title="🗄️ NAS Synology — volumes", color=fmt.GREEN)
    emb.timestamp = discord.utils.utcnow()
    pire = 0                                   # 0 = sain, 1 = à surveiller, 2 = grave
    for rd in sorted(raids, key=lambda x: str(x.get("raid_name"))):
        nom = str(rd.get("raid_name") or "?")
        status = _syno_i(rd, "status")
        total = _syno_i(rd, "total_bytes")
        free = _syno_i(rd, "free_bytes")
        # status : 1 = Normal, 11 = Dégradé, 12 = Crashé (2-10 = états transitoires).
        etat = ("🟢 Normal" if status == 1
                else ("🟠 Dégradé" if status == 11 else "🔴 Critique"))
        if status != 1:
            pire = 2
        # Un « Storage Pool » affiche comme espace libre ce qui n'est pas encore
        # ALLOUÉ à un volume : une fois le volume créé il tombe à zéro, ce qui est
        # normal. En faire une barre de remplissage afficherait 100 % en permanence
        # et rendrait le salon rouge à tort — seuls les volumes sont mesurés.
        if "pool" in nom.lower():
            emb.add_field(name=f"{nom} — {etat}",
                          value=f"{fmt.humanize_bytes(total)} de disques\n"
                                f"{fmt.humanize_bytes(free)} non alloués",
                          inline=False)
            continue
        used = max(0, total - free)
        pct = (used / total * 100) if total else 0
        pire = max(pire, 2 if pct >= 90 else (1 if pct >= 75 else 0))
        emb.add_field(
            name=f"{nom} — {etat}",
            value=f"{fmt.pct_bar(pct)}\n{fmt.pct_of(used, total, decimals=1)}",
            inline=False)
    emb.color = (fmt.GREEN, fmt.YELLOW, fmt.RED)[pire]
    emb.set_footer(text="SNMP · « Storage Pool » = le groupe de disques, "
                        "« Volume » = l'espace utilisable dessus")
    return emb, {}


async def emb_syno_fiche(bot, h=None):
    """Fiche détaillée, catégorie Lock : identité de la machine + SMART complet."""
    h = await nas_health(bot, h)
    sysd = (h or {}).get("system") or {}
    if not sysd:
        return _syno_muet()
    emb = discord.Embed(title="🗄️ NAS Synology — fiche détaillée", color=fmt.BLURPLE)
    emb.timestamp = discord.utils.utcnow()
    emb.add_field(name="Modèle", value=str(sysd.get("model") or "?"))
    emb.add_field(name="DSM", value=str(sysd.get("dsm_version") or "?"))
    serial = sysd.get("serial")
    if serial:
        emb.add_field(name="N° de série", value=f"`{serial}`")
    emb.add_field(name="Adresse", value=f"`{getattr(bot.cfg, 'syno_host', '?')}`")

    for d in sorted((h.get("disks") or []), key=lambda x: str(x.get("disk_id"))):
        emb.add_field(
            name=str(d.get("disk_id") or "?"),
            value=f"{d.get('disk_model') or '?'}\n"
                  f"{_syno_i(d, 'temp_c')} °C · "
                  f"{_syno_i(d, 'bad_sector')} secteur(s) défectueux",
            inline=False)
    lignes = []
    for r in sorted((h.get("smart") or []),
                    key=lambda x: (str(x.get("dev")), str(x.get("attr_name")))):
        lignes.append(f"`{(r.get('dev') or '?').rsplit('/', 1)[-1]}` "
                      f"{r.get('attr_name')} = **{_syno_i(r, 'raw')}**")
    if lignes:
        emb.add_field(name="SMART (valeurs brutes)",
                      value="\n".join(lignes[:12]), inline=False)
    emb.set_footer(text="SNMP · lecture seule · M/O SYNO uniquement")
    return emb, {}


async def emb_reseau(bot, h=None):
    """Réseau : routeur CRS305 (SNMP), WAN (ping/speedtest), services publics (uptime)."""
    pending = {}
    inf = bot.influx
    rt = await inf.router_status()
    wan = await inf.wan_status()
    vh = await inf.vhost_uptime()
    color = fmt.GREEN
    emb = discord.Embed(title="🌐 Réseau — routeur · WAN · services publics", color=color)
    emb.timestamp = discord.utils.utcnow()

    def f(d, k):
        try:
            return float(d.get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    # `rh` et pas `h` : `h` est le relevé SNMP du NAS du contrat commun. Ce salon
    # l'ignore, mais réutiliser son nom pour l'hôte du routeur ferait silencieusement
    # lire le NAS le jour où quelqu'un déplace cette ligne (le rendu, lui, est
    # inchangé : c'était déjà une variable locale avant l'extraction du 2026-08-11).
    rh = rt.get("host") or {}
    if rh:
        mt, mu = f(rh, "mem_total_blocks"), f(rh, "mem_used_blocks")
        mempct = (mu / mt * 100) if mt else 0
        temp = f(rh, "cpu_temp")
        emb.add_field(
            name="🧭 Routeur CRS305",
            value=(f"🟢 en ligne · CPU {f(rh, 'cpu_load'):.0f}% · {temp:.0f}°C · "
                   f"RAM {mempct:.0f}% · uptime {fmt.humanize_duration(int(f(rh, 'uptime') / 100))}"),
            inline=False)
        if temp >= 65:
            color = fmt.RED
    else:
        emb.add_field(name="🧭 Routeur CRS305", value="⚠️ pas de données SNMP", inline=False)
    # WAN latence + débit
    inet = [r for r in (wan.get("ping") or []) if r.get("url") in ("1.1.1.1", "8.8.8.8")]
    if inet:
        avg = sum(f(r, "average_response_ms") for r in inet) / len(inet)
        loss = max(f(r, "percent_packet_loss") for r in inet)
        we = "🟢" if loss == 0 else ("🟠" if loss < 20 else "🔴")
        emb.add_field(name="🌍 Internet (WAN)",
                      value=f"{we} latence {avg:.0f} ms · perte {loss:.0f}%")
        if loss >= 20:
            color = fmt.RED
    sp = wan.get("speedtest") or {}
    if sp:
        emb.add_field(name="⚡ Débit fibre",
                      value=f"↓ {f(sp, 'down_mbps'):.0f} / ↑ {f(sp, 'up_mbps'):.0f} Mbit/s")
    # services publics (uptime synthétique)
    if vh:
        up, downs = 0, []
        for r in vh:
            code = int(f(r, "http_response_code"))
            (downs.append(r.get("site")) if not (0 < code < 500) else None)
            up += 1 if 0 < code < 500 else 0
        val = f"**{up}/{len(vh)}** en ligne"
        if downs:
            val += "\n🔴 DOWN : " + ", ".join(d for d in downs if d)
            color = fmt.RED
        # 1024 = limite Discord d'un champ d'embed : sans la coupe, une panne
        # généralisée (tous les vhosts DOWN) faisait rejeter l'embed entier.
        emb.add_field(name="🔗 Services publics (HTTPS)", value=val[:1024], inline=False)
    emb.color = color
    emb.set_footer(text="routeur SNMP · WAN ping/speedtest · uptime synthétique · rafraîchi")
    return emb, pending


async def emb_services(bot, h=None):
    """Santé niveau service (sondes TCP/HTTP) : Postgres/Vaultwarden/mail/*arr…"""
    pending = {}
    sh = await bot.influx.service_health()
    color = fmt.GREEN
    emb = discord.Embed(title="🩺 Santé des services", color=color)
    emb.timestamp = discord.utils.utcnow()

    def ms(d):
        try:
            return float(d.get("response_time", 0) or 0) * 1000
        except (TypeError, ValueError):
            return 0.0
    rows = []
    for r in (sh.get("net") or []):
        rows.append((r.get("service"), str(r.get("result_code")) in ("0", "0.0"), ms(r)))
    for r in (sh.get("http") or []):
        try:
            code = int(float(r.get("http_response_code", 0) or 0))
        except (TypeError, ValueError):
            code = 0
        ok = str(r.get("result_code")) in ("0", "0.0") and 0 < code < 500
        rows.append((r.get("service"), ok, ms(r)))
    rows.sort(key=lambda x: (x[1], x[0] or ""))
    body, down = [], 0
    for name, ok, m in rows:
        if not name:
            continue
        body.append(f"{'🟢' if ok else '🔴'} **{name}** · {m:.0f} ms")
        down += 0 if ok else 1
    if down:
        color = fmt.RED
    emb.description = (f"⚠️ **{down} service(s) KO**" if down
                       else "✅ Tous les services répondent")
    if body:
        emb.add_field(name="Services", value="\n".join(body)[:1024], inline=False)
    emb.color = color
    emb.set_footer(text="sondes TCP/HTTP toutes les 30 s · rafraîchi")
    return emb, pending


# Embeds épinglés des salons de « 📊 Supervision <SERVER_KEY> » : clé du salon dans
# prov["super"] -> constructeur. Table de MODULE (et non d'instance) depuis l'extraction :
# ce sont des fonctions libres, plus des méthodes liées — l'ordre d'itération est celui
# du rafraîchissement.
BUILDERS = {
    "materiel": emb_materiel,
    "sauvegardes": emb_sauvegardes,
    "stockage": emb_stockage,
    "seedbox": emb_seedbox,
    "nas": emb_nas,
    "reseau": emb_reseau,
    "services": emb_services,
}

# Salons du serveur SYNO (prov["syno"]) — mêmes règles, rôles M/O SYNO côté permissions.
SYNO_BUILDERS = {
    "sante": emb_syno_sante,
    "disques": emb_syno_disques,
    "volumes": emb_syno_volumes,
    "fiche": emb_syno_fiche,
}
