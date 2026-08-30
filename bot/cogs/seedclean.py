"""seedclean — relaie les actions du script `seed-clean` (CT120) dans #rapports.

Demande Nico 2026-08-30 : « moins de messages seed-clean, et dans #rapports du R820, pas dans
médias ». L'ancien webhook Discord #medias du script est SUPPRIMÉ ; le bot n'a pas la
permission Gérer les webhooks sur #rapports, et la règle maison (cf. alertes Grafana) est de
toute façon « relayé PAR LE BOT, pas par webhook ». Le script consigne donc ses actions dans
`/opt/servarr/scripts/seed-clean-state.json` (clé `events`, bornée à 500) et ce cog les publie.

CE QUE FAIT CE COG
  - toutes les `SEEDCLEAN_POLL_SECONDS` (300 s), lit le fichier d'état SUR L'HYPERVISEUR via
    la clé SSH restreinte du bot (`pct exec 120 -- cat …`) — lecture seule, rien n'est écrit
    sur CT120 : les curseurs (« déjà publié ») vivent dans le state du bot ;
  - publie IMMÉDIATEMENT dans #rapports les événements `purge` (anciennes versions supprimées,
    Gio libérés) et `warn` (arrêt de sécurité du script) ;
  - regroupe les événements `tracker` (trackers morts retirés, déjà filtrés par le délai de
    grâce de 3 relevés consécutifs côté script) en UN bilan quotidien, posté au premier
    passage après `SEEDCLEAN_DIGEST_HOUR` (heure locale du CT106).

CE QUE CE COG NE FAIT PAS
  - il ne pilote pas qBittorrent et ne décide de rien : le script agit, le cog rapporte ;
  - une lecture impossible (SSH KO, JSON illisible) n'est PAS « seed-clean en panne » : on
    journalise, on garde les curseurs, on réessaie (« le bot réel dans ses mots ») ;
  - il ne relit jamais tout l'historique : seuls les événements plus récents que les curseurs
    sont publiés (les `ts` sont des flottants epoch, strictement croissants côté script).

PIÈGES CONNUS
  - deux curseurs SÉPARÉS (`last_urgent_ts`, `last_digest_ts`) : un seul curseur ferait
    perdre les événements `tracker` du jour dès qu'une purge est publiée entre-temps ;
  - texte venu de noms de torrents (réseau) : `_safe` avant tout embed.
"""
import datetime as dt
import json
import logging

import discord
from discord.ext import commands, tasks

from ..core import format as fmt
from ..core.nodeshell import run_readonly

log = logging.getLogger("discord-bot.seedclean")

KIND_URGENT = ("purge", "warn")


# ============================================================ fonctions pures (testées)
def _safe(s, n=900):
    """Nom de torrent / texte du script : pas de backtick fou, pas de mention, borné."""
    s = str(s or "").replace("@", "@​").replace("\r", "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_state(raw):
    """Sortie de `cat seed-clean-state.json` -> liste d'événements, ou None si illisible."""
    if not raw or not raw.strip():
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    ev = d.get("events") if isinstance(d, dict) else None
    if not isinstance(ev, list):
        return None
    out = []
    for e in ev:
        if isinstance(e, dict) and isinstance(e.get("ts"), (int, float)) and e.get("text"):
            out.append({"ts": float(e["ts"]), "kind": str(e.get("kind") or "?"), "text": str(e["text"])})
    return out


def select_urgent(events, last_ts):
    """Événements purge/warn plus récents que le curseur, du plus ancien au plus récent."""
    return sorted((e for e in events if e["kind"] in KIND_URGENT and e["ts"] > last_ts),
                  key=lambda e: e["ts"])


def select_trackers(events, last_ts):
    """Événements tracker plus récents que le curseur (candidats au bilan quotidien)."""
    return sorted((e for e in events if e["kind"] == "tracker" and e["ts"] > last_ts),
                  key=lambda e: e["ts"])


def digest_embed(events):
    """Bilan quotidien des retraits de trackers (déjà filtrés par la grâce côté script)."""
    lines = [f"• {_safe(e['text'], 180)}" for e in events]
    body = "\n".join(lines)
    if len(body) > 3900:
        body = body[:3900] + f"\n… (+{len(lines)} lignes au total)"
    emb = discord.Embed(
        title=f"🧹 seed-clean — bilan quotidien : {len(events)} tracker(s) mort(s) retiré(s)",
        description=body, color=fmt.GREY)
    emb.set_footer(text="Retrait après 3 relevés « mort » consécutifs (~3 h de grâce) · "
                        "script seed-clean horaire CT120 · relayé par le bot, sans webhook")
    return emb


def urgent_embed(e):
    warn = e["kind"] == "warn"
    emb = discord.Embed(
        title="⚠️ seed-clean — arrêt de sécurité" if warn else "🗑️ seed-clean — anciennes versions purgées",
        description=_safe(e["text"], 3900), color=fmt.YELLOW if warn else fmt.BLURPLE)
    emb.set_footer(text="script seed-clean horaire CT120 · relayé par le bot, sans webhook")
    return emb


def digest_due(now, last_date, hour, pending):
    """True si le bilan doit partir : des retraits en attente, l'heure est passée, pas encore fait aujourd'hui."""
    return bool(pending) and now.hour >= hour and last_date != now.date().isoformat()


# ============================================================ cog
class SeedClean(commands.Cog):
    """#rapports : purges immédiates + bilan quotidien des retraits de trackers (seed-clean CT120)."""

    def __init__(self, bot):
        self.bot = bot
        self.last_error = None

    async def cog_load(self):
        if not self.bot.cfg.seedclean_enabled:
            log.warning("seedclean: SEEDCLEAN_ENABLED=false, cog inactif")
            return
        self.poll.change_interval(seconds=self.bot.cfg.seedclean_poll_seconds)
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    async def _rapports(self):
        prov = self.bot.state.get("prov") or {}
        cid = (prov.get("super") or {}).get("rapports") or 0
        if not cid:
            return None
        ch = self.bot.get_channel(cid)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(cid)
            except Exception as e:  # noqa: BLE001
                log.warning("seedclean: #rapports (%s) injoignable: %s", cid, e)
                return None
        return ch

    @tasks.loop(seconds=300)
    async def poll(self):
        cfg = self.bot.cfg
        try:
            raw = await run_readonly(cfg, cfg.seedclean_state_cmd, timeout=30)
        except Exception as e:  # noqa: BLE001 — SSH KO ≠ seed-clean en panne
            self.last_error = f"SSH hyperviseur : {e}"
            log.warning("seedclean: lecture impossible: %s", e)
            return
        events = parse_state(raw)
        if events is None:
            # fichier pas encore créé (premier run du script) ou illisible : pas un événement
            self.last_error = "état seed-clean absent ou illisible"
            return
        self.last_error = None
        st = dict(self.bot.state.get("seedclean") or {})
        ch = await self._rapports()
        if ch is None:
            return  # sans #rapports on ne consomme rien : les curseurs n'avancent pas
        urgent = select_urgent(events, float(st.get("last_urgent_ts", 0)))
        for e in urgent:
            await ch.send(embed=urgent_embed(e), allowed_mentions=discord.AllowedMentions.none())
            st["last_urgent_ts"] = e["ts"]
            self.bot.state.set("seedclean", st)
        pending = select_trackers(events, float(st.get("last_digest_ts", 0)))
        now = dt.datetime.now()
        if digest_due(now, st.get("last_digest_date"), cfg.seedclean_digest_hour, pending):
            await ch.send(embed=digest_embed(pending), allowed_mentions=discord.AllowedMentions.none())
            st["last_digest_ts"] = pending[-1]["ts"]
            st["last_digest_date"] = now.date().isoformat()
            self.bot.state.set("seedclean", st)

    @poll.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SeedClean(bot))
