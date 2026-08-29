"""Relais des alertes Grafana → Discord, PAR LE BOT (plus aucun webhook côté Grafana).

Demande Nico 2026-08-29 : « Je ne veux pas de webhook grafana, ils sont reformés par le
bot discord ». Jusque-là Grafana postait lui-même dans #alertes via un contact point
Discord (webhook) ; ce webhook est mort le 30/07 (404 Unknown Webhook) et personne ne l'a
su pendant 30 jours : 4 949 notifications perdues, dont « RAID BBU dégradée ».

CE QUE FAIT CE COG
------------------
1. `poll` (GRAFANA_POLL_SECONDS, défaut 60 s) : lit les alertes ACTIVES de l'alertmanager
   intégré de Grafana (`GET /api/alertmanager/grafana/api/v2/alerts`, token de compte de
   service « edmine », rôle Viewer = lecture seule) et compare à l'état persisté :
     - nouvelle empreinte (fingerprint)  → embed 🔴/🟠 dans le salon des alertes ;
     - empreinte disparue                → embed ✅ Résolu, clé effacée.
   L'état vit dans state.json, espace « grafana » (une clé par fingerprint) : un
   redémarrage du bot ne re-poste PAS ce qui est déjà en cours, et une alerte résolue
   pendant l'arrêt poste bien son ✅ au premier cycle.
2. Grafana injoignable : on ne résout RIEN (une API muette n'est pas une alerte guérie),
   on poste UNE fois « ⚠️ Grafana injoignable » (edge-trigger, clé `_unreachable`) et
   son ✅ quand ça revient. C'est la surveillance de la chaîne elle-même, qui manquait.
3. `/grafana` : liste les alertes actives (lecture seule).

CE QUE CE COG NE FAIT PAS : il ne re-poste pas une alerte qui reste active (pas de
repeat_interval — le salon montre l'alerte une fois, /grafana montre l'état courant).
Les silences Grafana sont respectés (état « suppressed » = ignoré, et une alerte qui
passe en silence est traitée comme résolue côté salon).
"""
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.http import ApiClient
from ..core.permissions import read_check
from ..views.alertaction import alert_snoozed

log = logging.getLogger("discord-bot.grafana_alerts")

ALERTS_PATH = "/api/alertmanager/grafana/api/v2/alerts"
MAX_POSTS_PER_CYCLE = 8          # anti-rafale : le reste part au cycle suivant
UNREACHABLE_KEY = "_unreachable"
DESC_MAX = 900

_SEV_COLOR = {"critical": fmt.RED, "crit": fmt.RED, "error": fmt.RED,
              "warning": fmt.ORANGE, "warn": fmt.ORANGE,
              "info": fmt.YELLOW, "none": fmt.GREY}
_SEV_ICON = {"critical": "🔴", "crit": "🔴", "error": "🔴",
             "warning": "🟠", "warn": "🟠", "info": "🔵", "none": "⚪"}


# ============================================================================ pur

def _clean(s, limit=DESC_MAX):
    """Texte venu de Grafana → sûr pour un embed : pas de mention, pas de bloc de code
    ouvert, longueur bornée."""
    s = str(s or "").replace("@", "@​").replace("```", "'''").strip()
    return (s[: limit - 1] + "…") if len(s) > limit else s


def parse_alerts(payload):
    """Liste brute de l'alertmanager → dict {fingerprint: alerte normalisée}.

    Ne garde que les alertes réellement ACTIVES (état « active ») : « suppressed »
    (silence/inhibition) et « unprocessed » sont ignorées. Une entrée sans
    fingerprint ou sans alertname est ignorée en le disant.
    """
    out = {}
    if not isinstance(payload, list):
        return out
    for a in payload:
        try:
            labels = a.get("labels") or {}
            ann = a.get("annotations") or {}
            fp = str(a.get("fingerprint") or "")
            name = labels.get("alertname")
            if not fp or not name:
                log.info("grafana: alerte ignorée (sans fingerprint/alertname): %r", a)
                continue
            if ((a.get("status") or {}).get("state") or "active") != "active":
                continue
            out[fp] = {
                "fp": fp,
                "name": str(name),
                "severity": str(labels.get("severity") or "none").lower(),
                "folder": str(labels.get("grafana_folder") or ""),
                "host": str(labels.get("host") or labels.get("instance") or ""),
                "summary": str(ann.get("summary") or ""),
                "description": str(ann.get("description") or ""),
                "url": str(a.get("generatorURL") or ""),
                "starts": str(a.get("startsAt") or ""),
            }
        except (AttributeError, TypeError) as e:
            log.info("grafana: alerte illisible ignorée (%s): %r", e, a)
    return out


def diff(known_fps, current):
    """(nouvelles, résolues) — nouvelles = dans `current` mais pas connues,
    résolues = connues mais plus dans `current`. Ordre stable (tri par nom)."""
    known = set(known_fps)
    new = sorted((a for fp, a in current.items() if fp not in known),
                 key=lambda a: (a["severity"] != "critical", a["name"]))
    resolved = sorted(fp for fp in known if fp not in current)
    return new, resolved


def _since(starts):
    """« depuis 12 min » à partir d'un horodatage RFC3339 ; '' si illisible."""
    try:
        import datetime as dt
        s = starts.replace("Z", "+00:00")
        # Grafana peut donner 9 décimales : Python n'en accepte que 6.
        if "." in s:
            head, tail = s.split(".", 1)
            frac = "".join(ch for ch in tail if ch.isdigit())[:6]
            tz = tail[len("".join(ch for ch in tail if ch.isdigit())):]
            s = f"{head}.{frac.ljust(6, '0')}{tz}"
        t = dt.datetime.fromisoformat(s)
        secs = max(0, int(time.time() - t.timestamp()))
        return fmt.duration(secs) if hasattr(fmt, "duration") else f"{secs // 60} min"
    except (ValueError, AttributeError):
        return ""


def firing_embed(a):
    sev = a["severity"]
    title = f"{_SEV_ICON.get(sev, '🟠')} {_clean(a['name'], 200)}"
    body = _clean(a["description"] or a["summary"]) or "_(pas de description)_"
    emb = discord.Embed(title=title, description=body, color=_SEV_COLOR.get(sev, fmt.ORANGE))
    if a["host"]:
        emb.add_field(name="Hôte", value=f"`{_clean(a['host'], 80)}`", inline=True)
    if sev != "none":
        emb.add_field(name="Sévérité", value=f"`{sev}`", inline=True)
    since = _since(a["starts"])
    if since:
        emb.add_field(name="Depuis", value=since, inline=True)
    foot = "Grafana"
    if a["folder"]:
        foot += f" · {a['folder']}"
    emb.set_footer(text=f"{foot} · relais Edmine · fp {a['fp'][:12]}")
    return emb


def resolved_embed(a):
    return discord.Embed(
        title=f"✅ Résolu — {_clean(a.get('name', '?'), 200)}",
        description=(f"`{_clean(a['host'], 80)}` · " if a.get("host") else "")
        + "l'alerte n'est plus active dans Grafana.",
        color=fmt.GREEN)


# ============================================================================ cog

class GrafanaAlerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cfg = bot.cfg
        self.api = ApiClient(cfg.grafana_url, {"Authorization": f"Bearer {cfg.grafana_token}"},
                             timeout=10, label="grafana")
        self._space = bot.state.ns("grafana")
        self._warned_no_channel = False
        self.last_error = None
        self.last_ok = 0.0

    async def cog_load(self):
        if not self.bot.cfg.grafana_enabled:
            log.warning("grafana_alerts: GRAFANA_TOKEN absent — cog inactif "
                        "(/grafana répondra « non configuré »)")
            return
        self.poll.change_interval(seconds=self.bot.cfg.grafana_poll_seconds)
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    # ------------------------------------------------------------------ salon
    async def _channel(self):
        cid = self.bot.cfg.grafana_alert_channel_id
        if not cid:
            if not self._warned_no_channel:
                log.warning("grafana_alerts: aucun salon (GRAFANA_ALERT_CHANNEL_ID / "
                            "ALERT_CHANNEL_ID vides)")
                self._warned_no_channel = True
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("grafana_alerts: salon %s injoignable: %s", cid, e)
                return None
        return ch

    # ------------------------------------------------------------------ boucle
    @tasks.loop(seconds=60)
    async def poll(self):
        payload = await self.api.aget(ALERTS_PATH, quiet=True)
        if payload is None:
            await self._unreachable(True)
            return
        await self._unreachable(False)
        self.last_ok = time.time()
        current = parse_alerts(payload)
        known = [k for k in self._space.keys() if k != UNREACHABLE_KEY]
        new, resolved = diff(known, current)
        if not new and not resolved:
            return
        ch = await self._channel()
        if ch is None:
            return  # rien n'est marqué : on réessaie au prochain cycle, rien n'est perdu
        none = discord.AllowedMentions.none()
        posted = 0
        for a in new:
            if posted >= MAX_POSTS_PER_CYCLE:
                break
            if alert_snoozed(self.bot.state, f"grafana:{a['name']}"):
                self._space.set(a["fp"], {"level": a["severity"], "value": a["name"],
                                          "host": a["host"], "snoozed": True})
                continue
            await ch.send(embed=firing_embed(a), allowed_mentions=none)
            self._space.set(a["fp"], {"level": a["severity"], "value": a["name"],
                                      "host": a["host"]})
            posted += 1
        for fp in resolved:
            prev = self._space.get(fp) or {}
            if not prev.get("snoozed"):
                await ch.send(embed=resolved_embed({"name": prev.get("value"),
                                                    "host": prev.get("host")}),
                              allowed_mentions=none)
            self._space.clear(fp)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def _unreachable(self, down):
        prev = self._space.level(UNREACHABLE_KEY)
        if down and not prev:
            self.last_error = f"Grafana injoignable ({self.bot.cfg.grafana_url})"
            log.warning("grafana_alerts: %s", self.last_error)
            ch = await self._channel()
            if ch is not None:
                await ch.send(embed=discord.Embed(
                    title="⚠️ Grafana injoignable — relais des alertes aveugle",
                    description=f"`{self.bot.cfg.grafana_url}` ne répond pas. Tant que ça "
                                "dure, AUCUNE alerte Grafana n'arrive ici.",
                    color=fmt.ORANGE))
            self._space.set_level(UNREACHABLE_KEY, "warn")
        elif not down and prev:
            self.last_error = None
            ch = await self._channel()
            if ch is not None:
                await ch.send(embed=discord.Embed(
                    title="✅ Résolu — Grafana de nouveau joignable",
                    color=fmt.GREEN))
            self._space.clear(UNREACHABLE_KEY)

    # ------------------------------------------------------------------ commande
    @app_commands.command(name="grafana",
                          description="Alertes Grafana actives (relais Edmine, lecture seule).")
    @read_check()
    async def grafana(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        if not self.bot.cfg.grafana_enabled:
            await itx.followup.send("Relais Grafana non configuré (GRAFANA_TOKEN vide).",
                                    ephemeral=True)
            return
        payload = await self.api.aget(ALERTS_PATH, quiet=True)
        if payload is None:
            await itx.followup.send(f"⚠️ Grafana injoignable (`{self.bot.cfg.grafana_url}`).",
                                    ephemeral=True)
            return
        current = parse_alerts(payload)
        if not current:
            emb = discord.Embed(title="Grafana — aucune alerte active", color=fmt.GREEN)
        else:
            lines = []
            for a in sorted(current.values(),
                            key=lambda a: (a["severity"] != "critical", a["name"])):
                host = f" · `{_clean(a['host'], 40)}`" if a["host"] else ""
                lines.append(f"{_SEV_ICON.get(a['severity'], '🟠')} **{_clean(a['name'], 80)}**"
                             f"{host}{(' · ' + _since(a['starts'])) if a['starts'] else ''}")
            emb = discord.Embed(title=f"Grafana — {len(current)} alerte(s) active(s)",
                                description="\n".join(lines)[:4000], color=fmt.ORANGE)
        emb.set_footer(text=f"sondage {self.bot.cfg.grafana_poll_seconds} s · "
                            f"salon <#{self.bot.cfg.grafana_alert_channel_id}>")
        await itx.followup.send(embed=emb, ephemeral=True)


async def setup(bot):
    await bot.add_cog(GrafanaAlerts(bot))
