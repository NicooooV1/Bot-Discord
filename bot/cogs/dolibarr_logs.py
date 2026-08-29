"""Journal Dolibarr (CT108 `dolibarr`, doli.nicov1.fr) dans #doli-logs — catégorie 🔒 Lock,
propriétaire uniquement (demande Nico 2026-08-29 : « un salon log complet des logs
dolibarr sur discord »). Dolibarr contient des données de gestion (clients, factures) :
le salon est rangé dans Lock comme #jelly-logs, jamais dans Supervision.

D'OÙ VIENNENT LES LIGNES
------------------------
Le CT108 n'expose rien : ses journaux partent par Alloy (/etc/alloy/20-dolibarr.alloy)
vers Loki (CT117) sous `{job="dolibarr", host="dolibarr"}`, l'unité disant la source :
  • unit="dolibarr"      : /var/log/dolibarr/dolibarr.log — module Syslog de Dolibarr,
                           niveau INFO (accès aux pages, connexions, erreurs SQL…) ;
  • unit="dolibarr-cron" : /var/log/dolibarr_cron.log — un bloc par passage de
                           cron_run_jobs.php (toutes les 5 min) ;
  • unit="apache-error"  : dolibarr_error.log — PHP Fatal, 403/404 côté apache ;
  • unit="apache-access" : dolibarr_access.log — une ligne par requête HTTP.
Le cog interroge Loki toutes les DOLIBARR_LOGS_POLL_SECONDS (défaut 30 s) DEPUIS SON
CURSEUR (horodatage ns de la dernière ligne publiée, persisté dans state.json) — en
ordre chronologique, ce qui rend le rattrapage sans perte : si un cycle ramène
FETCH_LIMIT lignes, le suivant reprend là où il s'est arrêté.

CE QUI EST PUBLIÉ (« complet » côté application, sans bruit mécanique)
----------------------------------------------------------------------
  • journal applicatif : TOUTES les lignes ≥ NOTICE, plus les INFO qui disent quelque
    chose (session ouverte, mail, document…) ; sont TUES les INFO purement mécaniques
    d'un rendu de page (NOISE_INFO : « box_x::showBox », « DolGraph::draw_chart »,
    lectures de cache, « checkLoginPassEntity »…, ≈25 lignes par page d'accueil
    mesurées le 29/08) et les « --- End access to … » (le « --- Access to » suffit) ;
  • apache-error : toutes les lignes ;
  • apache-access : seulement les réponses ≥ 400 (les 2xx/3xx doublonnent l'applicatif) ;
  • cron : seulement les passages en ÉCHEC (PHP Fatal, job KO). Un passage OK toutes
    les 5 minutes ferait 288 lignes/jour pour dire « rien ».
Plusieurs lignes d'une même entrée (stack trace, bloc cron) sont regroupées côté Alloy ;
ici on affiche la première ligne, puis jusqu'à TRACE_LINES lignes suivantes en bloc de
code pour les niveaux ≥ err, et « ⤷ +N lignes » sinon.

GARDE-FOUS
----------
  • MAX_PER_CYCLE lignes publiées par cycle. Si Loki en renvoie FETCH_LIMIT d'un coup
    (boucle PHP, scan), le cog publie le début, puis UN bilan par niveau du reste et
    avance le curseur : Discord n'est pas un puits sans fond, Loki/Grafana gardent tout.
  • Le curseur n'avance qu'après un envoi réussi : salon injoignable = rejoué au cycle
    suivant. Premier démarrage : on se cale sur le présent, sans rejouer l'historique.
  • Texte venu d'un journal = donnée non fiable (URL forgée par un visiteur) : backticks
    neutralisés, mentions désactivées, longueur bornée.
  • #alertes (edge-trigger, espace « dolibarr ») sur PHP Fatal / cron KO / HTTP 5xx ;
    « ✅ Résolu » après RESOLVE_AFTER_MIN minutes sans nouvel incident. Les mots restent
    factuels : le bot rapporte une ligne de journal, il n'interprète pas.

CE QUE CE COG NE FAIT PAS : il ne crée aucun salon (provision._provision_dolibarr_log_channel)
et ne touche jamais à Dolibarr — lecture seule de Loki.
"""
import datetime as dt
import logging
import re
import time
from collections import Counter

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..views.alertaction import alert_snoozed

log = logging.getLogger("discord-bot.dolibarrlogs")

LOGQL = '{job="dolibarr", host="dolibarr"}'
FETCH_LIMIT = 400             # lignes max ramenées par cycle (ordre chronologique)
MAX_PER_CYCLE = 40            # lignes max PUBLIÉES par cycle
LINE_MAX = 320                # longueur max d'une ligne publiée
TRACE_LINES = 6               # lignes de suite affichées pour une entrée ≥ err
DISCORD_CHUNK = 1900
RESOLVE_AFTER_MIN = 60
ALERT_KEY = "dolibarr_incident"

LEVEL_ICON = {"emerg": "🔥", "alert": "🔥", "crit": "🔥", "err": "🔴",
              "warning": "🟠", "notice": "🔵", "info": "▫️", "debug": "▪️"}
SEVERE = frozenset({"emerg", "alert", "crit", "err"})
# INFO mécaniques d'un rendu de page (mesuré 2026-08-29 : ~25 lignes par page d'accueil).
NOISE_INFO = ("::showBox", "DolGraph::", "Stats::get", "checkLoginPassEntity",
              "functions_http::check_user_password_http", "Save lastsearch_values",
              "read data from cache file", "ModeleBoxes::", "::loadRights", "Conf::setValues",
              "::fetch ", "::fetch_", "::getNomUrl", "::_load_ldap", "run_jobs.php search qualified")


_SESSION_ID = re.compile(r"(Session id=)\S+")


# ============================================================ fonctions pures (testées)

def _safe(s, n=LINE_MAX):
    """Texte venu d'un journal : pas de backtick, pas de saut de ligne, borné."""
    s = str(s or "").replace("`", "'").replace("\r", "").strip()
    s = _SESSION_ID.sub(r"\1…", s)            # jamais un id de session dans Discord
    return s if len(s) <= n else s[: n - 1] + "…"


def strip_app_prefix(line):
    """« 2026-08-29 07:14:12 NOTICE  127.0.0.1  8284  33 --- Access to … »
    -> (« NOTICE », « 127.0.0.1 », « --- Access to … »). Ligne inconnue -> (None, None, ligne)."""
    parts = line.split(None, 6)
    # date heure NIVEAU ip pid idx message
    if len(parts) >= 6 and len(parts[0]) == 10 and parts[0][4] == "-" and parts[2].isupper() \
            and parts[4].isdigit():
        return parts[2], parts[3], (parts[6] if len(parts) == 7 else "")
    return None, None, line


def strip_apache_prefix(line):
    """« [Sat Aug 29 …] [php:error] [pid 12] [client 1.2.3.4:5] PHP Fatal … »
    -> « php:error · client 1.2.3.4 · PHP Fatal … »."""
    tags = []
    rest = line
    while rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            break
        tags.append(rest[1:end])
        rest = rest[end + 1:].lstrip()
    if not tags:
        return line
    keep = []
    for t in tags[1:]:                       # tags[0] = date apache
        if t.startswith("pid "):
            continue
        if t.startswith("client "):
            t = "client " + t[7:].rsplit(":", 1)[0]
        keep.append(t)
    return (" · ".join(keep) + " · " + rest) if keep else rest


def wanted(unit, level, line):
    """Filtre de publication (cf. docstring du module)."""
    if unit == "dolibarr":
        first = line.split("\n", 1)[0]
        if "--- End access to" in first:
            return False
        if level == "info" and any(p in first for p in NOISE_INFO):
            return False
        return True
    if unit == "dolibarr-cron":
        return level in SEVERE
    if unit == "apache-access":
        return level in SEVERE or level == "warning"
    return True                               # apache-error : tout


def render(ts, unit, level, line):
    """Une entrée Loki -> texte Discord (1 ou plusieurs lignes)."""
    when = dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone().strftime("%H:%M:%S")
    icon = LEVEL_ICON.get(level, "•")
    first, _, rest = line.partition("\n")
    extra = [ln for ln in rest.split("\n") if ln.strip()] if rest else []
    if unit == "dolibarr":
        lvl, ip, msg = strip_app_prefix(first)
        head = msg
        if ip and ip not in ("127.0.0.1", "-"):
            head = f"[{ip}] {msg}"
    elif unit == "apache-error":
        head = strip_apache_prefix(first)
    elif unit == "dolibarr-cron":
        # le bloc commence par la bannière « ***** cron_run_jobs.php (24.0.0) pid=… » :
        # on met en tête la première ligne parlante (Fatal/KO), la bannière suit
        bad = next((ln for ln in [first] + extra
                    if "PHP " in ln or " KO" in ln or "error" in ln.lower()), first)
        head = bad
        extra = [ln for ln in [first] + extra if ln is not bad]
    else:
        head = first
    tag = {"dolibarr": "app", "dolibarr-cron": "cron", "apache-error": "apache",
           "apache-access": "http"}.get(unit, unit)
    out = f"{icon} `{when}` **{tag}** {_safe(head)}"
    if extra:
        if level in SEVERE or unit == "dolibarr-cron":
            body = "\n".join(_safe(ln, 200) for ln in extra[:TRACE_LINES])
            more = len(extra) - TRACE_LINES
            out += f"\n```\n{body}\n```" + (f"⤷ +{more} ligne(s)" if more > 0 else "")
        else:
            out += f" ⤷ +{len(extra)} ligne(s)"
    return out


def overflow_digest(entries):
    """Bilan d'un lot non publié : « ⚠️ 360 lignes non publiées (err 2, warning 8, info 350) »."""
    c = Counter(lv for _, _, lv, _ in entries)
    parts = ", ".join(f"{k} {c[k]}" for k in ("emerg", "alert", "crit", "err", "warning",
                                              "notice", "info", "debug") if c.get(k))
    return (f"⚠️ {len(entries)} ligne(s) non publiée(s) ici (afflux) — {parts}. "
            "Tout est dans Loki/Grafana (job dolibarr).")


def chunk(blocks, limit=DISCORD_CHUNK):
    """Regroupe des blocs de texte en messages ≤ limit (un bloc n'est jamais coupé
    au milieu, sauf s'il dépasse seul la limite)."""
    out, cur = [], ""
    for b in blocks:
        b = b[:limit]
        if cur and len(cur) + len(b) + 1 > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{b}" if cur else b
    if cur:
        out.append(cur)
    return out


def incidents(entries):
    """Entrées qui justifient #alertes : (titre, description) ou None."""
    hits = []
    for _, unit, level, line in entries:
        first = line.split("\n", 1)[0]
        if unit == "dolibarr-cron" and level in SEVERE:
            hits.append("cron Dolibarr en échec")
        elif unit == "apache-error" and level in SEVERE and "PHP Fatal" in line:
            hits.append("PHP Fatal error (apache)")
        elif unit == "apache-access" and level in SEVERE:
            hits.append("réponse HTTP 5xx")
        elif unit == "dolibarr" and level in {"emerg", "alert", "crit"}:
            hits.append(f"journal Dolibarr niveau {level}")
        else:
            continue
        hits[-1] = (hits[-1], _safe(first, 160))
    if not hits:
        return None
    kinds = Counter(k for k, _ in hits)
    title = "🔴 Dolibarr : " + ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in kinds.items())
    desc = "\n".join(f"• {d}" for _, d in hits[:5])
    if len(hits) > 5:
        desc += f"\n… +{len(hits) - 5}"
    return title, desc


# ============================================================ le cog

class DolibarrLogs(commands.Cog):
    """Flux des journaux Dolibarr (Loki) vers #doli-logs, incidents vers #alertes."""

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        prov = bot.state.get("prov", {}) or {}
        self.channel_id = prov.get("dolibarr_logs") or self.cfg.dolibarr_logs_channel_id
        self._cursor_ns = bot.state.get("dolibarr_logs_cursor_ns")
        self._alerts = bot.state.ns("dolibarr")
        self._last_incident = bot.state.get("dolibarr_last_incident_ts")
        self._warned_no_channel = False
        self.last_error = None

    @property
    def enabled(self):
        return bool(self.cfg.dolibarr_logs_enabled and self.bot.loki.enabled)

    async def cog_load(self):
        if not self.enabled:
            log.warning("dolibarr_logs: inactif (DOLIBARR_LOGS_ENABLED=false ou LOKI_URL vide)")
            return
        self.poll.change_interval(seconds=self.cfg.dolibarr_logs_poll_seconds)
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    async def _channel(self, cid, what):
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("dolibarr_logs: salon %s (%s) injoignable: %s", cid, what, e)
                return None
        return ch

    # ------------------------------------------------------------------ boucle

    @tasks.loop(seconds=30)
    async def poll(self):
        st = self.bot.state
        if self._cursor_ns is None:
            # premier démarrage : présent, sans rejouer l'historique Loki
            self._cursor_ns = int(time.time() * 1e9)
            st.set("dolibarr_logs_cursor_ns", self._cursor_ns)
            log.info("dolibarr_logs: curseur initialisé au présent")
            return
        rows = await self.bot.loki.query_since(LOGQL, self._cursor_ns + 1, limit=FETCH_LIMIT)
        if rows is None:
            self.last_error = f"Loki injoignable à {dt.datetime.now().strftime('%H:%M:%S')}"
            return
        self.last_error = None
        if not rows:
            await self._maybe_resolve()
            return
        entries = [(ts, lab.get("unit", "?"), lab.get("level", "info"), line)
                   for ts, lab, line in rows]
        last_ns = int(rows[-1][0] * 1e9)
        flood = len(rows) >= FETCH_LIMIT

        # -- #alertes (edge-trigger) -------------------------------------------------
        inc = incidents(entries)
        if inc is not None:
            await self._fire(*inc)

        # -- #doli-logs ---------------------------------------------------------------
        ch = await self._channel(self.channel_id, "#doli-logs")
        if ch is None:
            if not self._warned_no_channel:
                log.warning("dolibarr_logs: aucun salon #doli-logs (provisioning non fait "
                            "ou DOLIBARR_LOGS_CHANNEL_ID vide) — flux inactif, curseur figé")
                self._warned_no_channel = True
            return
        self._warned_no_channel = False
        keep = [(ts, u, lv, ln) for ts, u, lv, ln in entries if wanted(u, lv, ln)]
        blocks = [render(ts, u, lv, ln) for ts, u, lv, ln in keep[:MAX_PER_CYCLE]]
        rest = keep[MAX_PER_CYCLE:]
        if rest and not flood:
            # pas un afflux : on publie ce qu'on peut et on reprend le reste au cycle
            # suivant (le curseur s'arrête sur la dernière ligne PUBLIÉE ou filtrée)
            last_ns = int(keep[MAX_PER_CYCLE - 1][0] * 1e9)
        elif rest:
            blocks.append(overflow_digest(rest))
        try:
            for msg in chunk(blocks):
                await ch.send(msg[:2000], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("dolibarr_logs: envoi vers #doli-logs échoué — rejoué au cycle suivant",
                        exc_info=True)
            return
        # curseur avancé seulement après envoi réussi
        self._cursor_ns = last_ns
        st.set("dolibarr_logs_cursor_ns", self._cursor_ns)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ alertes

    async def _fire(self, title, desc):
        self._last_incident = time.time()
        self.bot.state.set("dolibarr_last_incident_ts", self._last_incident)
        if self._alerts.level(ALERT_KEY) or alert_snoozed(self.bot.state, ALERT_KEY):
            return                                # déjà signalé : pas de répétition
        ch = await self._channel(self.cfg.alert_channel_id, "#alertes")
        if ch is None:
            return
        emb = discord.Embed(title=title, description=desc, color=fmt.RED)
        emb.set_footer(text=f"alerte: {ALERT_KEY} [crit] — détail dans #doli-logs")
        await ch.send(embed=emb, allowed_mentions=discord.AllowedMentions.none())
        self._alerts.set_level(ALERT_KEY, "crit")

    async def _maybe_resolve(self):
        if not self._alerts.level(ALERT_KEY):
            return
        last = self._last_incident or 0
        if time.time() - last < RESOLVE_AFTER_MIN * 60:
            return
        ch = await self._channel(self.cfg.alert_channel_id, "#alertes")
        if ch is not None and not alert_snoozed(self.bot.state, ALERT_KEY):
            await ch.send(embed=discord.Embed(
                title="✅ Résolu — Dolibarr : incident",
                description=f"Aucune nouvelle erreur depuis {RESOLVE_AFTER_MIN} min.",
                color=fmt.GREEN))
        self._alerts.clear(ALERT_KEY)


async def setup(bot):
    await bot.add_cog(DolibarrLogs(bot))
