"""#vpn — état des VPN WireGuard (R820 wg-vpn / wg-avy + MikroTik CRS305 wg0 en secours).

Demande Nico 2026-08-30 : « obtenir les informations VPN sur le bot Discord, un salon dédié :
état de chaque connexion, ping, latence et utilisation, un maximum d'information ».

CONTEXTE (règle Nico du même jour) : TOUS les VPN se terminent en priorité sur le WireGuard
du R820 (`wg-vpn` udp/39671 pour les pairs nomades Pierre + PC de Nico, `wg-avy` udp/39672
pour le site-à-site Aveyron). Le MikroTik CRS305 (`wg0`, MÊME clé) ne prend le relais que si
le R820 est injoignable : son netwatch désactive alors le dst-nat udp/39671 et active le pair
« Hub Aveyron ». Ce cog rend ce mode visible (« primaire » / « secours ») et alerte sur la
bascule.

CE QUE FAIT CE COG
  - toutes les `VPN_POLL_SECONDS` (60 s), exécute `VPN_STATUS_CMD` (= `/usr/local/sbin/vpn-status`)
    SUR L'HYPERVISEUR via la clé SSH restreinte du bot (`core.nodeshell.run_readonly`). Le
    script y lit `wg show … dump` (R820) et interroge le MikroTik en SSH avec le mot de passe
    `/root/.mt_pw` qui NE QUITTE JAMAIS l'hyperviseur (CT106 ne le voit pas, son pare-feu
    `106.fw` n'autorise d'ailleurs pas 10.3.0.1) ;
  - tient à jour UN message épinglé dans #vpn (🔒 Lock R820) : mode, chaque pair (connecté /
    dernier handshake / jamais vu), endpoint, ping min/moy/max + perte, volumes rx/tx cumulés
    et débit instantané (delta entre deux relevés), hub Aveyron, MikroTik (netwatch, dst-nat,
    route, pairs wg0 avec leurs compteurs), IP WAN et cohérence DNS `nicov1.fr` ;
  - poste dans #vpn un ÉVÉNEMENT à chaque transition : pair qui se connecte / dont le
    handshake devient ancien, changement d'endpoint, bascule primaire↔secours, MikroTik
    (in)joignable, hub Aveyron (dé)connecté, DNS ≠ WAN ;
  - relaie dans #alertes (edge-trigger + snooze) : bascule en mode secours, hub Aveyron sans
    handshake récent, MikroTik injoignable, DNS public ≠ IP WAN ;
  - `/vpn` : le même tableau à la demande (éphémère).

CE QUE CE COG NE FAIT PAS
  - il ne modifie RIEN (ni pairs, ni bascule) : lecture seule, pas de bouton ;
  - il ne conclut jamais « VPN down » d'un échec SSH : dans ce cas le tableau affiche
    « lecture impossible » et garde le dernier état connu (« le bot réel dans ses mots ») ;
  - un handshake absent n'est pas « déconnecté » : WireGuard ne connaît pas la notion de
    session. On écrit « dernier handshake il y a … » ou « jamais vu depuis le démarrage de
    l'interface » (compteurs remis à zéro à chaque `wg-quick up`).

PIÈGES CONNUS
  - le dump `wg` contient la clé privée en 1re colonne de la ligne d'interface : `vpn-status`
    ne la lit jamais (il n'exporte que 8 caractères de clé publique) ;
  - les rx/tx du MikroTik sont déjà humanisés (« 25.4KiB ») et remis à zéro au reboot du
    routeur : on les affiche tels quels avec l'uptime, sans calculer de débit dessus ;
  - après `systemctl restart wg-quick@wg-vpn`, tous les compteurs R820 retombent à 0 : le
    débit calculé serait négatif → ignoré (delta < 0 = « compteurs réinitialisés »).
"""
import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import channels
from ..core import format as fmt
from ..core.nodeshell import run_readonly
from ..core.permissions import admin_check
from ..core.ui import pin_edit
from ..views.alertaction import alert_snoozed

log = logging.getLogger("discord-bot.vpn")

# Au-delà, on n'écrit plus « connecté » : WireGuard renégocie toutes les ~2 min quand du
# trafic passe (REKEY_AFTER_TIME 120 s) ; 3 min sans handshake = plus de trafic (observé,
# c'est aussi le seuil que `vpn-status` applique à `connected`).
CONNECTED_S = 180
IFACES = ("wg-vpn", "wg-avy")


# ============================================================ fonctions pures (testées)
def _safe(s, n=80):
    """Texte venu du réseau (nom de pair, endpoint) : pas de backtick, pas de saut de ligne, borné."""
    s = str(s or "").replace("`", "'").replace("\n", " ").replace("\r", "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_status(raw):
    """Sortie de `vpn-status` -> dict, ou None si vide / illisible (= lecture impossible)."""
    if not raw or not raw.strip():
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(d, dict) or "wg-vpn" not in d:
        return None
    return d


def peer_key(iface, p):
    return f"{iface}/{p.get('name') or p.get('pubkey_short') or '?'}"


def peers(data, iface):
    sec = (data or {}).get(iface) or {}
    return list(sec.get("peers") or []) if sec.get("present") else []


def rates(prev, cur):
    """Débits rx/tx (octets/s) par pair entre deux relevés ; None si pas de relevé précédent
    ou compteurs réinitialisés (delta négatif, ex. restart de l'interface)."""
    out = {}
    if not prev or not cur:
        return out
    dt = (cur.get("ts") or 0) - (prev.get("ts") or 0)
    if dt <= 0:
        return out
    before = {peer_key(i, p): p for i in IFACES for p in peers(prev, i)}
    for i in IFACES:
        for p in peers(cur, i):
            b = before.get(peer_key(i, p))
            if not b:
                continue
            drx, dtx = p["rx_bytes"] - b["rx_bytes"], p["tx_bytes"] - b["tx_bytes"]
            if drx < 0 or dtx < 0:
                out[peer_key(i, p)] = None  # compteurs remis à zéro
            else:
                out[peer_key(i, p)] = (drx / dt, dtx / dt)
    return out


def peer_state(p):
    """('🟢'|'🟠'|'⚪', libellé) selon l'âge du dernier handshake."""
    age = p.get("handshake_age_s")
    if age is None:
        return "⚪", "jamais vu depuis le démarrage de l'interface"
    if age < CONNECTED_S:
        return "🟢", f"connecté · handshake il y a {age} s"
    return "🟠", f"dernier handshake il y a {fmt.humanize_duration(age)}"


def _ping_txt(pg):
    if not pg:
        return "ping non mesuré"
    if pg.get("loss_pct", 100) >= 100 or pg.get("avg_ms") is None:
        return "ping : aucune réponse"
    return (f"ping {pg['avg_ms']:.0f} ms (min {pg['min_ms']:.0f} / max {pg['max_ms']:.0f}"
            + (f", {pg['loss_pct']} % perte" if pg.get("loss_pct") else "") + ")")


def peer_line(iface, p, rate):
    """Une ligne de tableau pour un pair R820."""
    emoji, st = peer_state(p)
    parts = [f"{emoji} **{_safe(p.get('name'), 24)}** `{_safe(p.get('tunnel_ip') or p.get('allowed_ips'), 40)}` — {st}"]
    if p.get("endpoint"):
        parts.append(f"endpoint `{_safe(p['endpoint'], 40)}`")
    if p.get("handshake_age_s") is not None:
        parts.append(_ping_txt(p.get("ping")))
        parts.append(f"↓ {fmt.humanize_bytes(p.get('rx_bytes', 0))} ↑ {fmt.humanize_bytes(p.get('tx_bytes', 0))}")
        if rate:
            parts.append(f"débit ↓ {fmt.humanize_rate(rate[0])} ↑ {fmt.humanize_rate(rate[1])}")
        elif rate is None and p.get("rx_bytes"):
            pass  # pas de relevé précédent : on n'invente pas de débit
    return " · ".join(parts)


def mode_of(data):
    return (data or {}).get("mode") or "inconnu"


def alerts_from(data):
    """Niveaux d'alerte (#alertes) déduits d'un relevé : clé -> 'warn' | 'crit' | None."""
    if not data:
        return {}
    mt = data.get("mikrotik") or {}
    hub = next(iter(peers(data, "wg-avy")), None)
    return {
        "vpn_failover": "warn" if mode_of(data) == "secours" else None,
        "vpn_mikrotik": "warn" if not mt.get("reachable") else None,
        "vpn_avy_down": "crit" if (hub is None or not hub.get("connected")) else None,
        "vpn_dns_wan": "warn" if data.get("dns_matches_wan") is False else None,
    }


def snapshot(data):
    """Résumé persistable servant à détecter les transitions (événements #vpn)."""
    snap = {"mode": mode_of(data), "mt": bool((data.get("mikrotik") or {}).get("reachable")),
            "dns_ok": data.get("dns_matches_wan"), "peers": {}}
    for i in IFACES:
        for p in peers(data, i):
            snap["peers"][peer_key(i, p)] = {"on": bool(p.get("connected")), "endpoint": p.get("endpoint"),
                                             "name": p.get("name")}
    return snap


def events(prev, cur):
    """Transitions entre deux snapshots -> lignes à poster dans #vpn (jamais au 1er relevé)."""
    if not prev:
        return []
    out = []
    if prev.get("mode") != cur.get("mode"):
        if cur["mode"] == "secours":
            out.append("🟠 **Bascule** : le R820 ne répond plus au MikroTik → **wg0 du MikroTik prend le relais** (nomades + Aveyron).")
        elif cur["mode"] == "primaire":
            out.append("🟢 **Retour au mode primaire** : le WireGuard du R820 redevient le point d'entrée.")
        else:
            out.append(f"⚪ Mode VPN indéterminé (MikroTik non lu) — précédent : {prev.get('mode')}.")
    if prev.get("mt") != cur.get("mt"):
        out.append("🟢 MikroTik de nouveau lisible en SSH." if cur["mt"] else "🔴 MikroTik **injoignable en SSH** depuis l'hyperviseur (l'état de secours n'est plus observable).")
    if prev.get("dns_ok") is not False and cur.get("dns_ok") is False:
        out.append("⚠️ `nicov1.fr` (DNS public) ≠ IP WAN de la Bbox : les clients nomades ne trouveront plus le serveur.")
    elif prev.get("dns_ok") is False and cur.get("dns_ok"):
        out.append("✅ `nicov1.fr` pointe à nouveau sur l'IP WAN.")
    for k, c in cur.get("peers", {}).items():
        p = prev.get("peers", {}).get(k)
        name, iface = c.get("name") or k, k.split("/")[0]
        if p is None:
            out.append(f"🆕 Nouveau pair **{_safe(name)}** sur `{iface}`.")
            continue
        if not p["on"] and c["on"]:
            out.append(f"🟢 **{_safe(name)}** connecté sur `{iface}`" + (f" depuis `{_safe(c['endpoint'], 40)}`" if c.get("endpoint") else "") + ".")
        elif p["on"] and not c["on"]:
            out.append(f"🟠 **{_safe(name)}** : plus de handshake depuis {CONNECTED_S // 60} min sur `{iface}` (session terminée ou inactive).")
        elif c["on"] and p.get("endpoint") and c.get("endpoint") and p["endpoint"] != c["endpoint"]:
            out.append(f"🔁 **{_safe(name)}** a changé d'endpoint : `{_safe(p['endpoint'], 40)}` → `{_safe(c['endpoint'], 40)}`.")
    return out


def build_embed(data, rate_map, *, poll_s, stale=None):
    """Tableau #vpn. `stale` = (âge en s, erreur) si le dernier relevé a échoué."""
    mode = mode_of(data)
    if mode == "primaire":
        color, mt_txt = fmt.GREEN, "🟢 **PRIMAIRE** — les VPN se terminent sur le R820 (`wg-vpn`/`wg-avy`), le MikroTik relaie udp/39671."
    elif mode == "secours":
        color, mt_txt = fmt.ORANGE, "🟠 **SECOURS** — le MikroTik a pris le relais sur `wg0` (R820 vu injoignable par son netwatch)."
    else:
        color, mt_txt = fmt.GREY, "⚪ mode **indéterminé** (MikroTik non lu)."
    emb = discord.Embed(title=f"🛡️ VPN WireGuard — mode {mode.upper()}", color=color)
    mt = data.get("mikrotik") or {}
    lines = [mt_txt]
    if data.get("public_dns_nicov1") or mt.get("wan_ip"):
        ok = data.get("dns_matches_wan")
        lines.append(f"🌐 IP WAN `{_safe(mt.get('wan_ip') or '?', 20)}` · `nicov1.fr` → `{_safe(data.get('public_dns_nicov1') or 'non résolu', 20)}` "
                     + ("✅" if ok else ("⚠️ **différent**" if ok is False else "")))
    if stale:
        lines.append(f"⚠️ **Lecture impossible** depuis {fmt.humanize_duration(stale[0])} ({_safe(stale[1], 60)}) — état ci-dessous = dernier relevé réussi.")
    emb.description = "\n".join(lines)

    for iface, port_lbl in (("wg-vpn", "🧭"), ("wg-avy", "🏔️")):
        sec = data.get(iface) or {}
        if not sec.get("present"):
            emb.add_field(name=f"{port_lbl} {iface}", value="interface absente sur le R820", inline=False)
            continue
        rows = [peer_line(iface, p, rate_map.get(peer_key(iface, p))) for p in sec.get("peers") or []]
        if iface == "wg-avy":
            av = data.get("aveyron") or {}
            rows.append("hub `10.99.0.1` " + _ping_txt(av.get("hub_10.99.0.1")) + " · PVE `10.0.10.10` " + _ping_txt(av.get("pve_10.0.10.10")))
        n_on = sum(1 for p in sec.get("peers") or [] if p.get("connected"))
        emb.add_field(name=f"{port_lbl} {sec.get('label', iface)} — `{iface}` udp/{sec.get('listen_port')} · {n_on}/{len(sec.get('peers') or [])} connecté(s)",
                      value=("\n".join(rows) or "aucun pair")[:1024], inline=False)

    if mt.get("reachable"):
        rows = [f"netwatch R820 : **{_safe(mt.get('netwatch_status') or '?', 10)}** · dst-nat → R820 : {'✅ actif' if mt.get('dstnat_enabled') else '⛔ désactivé'}"
                f" · route 10.3.99.0/24 via **{_safe(mt.get('route_vpn_via') or '?', 6)}** · pair Hub Aveyron : {'▶️ actif' if mt.get('hub_peer_enabled') else '⏸️ inactif (normal)'}"]
        rows.append(f"uptime routeur {_safe(mt.get('uptime') or '?', 16)} · paquets udp/39671 reçus par wg0 depuis le boot : {mt.get('wg0_input_packets') if mt.get('wg0_input_packets') is not None else '?'}")
        for p in mt.get("wg0_peers") or []:
            st = "⏸️" if p.get("disabled") else ("🟢" if p.get("last_handshake") and _mt_recent(p["last_handshake"]) else ("🟠" if p.get("last_handshake") else "⚪"))
            bits = [f"{st} **{_safe(p.get('name'), 24)}**"]
            bits.append(f"handshake il y a {_safe(p['last_handshake'], 12)}" if p.get("last_handshake") else "jamais vu depuis le boot")
            if p.get("endpoint"):
                bits.append(f"endpoint `{_safe(p['endpoint'], 40)}`")
            if p.get("rx") is not None:
                bits.append(f"↓ {_safe(p['rx'], 12)} ↑ {_safe(p.get('tx'), 12)}")
            rows.append(" · ".join(bits))
        emb.add_field(name="📡 MikroTik CRS305 — `wg0` (secours, même clé)", value="\n".join(rows)[:1024], inline=False)
    else:
        emb.add_field(name="📡 MikroTik CRS305 — `wg0` (secours)",
                      value=f"🔴 non lu : {_safe(mt.get('error') or 'injoignable', 60)}", inline=False)
    ts = data.get("ts")
    when = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
    emb.set_footer(text=f"Relevé {when} en {data.get('duration_s', '?')} s · toutes les {poll_s} s via SSH hyperviseur (vpn-status) · "
                        f"MikroTik lu depuis l'hyperviseur · ping = 3 échos depuis le R820 · « connecté » = handshake < {CONNECTED_S} s")
    return emb


def _mt_recent(s):
    """'16s' / '4m18s' / '22h44m' -> True si < CONNECTED_S."""
    total, num = 0, ""
    for ch in str(s):
        if ch.isdigit():
            num += ch
        elif ch in "dhms" and num:
            total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[ch]
            num = ""
    return total < CONNECTED_S


# ============================================================ cog
class Vpn(commands.Cog):
    """#vpn : tableau épinglé + événements + alertes, et /vpn."""

    def __init__(self, bot):
        self.bot = bot
        self._alerts = bot.state.ns("vpn")
        self._last = None          # dernier relevé réussi
        self._last_ok_ts = None
        self._prev_for_rate = None
        self.last_error = None
        self._warned_no_channel = False

    async def cog_load(self):
        if not self.bot.cfg.vpn_enabled:
            log.warning("vpn: VPN_ENABLED=false, cog inactif")
            return
        self.poll.change_interval(seconds=self.bot.cfg.vpn_poll_seconds)
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    # ------------------------------------------------------------ collecte
    async def _collect(self):
        cfg = self.bot.cfg
        try:
            raw = await run_readonly(cfg, cfg.vpn_status_cmd, timeout=45)
        except Exception as e:  # noqa: BLE001 — SSH KO ≠ VPN KO
            self.last_error = f"SSH hyperviseur : {e}"
            log.warning("vpn: lecture impossible: %s", e)
            return None
        data = parse_status(raw)
        if data is None:
            self.last_error = "sortie de vpn-status vide ou illisible"
            log.warning("vpn: %s", self.last_error)
            return None
        self.last_error = None
        return data

    # ------------------------------------------------------------ salon
    async def _channel(self, cid, what):
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("vpn: salon %s (%s) injoignable: %s", cid, what, e)
                return None
        return ch

    async def _vpn_channel(self):
        """#vpn dans « 🔒 Lock R820 » (créé par le cog, jamais hors catégorie — règle 2026-08-11)."""
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        info = self.bot.state.get("vpn_msg") or {}
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is None and self.bot.cfg.vpn_channel_id:
            ch = guild.get_channel(self.bot.cfg.vpn_channel_id)
        cat = channels.lock_category(self.bot, guild)
        if ch is None:
            ch = await channels.ensure_channel(
                self.bot, guild, "vpn", cat,
                topic="🛡️ VPN WireGuard : pairs nomades (wg-vpn R820), site-à-site Aveyron (wg-avy), "
                      "secours MikroTik wg0. Tableau épinglé + événements. Lecture seule.",
                reason="salon VPN WireGuard (demande Nico 2026-08-30)")
            if ch is None:
                if not self._warned_no_channel:
                    log.warning("vpn: #vpn non créé (pas de catégorie Lock) — réessai au prochain cycle")
                    self._warned_no_channel = True
                return None
        await channels.seal_if_public(self.bot, ch, cat, why="endpoints publics et clés des pairs VPN")
        if info.get("channel") != ch.id:
            info["channel"] = ch.id
            self.bot.state.set("vpn_msg", info)
        return ch

    async def _publish(self, data, rate_map, stale=None):
        ch = await self._vpn_channel()
        if ch is None:
            return
        emb = build_embed(data, rate_map, poll_s=self.bot.cfg.vpn_poll_seconds, stale=stale)
        info = self.bot.state.get("vpn_msg") or {}
        _msg, mid = await pin_edit(ch, emb, message_id=info.get("message"), label="#vpn", log=log)
        if mid and mid != info.get("message"):
            info["message"] = mid
            self.bot.state.set("vpn_msg", info)

    async def _post_events(self, lines):
        if not lines:
            return
        ch = await self._vpn_channel()
        if ch is None:
            return
        chunk = ""
        for ln in lines:
            if len(chunk) + len(ln) + 1 > 1900:
                await ch.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                chunk = ""
            chunk += ln + "\n"
        if chunk:
            await ch.send(chunk, allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------ alertes (#alertes)
    async def _fire(self, key, level, title, desc):
        prev = self._alerts.level(key)
        if level and level != prev:
            if alert_snoozed(self.bot.state, key):
                return
            ch = await self._channel(self.bot.cfg.alert_channel_id, "#alertes")
            if ch is None:
                return
            emb = discord.Embed(title=title, description=desc,
                                color=fmt.RED if level == "crit" else fmt.YELLOW)
            emb.set_footer(text=f"alerte: {key} [{level}]")
            await ch.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
            self._alerts.set_level(key, level)
        elif not level and prev:
            ch = await self._channel(self.bot.cfg.alert_channel_id, "#alertes")
            if ch is not None and not alert_snoozed(self.bot.state, key):
                await ch.send(embed=discord.Embed(
                    title=f"✅ Résolu — {title}",
                    description="Condition disparue au dernier relevé.", color=fmt.GREEN))
            self._alerts.clear(key)

    ALERT_TEXT = {
        "vpn_failover": ("🟠 VPN en mode SECOURS (MikroTik)",
                         "Le netwatch du MikroTik ne voit plus le R820 : `wg0` a pris le relais pour les "
                         "nomades ET le site-à-site Aveyron. Les clients ne voient rien, mais le R820 est à vérifier."),
        "vpn_mikrotik": ("🔴 MikroTik injoignable en SSH",
                         "`vpn-status` (hyperviseur) n'a pas pu lire le CRS305 : le mode primaire/secours "
                         "n'est plus observable. Les tunnels R820 restent lus."),
        "vpn_avy_down": ("🔴 Site-à-site Aveyron sans handshake récent",
                         f"`wg-avy` n'a pas de handshake depuis plus de {CONNECTED_S} s avec le hub 82.66.8.226 : "
                         "supervision Aveyron, NFS média et bot multi-cluster impactés."),
        "vpn_dns_wan": ("⚠️ nicov1.fr ≠ IP WAN",
                        "Le DNS public ne pointe plus sur l'IP WAN de la Bbox : les clients nomades "
                        "(endpoint `nicov1.fr:39671`) ne trouveront plus le serveur. DynHost / IP Bbox à vérifier."),
    }

    # ------------------------------------------------------------ boucle
    @tasks.loop(seconds=60)
    async def poll(self):
        data = await self._collect()
        if data is None:
            if self._last is not None:
                age = int(time.time() - (self._last_ok_ts or time.time()))
                await self._publish(self._last, {}, stale=(age, self.last_error or "?"))
            return
        rate_map = rates(self._prev_for_rate, data)
        prev_snap = self.bot.state.get("vpn_snap")
        snap = snapshot(data)
        await self._publish(data, rate_map)
        if self.bot.cfg.vpn_events:
            await self._post_events(events(prev_snap, snap))
        self.bot.state.set("vpn_snap", snap)
        for key, level in alerts_from(data).items():
            title, desc = self.ALERT_TEXT[key]
            await self._fire(key, level, title, desc)
        self._prev_for_rate, self._last, self._last_ok_ts = data, data, time.time()

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ commande
    @app_commands.command(name="vpn", description="État des VPN WireGuard (R820 prioritaire, MikroTik en secours).")
    @admin_check(require_admin_channel=False, cap="services")
    async def vpn_cmd(self, itx: discord.Interaction):
        if not self.bot.cfg.vpn_enabled:
            await itx.response.send_message("Cog VPN désactivé (`VPN_ENABLED=false`).", ephemeral=True)
            return
        await itx.response.defer(ephemeral=True, thinking=True)
        data = await self._collect()
        if data is None:
            if self._last is None:
                await itx.followup.send(f"❌ Lecture impossible : {_safe(self.last_error, 120)}", ephemeral=True)
                return
            age = int(time.time() - (self._last_ok_ts or time.time()))
            emb = build_embed(self._last, {}, poll_s=self.bot.cfg.vpn_poll_seconds, stale=(age, self.last_error or "?"))
        else:
            emb = build_embed(data, rates(self._prev_for_rate, data), poll_s=self.bot.cfg.vpn_poll_seconds)
        await itx.followup.send(embed=emb, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Vpn(bot))
