"""DNS maison (AdGuard Home, CT125 `dns` 10.3.10.53) : flux des requêtes, salon #dns, /dns.

CE QUE FAIT CE COG (demande Nico 2026-08-28 : « ajoute le trafic DNS dans les logs du
bot et aussi dans un salon spécifique avec les informations essentielles »)
-----------------------------------------------------------------------------
1. `dns_poll` (boucle, DNS_POLL_SECONDS, défaut 60 s) : lit le journal des requêtes
   d'AdGuard Home (`/control/querylog`) depuis le dernier horodatage vu et poste :
     - dans #journaux-live (le « log » du bot, `cfg.live_log_channel_id`) : TOUT le
       trafic DNS, compacté (lignes identiques consécutives client+domaine → ×N,
       lots découpés à 1900 caractères) — débrayable via DNS_FEED_LOGS=0 ;
     - dans #dns (salon provisionné « dns », catégorie Supervision) : les requêtes
       BLOQUÉES regroupées par (client, domaine) avec la règle — SEULEMENT si
       DNS_BLOCKED_FEED=1. Défaut 0 depuis le 28/08 (Nico : #dns = bilan horaire et
       pics seulement, le détail reste dans l'UI AdGuard et Grafana).
   Le curseur (horodatage AdGuard de la dernière entrée traitée) vit dans state.json
   et n'avance QU'APRÈS un envoi réussi : salon injoignable = on réessaie, rien n'est
   perdu en silence. Premier démarrage : on se cale sur le présent SANS rejouer
   l'historique (sinon chaque déploiement re-posterait 90 jours de journal).
2. `dns_digest` (chaque heure pile) : embed dans #dns avec les compteurs de l'heure
   ÉCOULÉE tels que RELEVÉS PAR CE COG (requêtes, bloquées, % , top domaines, top
   bloqués, top clients, latence moyenne). Ce sont les entrées effectivement vues par
   le sondage, pas une statistique AdGuard — le pied de page le dit.
3. Pics (edge-trigger, espace d'alertes « dns », salon #alertes) : un client qui
   dépasse DNS_SPIKE_BLOCKED requêtes bloquées dans un seul cycle de sondage
   (défaut 30) = « probable bot/télémétrie/malware » ; un volume total supérieur à
   DNS_SPIKE_QUERIES par cycle (défaut 600) = « probable boucle ou scan ». Le mot
   « probable » est volontaire : le cog constate un volume, il ne prouve pas une
   intention (règle « le bot réel dans ses mots »).
4. `/dns stats` (lecture), `/dns top` (lecture), `/dns pause` (admin, persistant),
   `/dns bloquer <domaine>` / `/dns debloquer <domaine>` (admin : règle utilisateur
   AdGuard `||domaine^`, effet immédiat, visible dans /dns top).

CE QUE CE COG NE FAIT PAS
-------------------------
- Il ne crée AUCUN salon lui-même : #dns est déclaré dans `provision.SUPER_CHANNELS`
  et son id arrive par `Provision._rewire` (comme #alertes). Sans provisioning, le
  flux « essentiel » est simplement inactif et le journal le dit une fois.
- Il ne touche pas aux listes de blocage (HaGeZi, mises à jour manuelles, cf. note
  d'infra) ni à la configuration upstream (8.8.8.8 uniquement, choix Nico).

PIÈGES CONNUS
-------------
- `/control/querylog` renvoie du plus RÉCENT au plus ancien, limité à `limit` : si un
  cycle ramène `limit` entrées toutes nouvelles, il y en a PROBABLEMENT d'autres non
  vues — on l'annonce dans le lot au lieu de faire comme si de rien n'était.
- Horodatages AdGuard en nanosecondes (« …30.022823354Z ») : `fromisoformat` refuse 9
  décimales, `_parse_ts` tronque à 6. Comparer des chaînes serait faux (longueurs
  variables).
- Un nom de domaine est une donnée fournie par le RÉSEAU (donc par un attaquant
  potentiel) : tout envoi passe avec `AllowedMentions.none()` et les backticks sont
  neutralisés.
- 106.fw : `policy_out DROP` — sans la règle « OUT ACCEPT -dest 10.3.10.53 tcp 3000 »
  toutes les lectures échouent en silence (None) et le cog le dit dans /dns stats.
"""
import base64
import datetime as dt
import logging
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.http import ApiClient
from ..core.permissions import admin_check, read_check
from ..views.alertaction import alert_snoozed

log = logging.getLogger("discord-bot.dns")

QUERYLOG_LIMIT = 500          # taille max d'un cycle ; au-delà on annonce la troncature
DISCORD_CHUNK = 1900          # marge sous la limite de 2000 caractères
TOP_N = 8
# Raisons AdGuard qui signifient « réponse refusée/remplacée par un blocage ». Les
# rewrites (SafeSearch, /etc/hosts, réécritures DNS) ne sont PAS des blocages.
BLOCKED_REASONS = frozenset({"FilteredBlackList", "FilteredSafeBrowsing", "FilteredParental",
                             "FilteredBlockedService", "FilteredInvalid"})

# Hôtes que le cog sait nommer avec certitude (adresses FIXES de l'infra). Tout autre
# client est affiché par son IP nue : on ne devine pas un nom de machine.
KNOWN_CLIENTS = {
    "10.3.10.200": "hyperviseur pve",
    "10.3.10.1": "routeur CRS305",
    "10.3.10.53": "AdGuard (lui-même)",
    "10.3.10.106": "bot Edmine (CT106)",
}


# ============================================================ fonctions pures (testées)

def _parse_ts(s):
    """« 2026-08-28T17:59:30.022823354Z » -> datetime UTC aware. None si illisible."""
    if not s:
        return None
    try:
        base, _, frac = s.rstrip("Z").partition(".")
        frac = (frac + "000000")[:6]
        return dt.datetime.fromisoformat(f"{base}.{frac}").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _safe(s, n=80):
    """Texte venu du réseau : pas de backtick, pas de saut de ligne, longueur bornée."""
    s = str(s or "").replace("`", "'").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_querylog(payload):
    """Réponse AdGuard `/control/querylog` -> liste d'entrées normalisées, du plus
    ANCIEN au plus récent. Une entrée sans horodatage lisible est ignorée en le disant."""
    rows = (payload or {}).get("data") or []
    out = []
    for r in rows:
        t = _parse_ts(r.get("time"))
        if t is None:
            log.warning("dns: entrée querylog sans horodatage lisible ignorée: %r",
                        str(r)[:120])
            continue
        q = r.get("question") or {}
        reason = r.get("reason") or ""
        try:
            elapsed = float(r.get("elapsedMs") or 0.0)
        except (TypeError, ValueError):
            elapsed = 0.0
        out.append({
            "time": t,
            "client": r.get("client") or "?",
            "name": q.get("name") or "?",
            "qtype": q.get("type") or "?",
            "blocked": reason in BLOCKED_REASONS,
            "reason": reason,
            "rule": r.get("rule") or "",
            "upstream": r.get("upstream") or "",
            "cached": bool(r.get("cached")),
            "status": r.get("status") or "",
            "elapsed_ms": elapsed,
        })
    out.sort(key=lambda e: e["time"])
    return out


def newer_than(entries, cursor):
    """Entrées strictement postérieures au curseur (datetime) ; tout si curseur None."""
    if cursor is None:
        return list(entries)
    return [e for e in entries if e["time"] > cursor]


def client_label(ip):
    name = KNOWN_CLIENTS.get(ip)
    return f"{ip} ({name})" if name else ip


def feed_lines(entries):
    """Trafic COMPLET pour #journaux-live : une ligne par requête, les répétitions
    consécutives (même client, même domaine, même issue) sont compactées en ×N."""
    lines = []
    prev_key, prev_idx = None, -1
    for e in entries:
        if e["blocked"]:
            issue = "⛔ bloqué"
        elif e["cached"]:
            issue = "⚡ cache"
        else:
            issue = f"→ {e['upstream'] or '?'}"
        key = (e["client"], e["name"], e["qtype"], issue)
        if key == prev_key:
            n = lines[prev_idx][1] + 1
            lines[prev_idx] = (lines[prev_idx][0], n)
            continue
        hhmm = e["time"].astimezone().strftime("%H:%M:%S")
        txt = (f"{hhmm}  {e['client']:<15} {e['qtype']:<5} {_safe(e['name'], 70)}  "
               f"{issue}  {e['elapsed_ms']:.1f} ms")
        lines.append((txt, 1))
        prev_key, prev_idx = key, len(lines) - 1
    return [t if n == 1 else f"{t}  ×{n}" for t, n in lines]


def blocked_summary(entries):
    """L'ESSENTIEL pour #dns : requêtes bloquées regroupées par (client, domaine),
    avec la règle qui a matché. Triées par volume décroissant."""
    groups = {}
    for e in entries:
        if not e["blocked"]:
            continue
        k = (e["client"], e["name"])
        g = groups.setdefault(k, {"n": 0, "rule": e["rule"], "reason": e["reason"],
                                  "first": e["time"], "last": e["time"]})
        g["n"] += 1
        g["last"] = e["time"]
        if not g["rule"] and e["rule"]:
            g["rule"] = e["rule"]
    lines = []
    for (client, name), g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        why = _safe(g["rule"], 60) if g["rule"] else g["reason"] or "raison non fournie"
        when = g["last"].astimezone().strftime("%H:%M")
        mult = f" ×{g['n']}" if g["n"] > 1 else ""
        lines.append(f"⛔ `{client_label(client)}` → **{_safe(name, 70)}**{mult} "
                     f"— {why} ({when})")
    return lines


def chunk_lines(lines, limit=DISCORD_CHUNK, fence=True):
    """Découpe une liste de lignes en messages ≤ limit. `fence` = bloc de code."""
    out, cur = [], ""
    wrap = (len("```\n```") if fence else 0)
    for ln in lines:
        ln = ln[: limit - wrap - 1]
        if cur and len(cur) + len(ln) + 1 > limit - wrap:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        out.append(cur)
    if fence:
        out = [f"```\n{c}\n```" for c in out]
    return out


def detect_spikes(entries, blocked_threshold, query_threshold):
    """Constats de volume sur UN cycle. Renvoie une liste de
    (clé, niveau, titre, description) ; niveau None = nominal pour cette clé.
    Le vocabulaire reste au constat (« probable ») : un volume n'est pas une preuve."""
    out = []
    per_client_blocked = Counter(e["client"] for e in entries if e["blocked"])
    worst_client, worst_n = (per_client_blocked.most_common(1) or [(None, 0)])[0]
    if worst_client and worst_n >= blocked_threshold:
        doms = Counter(e["name"] for e in entries
                       if e["blocked"] and e["client"] == worst_client).most_common(3)
        detail = ", ".join(f"{_safe(d, 40)} ×{n}" for d, n in doms)
        out.append(("dns_client_blocked_spike", "warn",
                    "🟡 DNS : rafale de requêtes bloquées",
                    f"`{client_label(worst_client)}` : **{worst_n}** requêtes bloquées "
                    f"en un cycle (seuil {blocked_threshold}).\n"
                    f"Domaines : {detail}\n"
                    "Probable télémétrie, bot ou logiciel indésirable — à vérifier sur "
                    "la machine. AdGuard bloque déjà ces domaines."))
    else:
        out.append(("dns_client_blocked_spike", None, "DNS : rafale de bloquées", ""))

    total = len(entries)
    if total >= query_threshold:
        top = Counter(e["client"] for e in entries).most_common(3)
        detail = ", ".join(f"{client_label(c)} ×{n}" for c, n in top)
        out.append(("dns_volume_spike", "warn",
                    "🟡 DNS : volume de requêtes inhabituel",
                    f"**{total}** requêtes en un cycle (seuil {query_threshold}).\n"
                    f"Clients : {detail}\nProbable boucle de résolution ou scan."))
    else:
        out.append(("dns_volume_spike", None, "DNS : volume de requêtes", ""))
    return out


class HourCounters:
    """Compteurs de l'heure en cours, alimentés par les entrées RÉELLEMENT vues."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.started = dt.datetime.now(dt.timezone.utc)
        self.total = 0
        self.blocked = 0
        self.elapsed_sum = 0.0
        self.domains = Counter()
        self.blocked_domains = Counter()
        self.clients = Counter()
        self.blocked_clients = Counter()
        self.truncated = 0

    def add(self, entries):
        for e in entries:
            self.total += 1
            self.elapsed_sum += e["elapsed_ms"]
            self.domains[e["name"]] += 1
            self.clients[e["client"]] += 1
            if e["blocked"]:
                self.blocked += 1
                self.blocked_domains[e["name"]] += 1
                self.blocked_clients[e["client"]] += 1


def digest_embed(hc, poll_seconds):
    """Embed du digest horaire à partir des compteurs relevés (pas des stats AdGuard)."""
    pct = (hc.blocked / hc.total * 100.0) if hc.total else 0.0
    color = fmt.GREEN if pct < 15 else (fmt.YELLOW if pct < 40 else fmt.RED)
    since = hc.started.astimezone().strftime("%H:%M")
    emb = discord.Embed(
        title=f"🕐 DNS — bilan de l'heure (depuis {since})", color=color)
    if hc.total == 0:
        emb.description = ("Aucune requête relevée sur l'heure — soit rien n'a interrogé "
                           "AdGuard, soit le sondage a échoué (voir le journal du bot).")
    else:
        avg = hc.elapsed_sum / hc.total
        emb.add_field(name="Requêtes", value=f"**{hc.total}**", inline=True)
        emb.add_field(name="Bloquées", value=f"**{hc.blocked}** · {pct:.1f} %", inline=True)
        emb.add_field(name="Latence moyenne", value=f"{avg:.1f} ms", inline=True)

        def top(counter, n=TOP_N, label=lambda k: _safe(k, 45)):
            return "\n".join(f"`{v:>4}` {label(k)}" for k, v in counter.most_common(n)) or "—"
        emb.add_field(name="Top domaines", value=top(hc.domains), inline=False)
        emb.add_field(name="Top bloqués", value=top(hc.blocked_domains), inline=False)
        emb.add_field(name="Top clients",
                      value=top(hc.clients, label=client_label), inline=False)
        if hc.truncated:
            emb.add_field(name="⚠️ Relevé incomplet",
                          value=f"{hc.truncated} cycle(s) ont atteint la limite de "
                                f"{QUERYLOG_LIMIT} entrées : les totaux sont des MINIMA.",
                          inline=False)
    emb.set_footer(text=f"Compté par le bot sur les entrées relevées toutes les "
                        f"{poll_seconds} s · source AdGuard Home CT125")
    return emb


def user_rules_toggle(rules, domain, block):
    """Ajoute/retire la règle « ||domaine^ » dans la liste des règles utilisateur.
    Renvoie (nouvelle_liste, changé)."""
    rule = f"||{domain}^"
    rules = [r for r in (rules or []) if r.strip()]
    if block:
        if rule in rules:
            return rules, False
        return rules + [rule], True
    if rule not in rules:
        return rules, False
    return [r for r in rules if r != rule], True


def valid_domain(d):
    d = (d or "").strip().lower().rstrip(".")
    if not d or len(d) > 253 or "/" in d or " " in d or "`" in d:
        return None
    parts = d.split(".")
    if len(parts) < 2 or any(not p or len(p) > 63 for p in parts):
        return None
    if any(not (c.isalnum() or c in "-_") for p in parts for c in p):
        return None
    if any(p[0] == "-" or p[-1] == "-" for p in parts):
        return None
    return d


# ============================================================================ le cog

class Dns(commands.Cog):
    dns = app_commands.Group(name="dns",
                             description="DNS maison (AdGuard Home) : stats, top, pause, blocage.")

    def __init__(self, bot):
        self.bot = bot
        cfg = bot.cfg
        tok = base64.b64encode(f"{cfg.adguard_user}:{cfg.adguard_pass}".encode()).decode()
        self.ag = ApiClient(cfg.adguard_url, {"Authorization": f"Basic {tok}"},
                            timeout=10, label="adguard")
        prov = bot.state.get("prov", {}) or {}
        self.channel_id = (prov.get("super") or {}).get("dns") or cfg.dns_channel_id
        self.paused = bool(bot.state.get("dns_paused", False))
        self.hour = HourCounters()
        self._alerts = bot.state.ns("dns")
        self._warned_no_channel = False
        self.last_error = None

    async def cog_load(self):
        if not self.bot.cfg.adguard_enabled:
            log.warning("dns: ADGUARD_USER/ADGUARD_PASS absents — cog inactif "
                        "(/dns répondra « non configuré »)")
            return
        self.dns_poll.change_interval(seconds=self.bot.cfg.dns_poll_seconds)
        self.dns_poll.start()
        self.dns_digest.start()

    async def cog_unload(self):
        self.dns_poll.cancel()
        self.dns_digest.cancel()

    # ------------------------------------------------------------------ salons
    async def _channel(self, cid, what):
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("dns: salon %s (%s) injoignable: %s", cid, what, e)
                return None
        return ch

    async def _send_chunks(self, ch, chunks):
        for c in chunks:
            await ch.send(c[:2000], allowed_mentions=discord.AllowedMentions.none())

    # ------------------------------------------------------------------ boucles
    @tasks.loop(seconds=60)
    async def dns_poll(self):
        st = self.bot.state
        cfg = self.bot.cfg
        payload = await self.ag.aget("/control/querylog",
                                     {"limit": QUERYLOG_LIMIT, "response_status": "all"},
                                     quiet=True)
        if payload is None:
            # None = appel en échec (CT125 down, 106.fw, mot de passe) — on ne conclut
            # RIEN, on réessaie au prochain cycle.
            self.last_error = f"AdGuard injoignable ({cfg.adguard_url}) à " \
                              f"{dt.datetime.now().strftime('%H:%M:%S')}"
            log.warning("dns: %s", self.last_error)
            return
        self.last_error = None
        entries = parse_querylog(payload)
        cursor = _parse_ts(st.get("dns_cursor"))
        if cursor is None:
            # premier démarrage : se caler sur le présent sans rejouer l'historique
            if entries:
                st.set("dns_cursor", entries[-1]["time"].isoformat())
            else:
                st.set("dns_cursor", dt.datetime.now(dt.timezone.utc).isoformat())
            log.info("dns: curseur initialisé, %d entrées existantes non rejouées",
                     len(entries))
            return
        fresh = newer_than(entries, cursor)
        if not fresh:
            return
        truncated = len(fresh) >= QUERYLOG_LIMIT
        self.hour.add(fresh)
        if truncated:
            self.hour.truncated += 1

        # -- alertes de pic (edge-trigger, même modèle que Alerts._fire) ----------
        for key, level, title, desc in detect_spikes(fresh, cfg.dns_spike_blocked,
                                                     cfg.dns_spike_queries):
            await self._fire(key, level, title, desc)

        if self.paused:
            st.set("dns_cursor", fresh[-1]["time"].isoformat())
            return

        # -- #dns : bloquées du cycle — DÉSACTIVÉ par défaut (Nico 28/08 : « limite #dns
        #    au bilan horaire et aux pics seulement »). DNS_BLOCKED_FEED=1 pour rouvrir.
        ch = await self._channel(self.channel_id, "#dns") if cfg.dns_blocked_feed else None
        if ch is None:
            if cfg.dns_blocked_feed and not self._warned_no_channel:
                log.warning("dns: aucun salon #dns (provisioning non fait ou "
                            "DNS_CHANNEL_ID vide) — flux des bloquées inactif")
                self._warned_no_channel = True
        else:
            lines = blocked_summary(fresh)
            if truncated:
                lines.append(f"⚠️ cycle plein ({QUERYLOG_LIMIT} entrées) : d'autres "
                             "requêtes ont probablement eu lieu sans être relevées")
            if lines:
                await self._send_chunks(ch, chunk_lines(lines, fence=False))

        # -- #journaux-live : tout le trafic -----------------------------------------
        if cfg.dns_feed_logs:
            lg = await self._channel(cfg.live_log_channel_id, "#journaux-live")
            if lg is not None:
                lines = feed_lines(fresh)
                head = f"DNS · {len(fresh)} requête(s), " \
                       f"{sum(1 for e in fresh if e['blocked'])} bloquée(s)"
                if truncated:
                    head += f" — limite {QUERYLOG_LIMIT} atteinte, relevé incomplet"
                await self._send_chunks(lg, chunk_lines([head] + lines))

        # curseur avancé seulement ici : tout envoi raté ci-dessus a levé et sera rejoué
        st.set("dns_cursor", fresh[-1]["time"].isoformat())

    @dns_poll.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()

    @tasks.loop(time=[dt.time(h, 0) for h in range(24)])
    async def dns_digest(self):
        hc, self.hour = self.hour, HourCounters()
        ch = await self._channel(self.channel_id, "#dns")
        if ch is None:
            return
        await ch.send(embed=digest_embed(hc, self.bot.cfg.dns_poll_seconds))

    @dns_digest.before_loop
    async def _before_digest(self):
        await self.bot.wait_until_ready()

    async def _fire(self, key, level, title, desc):
        prev = self._alerts.level(key)
        if level and level != prev:
            if alert_snoozed(self.bot.state, key):
                return
            ch = await self._channel(self.bot.cfg.alert_channel_id, "#alertes")
            if ch is None:
                return
            emb = discord.Embed(title=title, description=desc, color=fmt.YELLOW)
            emb.set_footer(text=f"alerte: {key} [{level}]")
            await ch.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
            self._alerts.set_level(key, level)
        elif not level and prev:
            ch = await self._channel(self.bot.cfg.alert_channel_id, "#alertes")
            if ch is not None and not alert_snoozed(self.bot.state, key):
                await ch.send(embed=discord.Embed(
                    title=f"✅ Résolu — {title}",
                    description="Volume revenu sous le seuil au dernier cycle.",
                    color=fmt.GREEN))
            self._alerts.clear(key)

    # ------------------------------------------------------------------ commandes
    def _not_configured(self):
        return not self.bot.cfg.adguard_enabled

    @dns.command(name="stats", description="État et statistiques du DNS maison (AdGuard Home).")
    @read_check()
    async def dns_stats(self, itx: discord.Interaction):
        await itx.response.defer()
        if self._not_configured():
            await itx.followup.send("DNS : ADGUARD_USER/ADGUARD_PASS non configurés dans config.env.")
            return
        status = await self.ag.aget("/control/status", quiet=True)
        stats = await self.ag.aget("/control/stats", quiet=True)
        filt = await self.ag.aget("/control/filtering/status", quiet=True)
        if status is None or stats is None:
            await itx.followup.send(
                f"⚠️ AdGuard Home injoignable (`{self.bot.cfg.adguard_url}`) — aucune "
                "donnée. Causes vues : CT125 arrêté, règle 106.fw absente, mot de passe.")
            return
        prot = status.get("protection_enabled")
        emb = discord.Embed(
            title="🛡️ DNS maison — AdGuard Home (CT125, 10.3.10.53)",
            color=fmt.GREEN if prot else fmt.RED)
        emb.add_field(name="Protection",
                      value=("🟢 active" if prot else "🔴 DÉSACTIVÉE") +
                            f" · v{status.get('version', '?')}", inline=True)
        emb.add_field(name="Upstream", value="8.8.8.8 / 8.8.4.4 (seuls)", inline=True)
        n, b = stats.get("num_dns_queries", 0), stats.get("num_blocked_filtering", 0)
        pct = (b / n * 100.0) if n else 0.0
        emb.add_field(name="Sur la fenêtre de stats AdGuard (90 j glissants)",
                      value=f"{n} requêtes · {b} bloquées ({pct:.1f} %) · "
                            f"{float(stats.get('avg_processing_time', 0)) * 1000:.1f} ms moy.",
                      inline=False)
        hc = self.hour
        emb.add_field(name=f"Heure en cours (depuis {hc.started.astimezone():%H:%M}, relevé par le bot)",
                      value=f"{hc.total} requêtes · {hc.blocked} bloquées", inline=False)
        if filt is not None:
            lists = [f for f in filt.get("filters", []) if f.get("enabled")]
            rules = sum(int(f.get("rules_count", 0)) for f in lists)
            emb.add_field(name="Listes actives",
                          value="\n".join(f"• {_safe(f.get('name'), 60)} — "
                                          f"{int(f.get('rules_count', 0))} règles"
                                          for f in lists) or "aucune", inline=False)
            emb.add_field(name="Règles utilisateur",
                          value=f"{len([r for r in filt.get('user_rules', []) if r.strip()])} "
                                f"(total listes : {rules})", inline=True)
        emb.add_field(name="Flux Discord",
                      value=("⏸️ en pause (persistant)" if self.paused else
                             f"▶️ sondage {self.bot.cfg.dns_poll_seconds} s · #dns = bilan "
                             f"horaire{' + bloquées' if self.bot.cfg.dns_blocked_feed else ''}"
                             f", pics → #alertes") +
                            (f"\n⚠️ dernier échec : {self.last_error}" if self.last_error else ""),
                      inline=True)
        emb.set_footer(text="Lecture AdGuard Home via son API · #dns = bloquées + bilan horaire")
        await itx.followup.send(embed=emb)

    @dns.command(name="top", description="Top domaines / bloqués / clients (fenêtre AdGuard).")
    @read_check()
    async def dns_top(self, itx: discord.Interaction):
        await itx.response.defer()
        if self._not_configured():
            await itx.followup.send("DNS : non configuré (ADGUARD_USER/ADGUARD_PASS).")
            return
        stats = await self.ag.aget("/control/stats", quiet=True)
        if stats is None:
            await itx.followup.send("⚠️ AdGuard Home injoignable — aucune donnée.")
            return

        def top(key, label=lambda k: _safe(k, 45)):
            rows = []
            for item in (stats.get(key) or [])[:TOP_N]:
                for k, v in item.items():
                    rows.append(f"`{v:>6}` {label(k)}")
            return "\n".join(rows) or "—"
        emb = discord.Embed(title="📊 DNS — tops (fenêtre de stats AdGuard, 90 j)",
                            color=fmt.GREEN)
        emb.add_field(name="Domaines les plus demandés", value=top("top_queried_domains"),
                      inline=False)
        emb.add_field(name="Domaines les plus bloqués", value=top("top_blocked_domains"),
                      inline=False)
        emb.add_field(name="Clients", value=top("top_clients", client_label), inline=False)
        emb.set_footer(text="Limité à 8 par catégorie · détail complet : UI AdGuard (VLAN 10)")
        await itx.followup.send(embed=emb)

    @dns.command(name="pause", description="Met en pause / reprend le flux DNS dans Discord (persistant).")
    @admin_check(require_admin_channel=False)
    async def dns_pause(self, itx: discord.Interaction):
        self.paused = not self.paused
        self.bot.state.set("dns_paused", self.paused)
        await itx.response.send_message(
            ("⏸️ Flux DNS en pause : plus de détail par cycle dans #dns/#journaux-live "
             "(si activés) ; les alertes de pic et le bilan horaire continuent. Persistant."
             if self.paused else
             "▶️ Flux DNS repris (le trafic pendant la pause n'est pas rejoué)."))

    @dns.command(name="bloquer", description="Bloque un domaine (règle utilisateur AdGuard ||domaine^).")
    @app_commands.describe(domaine="Domaine à bloquer, ex. telemetry.example.com")
    @admin_check(require_admin_channel=False)
    async def dns_block(self, itx: discord.Interaction, domaine: str):
        await self._toggle_rule(itx, domaine, True)

    @dns.command(name="debloquer", description="Retire une règle utilisateur ||domaine^ d'AdGuard.")
    @app_commands.describe(domaine="Domaine à débloquer")
    @admin_check(require_admin_channel=False)
    async def dns_unblock(self, itx: discord.Interaction, domaine: str):
        await self._toggle_rule(itx, domaine, False)

    async def _toggle_rule(self, itx, domaine, block):
        await itx.response.defer()
        d = valid_domain(domaine)
        if d is None:
            await itx.followup.send("Domaine invalide (lettres, chiffres, tirets, points).")
            return
        filt = await self.ag.aget("/control/filtering/status", quiet=True)
        if filt is None:
            await itx.followup.send("⚠️ AdGuard Home injoignable — rien modifié.")
            return
        rules, changed = user_rules_toggle(filt.get("user_rules") or [], d, block)
        if not changed:
            await itx.followup.send(f"`{d}` était déjà {'bloqué' if block else 'non bloqué'} "
                                    "par une règle utilisateur — rien modifié.")
            return
        # set_rules répond 200 SANS corps : le client JSON rend None même en succès.
        # On ne juge donc que sur la RELECTURE, jamais sur l'envoi.
        await self.ag.apost("/control/filtering/set_rules", {"rules": rules}, quiet=True)
        after = await self.ag.aget("/control/filtering/status", quiet=True)
        kept = after is not None and ((f"||{d}^" in (after.get("user_rules") or [])) == block)
        verb = "bloqué" if block else "débloqué"
        if kept:
            await itx.followup.send(f"✅ `{d}` {verb} (règle utilisateur AdGuard, effet immédiat). "
                                    f"Règles utilisateur : {len(rules)}.")
            log.info("dns: %s %s par %s", d, verb, itx.user)
        else:
            await itx.followup.send(f"⚠️ Envoi accepté mais la relecture ne montre pas `{d}` "
                                    f"{verb} — vérifier dans l'UI AdGuard.")


async def setup(bot):
    await bot.add_cog(Dns(bot))
