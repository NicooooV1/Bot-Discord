"""Entrypoint: build the bot, load cogs, sync guild commands, run the gateway."""
import asyncio
import logging
import signal
import sys

import discord
from discord import app_commands
from discord.ext import commands

from .core.config import Config
from .core.influx import Influx
from .core.loki import Loki
from .core.pve import Pve
from .core.state import State
from .core.audit import Audit
from .core.twofa import TwoFA
from .core import bg, permissions
from .views.twofa_view import NeedsTwoFA

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("discord-bot")

discord.VoiceClient.warn_nacl = False  # voice is never used; silence the PyNaCl warning

COGS = ["status", "graphs", "hardware", "storage", "backups", "sysinfo",
        "logs", "lokilogs", "dashboard", "ct_channels", "node_channel", "actions", "alerts",
        "reports", "servarr", "medias", "requests", "youtube", "meta", "provision", "terminal",
        "heartbeat", "twofa", "docker", "gestion", "avy", "avy_metrics",
        "jellyfin_activity", "seerr_activity", "dist", "netrates", "transfert", "sso",
        "dns", "dolibarr_logs"]

# Seule famille de commandes exemptée de 2FA. Sans elle, impossible de s'inscrire ni de
# déverrouiller : la clé serait enfermée à l'intérieur. NE PAS retirer.
TWOFA_EXEMPT = {"2fa"}


class GatedTree(app_commands.CommandTree):
    """Barre TOUTES les commandes derrière le 2FA (choix de Nico 2026-07-16).

    On se contente de LEVER NeedsTwoFA : c'est le gestionnaire d'erreurs global qui
    répond (avec le bouton de déverrouillage). Répondre ici en plus enverrait deux
    messages pour une seule interaction.

    ⚠️ Une interaction d'AUTOCOMPLETE passe par ce MÊME interaction_check (elle utilise
    la même InteractionType côté discord.py) — mais elle ne peut recevoir qu'une réponse
    de type autocomplete_result, jamais un message normal. Lever NeedsTwoFA dessus fait
    tomber la réponse dans le gestionnaire d'erreurs global, qui appelle
    interaction.response.send_message() : Discord rejette ce type de réponse pour une
    interaction d'autocomplete, l'erreur est avalée (HTTPException: pass) et le menu
    reste bloqué en chargement infini côté client — sans session 2FA active, TOUS les
    champs autocomplete de TOUTES les commandes gated étaient donc cassés (constaté sur
    /gestion add, 2026-07-17). On répond ici nous-mêmes avec un choix informatif, qui
    renvoie vers /2fa au clic plutôt que de laisser planter.
    """

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        bot = itx.client
        cfg = getattr(bot, "cfg", None)
        tf = getattr(bot, "twofa", None)
        if tf is None or cfg is None or not getattr(cfg, "twofa_enabled", False):
            return True                       # 2FA désactivé -> comportement d'avant
        cmd = itx.command
        root = cmd.qualified_name.split(" ")[0] if cmd else ""
        if root in TWOFA_EXEMPT:
            return True
        if tf.trusted(itx.user.id):
            return True
        if itx.type is discord.InteractionType.autocomplete:
            try:
                await itx.response.autocomplete(
                    [app_commands.Choice(
                        name="🔐 Session 2FA expirée — /2fa unlock puis relance la commande",
                        value="")])
            except discord.HTTPException:
                pass
            return False
        raise NeedsTwoFA(tf.enrolled(itx.user.id))


class MonitorBot(commands.Bot):
    def __init__(self, cfg: Config):
        intents = discord.Intents.default()
        # Aucun intent privilégié : le bot n'a plus aucun listener on_message depuis
        # le retrait de l'assistant IA (2026-08-18). MESSAGE CONTENT peut être décoché
        # côté portail développeur.
        super().__init__(command_prefix=commands.when_mentioned,
                         intents=intents, help_command=None,
                         tree_cls=GatedTree,
                         allowed_mentions=discord.AllowedMentions.none())
        self.cfg = cfg
        self.twofa = TwoFA(cfg.twofa_path, cfg.twofa_session_min, log=log)
        self.influx = Influx(cfg)
        self.loki = Loki(cfg)
        self.pve = Pve(cfg)
        self.state = State(cfg.state_path)
        self.audit = Audit(cfg.audit_path)
        self.audit.notifier = self.action_feed   # miroir des actions dans #live_log
        # cogs dont le chargement a échoué : {nom: raison} — remonté par /health
        self.failed_cogs = {}
        self._closing = False
        self._close_task = None

    _ACTION_EMOJI = {"start": "▶️", "stop": "⏹️", "shutdown": "⏹️", "restart": "🔁",
                     "reboot": "🔁", "backup": "💾", "backup-delete": "🗑️", "refresh": "🔄"}

    async def action_feed(self, *, action, target, user, result="ok", upid=None):
        """Journalise une action du bot dans le salon live-log (best-effort)."""
        cid = getattr(self.cfg, "live_log_channel_id", 0)
        if not cid:
            return
        ch = self.get_channel(cid)
        if ch is None:
            return
        who = str(user).split("(")[0].strip() or "?"
        emoji = self._ACTION_EMOJI.get(action, "🤖")
        res = "" if result in (None, "ok", "submitted") else f" · {result}"
        try:
            await ch.send(f"{emoji} **{who}** · `{action}` → **{target}**{res}",
                          allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

    def _on_sigterm(self):
        if self._closing:
            return
        self._closing = True
        log.info("SIGTERM reçu — arrêt propre (flush final du flux de logs)")
        # keep a strong reference: the loop only weak-refs tasks and a GC'd
        # task would silently abort the graceful shutdown mid-flush
        self._close_task = asyncio.create_task(self.close())

    async def close(self):
        # Fermeture best-effort de chaque ressource : un échec sur l'une ne doit pas
        # empêcher les autres de se fermer ni bloquer l'arrêt demandé par systemd.
        for name, closer in (("loki", getattr(self.loki, "aclose", None)),
                             ("influx", getattr(self.influx, "aclose", None))):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # noqa: BLE001
                log.warning("arrêt: fermeture de %s en échec", name, exc_info=True)
        # l'état différé (state._save_soon) doit toucher le disque avant l'extinction
        try:
            self.state.flush()
        except Exception:  # noqa: BLE001
            log.warning("état non flushé à l'arrêt", exc_info=True)
        await super().close()

    async def setup_hook(self):
        # graceful SIGTERM (systemd stop): flush the live log stream before exit
        try:
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGTERM, self._on_sigterm)
        except (NotImplementedError, RuntimeError):
            pass
        for ext in COGS:
            try:
                await self.load_extension(f"bot.cogs.{ext}")
            except Exception as exc:
                # On NE quitte PAS : un cog manquant ne doit pas emporter le bot (sans
                # `twofa`, le bouton « Déverrouiller » disparaîtrait — seul recours réel).
                # Mais l'échec cesse d'être silencieux : il est mémorisé et remonté par
                # /health, et les cogs qui dépendent d'un absent doivent fail-CLOSED
                # plutôt que de dégrader un contrôle d'accès (cf. medias -> requests).
                self.failed_cogs[ext] = f"{type(exc).__name__}: {exc}"[:200]
                log.exception("CHARGEMENT DU COG %s EN ÉCHEC — fonctions absentes", ext)
        if self.failed_cogs:
            log.error("cogs non chargés: %s — /health le signale",
                      ", ".join(sorted(self.failed_cogs)))

        # Filet sur TOUTES les boucles de fond, en un seul point d'appel : une exception
        # non rattrapée dans une tasks.loop l'arrête DÉFINITIVEMENT (discord.py ne la
        # relance jamais). 21 boucles, 0 gestionnaire avant le 2026-08-11.
        guarded = []
        for cog in self.cogs.values():
            try:
                guarded += bg.guard_cog_loops(cog, logger=log)
            except Exception:  # noqa: BLE001 — poser le filet ne doit casser aucun cog
                log.exception("filet des boucles: %s", type(cog).__name__)
        log.info("filet posé sur %d boucles de fond", len(guarded))

        from .views.refresh import RefreshView
        self.add_view(RefreshView(self))
        permissions.install_error_handler(self)
        if self.cfg.guild_id:
            guild = discord.Object(id=self.cfg.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("synced %d commands to guild %s", len(synced), self.cfg.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("synced %d commands globally (may take up to 1h)", len(synced))

    async def on_ready(self):
        log.info("Connecté: %s (id=%s) | influx=%s pve=%s actions=%s loki=%s",
                 self.user, getattr(self.user, "id", "?"),
                 self.influx.enabled, self.pve.enabled, self.pve.actions_enabled,
                 self.loki.enabled)


def main():
    cfg = Config()
    miss = cfg.missing_required()
    if miss:
        log.error("Config incomplète, manque: %s", ", ".join(miss))
        sys.exit(2)
    bot = MonitorBot(cfg)
    bot.run(cfg.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
