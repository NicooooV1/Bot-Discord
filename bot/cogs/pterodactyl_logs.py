"""Journaux Pterodactyl (panel CT101 + Wings CT100) dans #ptero-logs — catégorie 🔒 Lock,
propriétaire uniquement (demande Nico 2026-08-29 : « met les logs de ptero sur discord via
le bot »). Même modèle que `dolibarr_logs` (#doli-logs) : lecture seule de Loki, curseur
persistant, incidents → #alertes. Le panel gère des comptes et des serveurs de jeu : Lock,
pas Supervision.

D'OÙ VIENNENT LES LIGNES
------------------------
  • panel (CT101, Alloy /etc/alloy/20-pterodactyl.alloy) : {job="pterodactyl", host="pterodactyl-panel"}
      unit="laravel"       : storage/logs/laravel.log — « [date] production.LEVEL: message {json} »
                             (+ stack trace regroupée côté Alloy) ;
      unit="apache-error"  : pterodactyl_error.log — PHP Fatal, 403/404 apache ;
      unit="apache-access" : pterodactyl_access.log — une ligne par requête HTTP.
  • Wings (CT100) : aucun drop-in, son journal part déjà par journald :
      {job="systemd-journal", host="pterodactyl-wings", unit="wings.service"} — lignes
      «  INFO: [Aug 29 16:34:22.945] message key=value… » ; le niveau du label Loki est la
      priorité journald (toujours info), le VRAI niveau est le préfixe texte (INFO/WARN/ERROR/FATAL).
Une seule requête Loki couvre les deux hôtes (sélecteur sur host + unit).

CE QUI EST PUBLIÉ
-----------------
  • laravel : tout sauf debug (LOG_LEVEL=debug côté panel) ; le contexte JSON
    « {"exception": …} » est réduit à la classe d'exception ;
  • wings : WARN/ERROR/FATAL toujours ; INFO seulement les événements qui comptent
    (action power start/stop/restart/kill, installation, sauvegarde, transfert, SFTP,
    démarrage de Wings) — le reste (locks, polling, sync) est du bruit mécanique ;
  • apache-error : tout ; apache-access : seulement ≥ 400.
GARDE-FOUS : identiques à dolibarr_logs (MAX_PER_CYCLE, bilan d'afflux, curseur avancé
après envoi réussi, texte de journal neutralisé, mentions désactivées).
#alertes (edge-trigger, espace « pterodactyl ») : Laravel ≥ err, Wings ERROR/FATAL,
HTTP 5xx, PHP Fatal ; « ✅ Résolu » après RESOLVE_AFTER_MIN min sans nouvel incident.

CE QUE CE COG NE FAIT PAS : il ne crée aucun salon (provision._provision_pterodactyl_log_channel)
et ne touche ni au panel ni à Wings — lecture seule de Loki.
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

log = logging.getLogger("discord-bot.pterologs")

LOGQL = '{host=~"pterodactyl-(panel|wings)", unit=~"laravel|apache-error|apache-access|wings\\\\.service"}'
FETCH_LIMIT = 400
MAX_PER_CYCLE = 40
LINE_MAX = 320
TRACE_LINES = 6
DISCORD_CHUNK = 1900
RESOLVE_AFTER_MIN = 60
ALERT_KEY = "pterodactyl_incident"

LEVEL_ICON = {"emerg": "🔥", "alert": "🔥", "crit": "🔥", "err": "🔴",
              "warning": "🟠", "notice": "🔵", "info": "▫️", "debug": "▪️"}
SEVERE = frozenset({"emerg", "alert", "crit", "err"})
WINGS_LEVEL = {"DEBUG": "debug", "INFO": "info", "WARN": "warning", "ERROR": "err", "FATAL": "crit"}
# INFO Wings qui méritent Discord (le reste = mécanique : locks, polling, sync, preflight).
WINGS_KEEP_INFO = ("processing event... action=", "completed server preflight",
                   "server marked as", "installation", "reinstall", "backup", "transfer",
                   "sftp server listening", "configuring internal webserver",
                   "authentication", "crash", "deleting server", "deleted server",
                   "exceeded", "killed", "stopped")
_WINGS = re.compile(r"^\s*(?P<lvl>DEBUG|INFO|WARN|ERROR|FATAL):\s*\[[^\]]*\]\s*(?P<msg>.*)$")
_LARAVEL = re.compile(r"^\[[^\]]+\] (?P<env>\w+)\.(?P<lvl>[A-Z]+): (?P<msg>.*)$")
_EXC = re.compile(r'\{"exception":"\[object\] \(([^(]+)\(code: \d+\): ([^"]{0,160})')
_TOKENS = re.compile(r"(token|password|secret|key)=\S+", re.I)


# ============================================================ fonctions pures (testées)

def _safe(s, n=LINE_MAX):
    """Texte venu d'un journal : pas de backtick, pas de saut de ligne, secrets masqués, borné."""
    s = str(s or "").replace("`", "'").replace("\r", "").strip()
    s = _TOKENS.sub(r"\1=…", s)
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_wings(line):
    """«  INFO: [Aug 29 16:34:22.945] syncing server… server=b62… » -> ("info", "syncing server… server=b62…").
    Ligne inconnue -> ("info", ligne)."""
    m = _WINGS.match(line)
    if not m:
        return "info", line.strip()
    return WINGS_LEVEL.get(m.group("lvl"), "info"), m.group("msg").strip()


def parse_laravel(line):
    """« [2026-08-29 14:28:39] production.ERROR: Array to string conversion {"exception":"[object]
    (ErrorException(code: 0): Array to string conversion at /var/www/x.php:3)…} »
    -> ("err", "Array to string conversion — ErrorException: Array to string conversion at /var/www/x.php:3").
    Ligne inconnue -> (None, ligne)."""
    m = _LARAVEL.match(line)
    if not m:
        return None, line
    lvl = {"DEBUG": "debug", "INFO": "info", "NOTICE": "notice", "WARNING": "warning",
           "ERROR": "err", "CRITICAL": "crit", "ALERT": "alert",
           "EMERGENCY": "emerg"}.get(m.group("lvl"), "info")
    msg = m.group("msg")
    head, sep, ctx = msg.partition(' {"exception"')
    if sep:
        e = _EXC.search(sep + ctx)
        if e:
            head = f"{head.strip()} — {e.group(1).rsplit(chr(92), 1)[-1]}: {e.group(2)}"
    else:
        head, _, _ = msg.partition(" {")       # contexte JSON sans exception : on le tait
    return lvl, head.strip()


def strip_apache_prefix(line):
    """« [Sat Aug 29 …] [php:error] [pid 12] [client 1.2.3.4:5] PHP Fatal … »
    -> « php:error · client 1.2.3.4 · PHP Fatal … »."""
    tags, rest = [], line
    while rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            break
        tags.append(rest[1:end])
        rest = rest[end + 1:].lstrip()
    if not tags:
        return line
    keep = []
    for t in tags[1:]:
        if t.startswith("pid "):
            continue
        if t.startswith("client "):
            t = "client " + t[7:].rsplit(":", 1)[0]
        keep.append(t)
    return (" · ".join(keep) + " · " + rest) if keep else rest


def effective_level(unit, label_level, line):
    """Niveau réel d'une entrée : pour Wings, le préfixe texte prime sur la priorité journald."""
    if unit == "wings.service":
        return parse_wings(line.split("\n", 1)[0])[0]
    return label_level


def wanted(unit, level, line):
    """Filtre de publication (cf. docstring du module)."""
    first = line.split("\n", 1)[0]
    if unit == "laravel":
        return level != "debug"
    if unit == "wings.service":
        if level in SEVERE or level == "warning":
            return True
        msg = parse_wings(first)[1].lower()
        return any(k in msg for k in WINGS_KEEP_INFO)
    if unit == "apache-access":
        return level in SEVERE or level == "warning"
    return True                               # apache-error : tout


def render(ts, unit, level, line):
    """Une entrée Loki -> texte Discord (1 ou plusieurs lignes)."""
    when = dt.datetime.fromtimestamp(ts, dt.timezone.utc).astimezone().strftime("%H:%M:%S")
    icon = LEVEL_ICON.get(level, "•")
    first, _, rest = line.partition("\n")
    extra = [ln for ln in rest.split("\n") if ln.strip()] if rest else []
    if unit == "laravel":
        _, head = parse_laravel(first)
        extra = [ln for ln in extra if not ln.startswith("[stacktrace]")]
    elif unit == "wings.service":
        _, head = parse_wings(first)
    elif unit == "apache-error":
        head = strip_apache_prefix(first)
    else:
        head = first
    tag = {"laravel": "panel", "wings.service": "wings", "apache-error": "apache",
           "apache-access": "http"}.get(unit, unit)
    out = f"{icon} `{when}` **{tag}** {_safe(head)}"
    if extra:
        if level in SEVERE:
            body = "\n".join(_safe(ln, 200) for ln in extra[:TRACE_LINES])
            more = len(extra) - TRACE_LINES
            out += f"\n```\n{body}\n```" + (f"⤷ +{more} ligne(s)" if more > 0 else "")
        else:
            out += f" ⤷ +{len(extra)} ligne(s)"
    return out


def overflow_digest(entries):
    c = Counter(lv for _, _, lv, _ in entries)
    parts = ", ".join(f"{k} {c[k]}" for k in ("emerg", "alert", "crit", "err", "warning",
                                              "notice", "info", "debug") if c.get(k))
    return (f"⚠️ {len(entries)} ligne(s) non publiée(s) ici (afflux) — {parts}. "
            "Tout est dans Loki/Grafana (hosts pterodactyl-panel / pterodactyl-wings).")


def chunk(blocks, limit=DISCORD_CHUNK):
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
        if unit == "laravel" and level in SEVERE:
            hits.append((f"panel : erreur Laravel ({level})", _safe(parse_laravel(first)[1], 160)))
        elif unit == "wings.service" and level in SEVERE:
            hits.append(("Wings : " + ("FATAL" if level == "crit" else "ERROR"),
                         _safe(parse_wings(first)[1], 160)))
        elif unit == "apache-error" and level in SEVERE and "PHP Fatal" in line:
            hits.append(("PHP Fatal error (apache)", _safe(strip_apache_prefix(first), 160)))
        elif unit == "apache-access" and level in SEVERE:
            hits.append(("réponse HTTP 5xx", _safe(first, 160)))
    if not hits:
        return None
    kinds = Counter(k for k, _ in hits)
    title = "🔴 Pterodactyl : " + ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in kinds.items())
    desc = "\n".join(f"• {d}" for _, d in hits[:5])
    if len(hits) > 5:
        desc += f"\n… +{len(hits) - 5}"
    return title, desc


# ============================================================ le cog

class PterodactylLogs(commands.Cog):
    """Flux des journaux Pterodactyl (Loki) vers #ptero-logs, incidents vers #alertes."""

    def __init__(self, bot):
        self.bot = bot
        self.cfg = bot.cfg
        prov = bot.state.get("prov", {}) or {}
        self.channel_id = prov.get("pterodactyl_logs") or self.cfg.pterodactyl_logs_channel_id
        self._cursor_ns = bot.state.get("pterodactyl_logs_cursor_ns")
        self._alerts = bot.state.ns("pterodactyl")
        self._last_incident = bot.state.get("pterodactyl_last_incident_ts")
        self._warned_no_channel = False
        self.last_error = None

    @property
    def enabled(self):
        return bool(self.cfg.pterodactyl_logs_enabled and self.bot.loki.enabled)

    async def cog_load(self):
        if not self.enabled:
            log.warning("pterodactyl_logs: inactif (PTERODACTYL_LOGS_ENABLED=false ou LOKI_URL vide)")
            return
        self.poll.change_interval(seconds=self.cfg.pterodactyl_logs_poll_seconds)
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
                log.warning("pterodactyl_logs: salon %s (%s) injoignable: %s", cid, what, e)
                return None
        return ch

    # ------------------------------------------------------------------ boucle

    @tasks.loop(seconds=30)
    async def poll(self):
        st = self.bot.state
        if self._cursor_ns is None:
            self._cursor_ns = int(time.time() * 1e9)
            st.set("pterodactyl_logs_cursor_ns", self._cursor_ns)
            log.info("pterodactyl_logs: curseur initialisé au présent")
            return
        rows = await self.bot.loki.query_since(LOGQL, self._cursor_ns + 1, limit=FETCH_LIMIT)
        if rows is None:
            self.last_error = f"Loki injoignable à {dt.datetime.now().strftime('%H:%M:%S')}"
            return
        self.last_error = None
        if not rows:
            await self._maybe_resolve()
            return
        entries = []
        for ts, lab, line in rows:
            unit = lab.get("unit", "?")
            entries.append((ts, unit, effective_level(unit, lab.get("level", "info"), line), line))
        last_ns = int(rows[-1][0] * 1e9)
        flood = len(rows) >= FETCH_LIMIT

        inc = incidents(entries)
        if inc is not None:
            await self._fire(*inc)

        ch = await self._channel(self.channel_id, "#ptero-logs")
        if ch is None:
            if not self._warned_no_channel:
                log.warning("pterodactyl_logs: aucun salon #ptero-logs (provisioning non fait "
                            "ou PTERODACTYL_LOGS_CHANNEL_ID vide) — flux inactif, curseur figé")
                self._warned_no_channel = True
            return
        self._warned_no_channel = False
        keep = [(ts, u, lv, ln) for ts, u, lv, ln in entries if wanted(u, lv, ln)]
        blocks = [render(ts, u, lv, ln) for ts, u, lv, ln in keep[:MAX_PER_CYCLE]]
        rest = keep[MAX_PER_CYCLE:]
        if rest and not flood:
            last_ns = int(keep[MAX_PER_CYCLE - 1][0] * 1e9)
        elif rest:
            blocks.append(overflow_digest(rest))
        try:
            for msg in chunk(blocks):
                await ch.send(msg[:2000], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("pterodactyl_logs: envoi vers #ptero-logs échoué — rejoué au cycle suivant",
                        exc_info=True)
            return
        self._cursor_ns = last_ns
        st.set("pterodactyl_logs_cursor_ns", self._cursor_ns)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ alertes

    async def _fire(self, title, desc):
        self._last_incident = time.time()
        self.bot.state.set("pterodactyl_last_incident_ts", self._last_incident)
        if self._alerts.level(ALERT_KEY) or alert_snoozed(self.bot.state, ALERT_KEY):
            return
        ch = await self._channel(self.cfg.alert_channel_id, "#alertes")
        if ch is None:
            return
        emb = discord.Embed(title=title, description=desc, color=fmt.RED)
        emb.set_footer(text=f"alerte: {ALERT_KEY} [crit] — détail dans #ptero-logs")
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
                title="✅ Résolu — Pterodactyl : incident",
                description=f"Aucune nouvelle erreur depuis {RESOLVE_AFTER_MIN} min.",
                color=fmt.GREEN))
        self._alerts.clear(ALERT_KEY)


async def setup(bot):
    await bot.add_cog(PterodactylLogs(bot))
