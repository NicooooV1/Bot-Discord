"""Meta commands + salon #help auto-actualisé.

- /help : index des commandes (éphémère, même contenu que le salon).
- /whoami : tes rôles & permissions.
- Salon #help (catégorie « 🔒 Lock <SERVER_KEY> ») : message épinglé listant TOUTES les
  commandes, leur usage (signature des arguments) et à quoi elles servent — auto-généré
  depuis l'arbre de commandes du bot, rafraîchi par une boucle + un bouton « Rafraîchir ».
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ..core import channels
from ..core import format as fmt
from ..core.gates import GatedView
from ..core.permissions import read_check, is_admin, can_read, is_breakglass, tier_of
from ..core.ui import pin_edit

log = logging.getLogger("discord-bot.meta")

HELP_CHANNEL = "help"

# command name -> category; anything unlisted lands in "Autres"
# ⚠️ Table à tenir à jour quand une commande est ajoutée : une commande absente n'est pas
# perdue (elle tombe dans « 🧩 Autres »), mais le classement perd son sens. Le cadenas 🔒,
# lui, n'est PLUS déduit d'une liste (cf. _is_admin_cmd) — il ne peut donc pas périmer.
# Recollationnée sur l'arbre RÉEL le 2026-08-11 : 46 racines listées, 46 commandes
# déclarées, aucune ne tombe dans « 🧩 Autres » et aucune entrée ne désigne le vide.
CATEGORIES = [
    ("📊 État & métriques", ["status", "health", "ping", "node", "ct", "cts", "graph"]),
    ("💽 Stockage & matériel", ["storage", "thinpool", "raid", "smart", "temps", "ipmi"]),
    ("🛟 Sauvegardes", ["backups"]),
    ("📜 Logs & journaux", ["journal", "logs", "tail", "tasks", "logstream",
                             "logsearch", "ctlogs", "apperrors"]),
    ("🔎 In-guest", ["sys", "df"]),
    ("🚨 Alertes & dashboard", ["alerts", "dashboard"]),
    ("🧲 Seedbox & médias", ["ratio", "setratio", "langues", "film", "serie"]),
    ("🐳 Docker & torrents", ["docker", "torrents"]),
    ("📥 Téléchargements YouTube/Twitch", ["yt", "tw", "musique", "yt-config", "dl"]),
    ("🔐 Sécurité & accès", ["2fa", "gestion"]),
    ("🔧 Actions (admin)", ["ctctl", "backup", "audit"]),
    ("🪪 Divers", ["whoami", "help"]),
]
# Repli pour les commandes gardées par du code INLINE et non par @admin_check :
# /gestion vérifie lui-même le tier O dans son corps, il n'a donc aucun check à inspecter.
ADMIN_CMDS = {"gestion"}


def _is_admin_cmd(cmd):
    """🔒 déduit du CODE, pas d'une liste (2026-08-11).

    `ADMIN_CMDS` listait 4 commandes pour 8 réellement gardées : /docker, /torrents,
    /setratio, /logstream… s'affichaient SANS cadenas alors que la légende promet
    l'inverse. `admin_check()` renvoie un prédicat local — repérable à son `__qualname__`
    — attaché à `Command.checks` : la liste ne peut plus dériver du code."""
    for chk in getattr(cmd, "checks", None) or ():
        if "admin_check" in getattr(chk, "__qualname__", ""):
            return True
    return cmd.qualified_name.split(" ")[0] in ADMIN_CMDS


class Meta(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.helpchan.start()

    async def cog_load(self):
        # vue persistante : le bouton Rafraîchir survit aux redémarrages du bot
        self.bot.add_view(HelpRefreshView(self))

    def cog_unload(self):
        self.helpchan.cancel()

    # ------------------------------------------------------------------ contenu
    def _grouped_commands(self):
        """Retourne [(label_catégorie, [ligne, ...]), ...] depuis l'arbre de commandes."""
        cmds = {}  # qualified_name -> (signature, description, admin?)
        for c in self.bot.tree.walk_commands():
            if not isinstance(c, app_commands.Command):
                continue  # ignore les groupes (leurs sous-commandes sont listées)
            qn = c.qualified_name
            parts = []
            for p in c.parameters:
                nm = getattr(p, "display_name", None) or p.name
                parts.append(f"<{nm}>" if p.required else f"[{nm}]")
            cmds[qn] = (" ".join(parts), (c.description or "").strip(),
                        _is_admin_cmd(c))

        def line(qn):
            sig, desc, admin = cmds[qn]
            head = f"`/{qn}" + (f" {sig}" if sig else "") + "`" + (" 🔒" if admin else "")
            return f"{head} — {desc}" if desc else head

        sections, placed = [], set()
        for label, names in CATEGORIES:
            lines = []
            for qn in sorted(cmds):
                if qn.split(" ")[0] in names:
                    placed.add(qn)
                    lines.append(line(qn))
            if lines:
                sections.append((label, lines))
        extra = [line(qn) for qn in sorted(cmds) if qn not in placed]
        if extra:
            sections.append(("🧩 Autres", extra))
        return sections

    def _help_embed(self):
        """Un seul embed (cap Discord = 6000 car. cumulés sur tout le message)."""
        sections = self._grouped_commands()
        total = sum(len(lines) for _, lines in sections)
        emb = discord.Embed(
            title="📖 Aide — toutes les commandes du bot",
            # la légende disait « salon admin + rôle autorisé » : faux pour /docker,
            # /torrents, /setratio… qui sont `admin_check(require_admin_channel=False)`
            # et se lancent depuis n'importe quel salon autorisé (2026-08-11).
            description=("Ce que fait chaque commande et comment l'utiliser.\n"
                         "`<obligatoire>` · `[optionnel]` · 🔒 = réservé aux "
                         "gestionnaires (rôle Gestion ; certaines aussi confinées au "
                         "salon admin)."),
            color=fmt.BLURPLE)
        emb.timestamp = discord.utils.utcnow()
        used = len(emb.title) + len(emb.description)
        fields, truncated = 0, False
        for label, lines in sections:
            # découpe les lignes en valeurs de champ <= 1024 caractères
            chunks, cur = [], ""
            for ln in lines:
                piece = ("\n" if cur else "") + ln
                if len(cur) + len(piece) > 1024:
                    chunks.append(cur)
                    cur = ln
                else:
                    cur += piece
            if cur:
                chunks.append(cur)
            for i, chunk in enumerate(chunks):
                name = label if i == 0 else f"{label} (suite)"
                if fields >= 24 or used + len(name) + len(chunk) > 5800:
                    truncated = True
                    break
                emb.add_field(name=name, value=chunk, inline=False)
                fields += 1
                used += len(name) + len(chunk)
            if truncated:
                break
        foot = f"{total} commandes · 🔄 Rafraîchir pour régénérer · auto-généré depuis le bot"
        emb.set_footer(text=("⚠️ liste tronquée · " + foot) if truncated else foot)
        return emb

    # ------------------------------------------------------------------ salon
    async def _ensure_help_channel(self):
        """Salon #help, dans la catégorie « 🔒 Lock <SERVER_KEY> » de provision.

        La résolution de la catégorie (id publié par provision, puis nom comparé en
        alphanumérique) vit dans `core.channels` — chercher « Lock » tout court désignait
        une catégorie DIFFÉRENTE, que le bot créait alors sans overwrites : #help y
        naissait visible de tout le serveur (2026-08-11).

        ⚠️ On ne crée plus de catégorie de repli, et on ne passe PAS d'`overwrites=` à la
        création du salon. Deux raisons, toutes deux vécues :
          - `create_text_channel(category=None)` pose le salon à la RACINE, donc public ;
            pas de catégorie verrouillée => pas de salon ce cycle, on retentera (c'est la
            règle de `channels.ensure_channel`) ;
          - dès qu'on passe `overwrites=`, Discord CESSE de recopier ceux de la catégorie.
            Un #help créé dans la vraie « 🔒 Lock <clé> » avec nos seuls overwrites en
            ressortait VISIBLE DE TOUS. L'héritage est aussi ce que provision réimpose de
            toute façon à chaque cycle sur TOUS les salons de Lock (`_enforce_perms`) :
            tout overwrite posé ici ne survivrait pas 5 minutes.
        """
        gid = getattr(self.bot.cfg, "guild_id", None)
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            return None
        info = self.bot.state.get("help_msg") or {}
        ch = guild.get_channel(info["channel"]) if info.get("channel") else None
        if ch is None:
            ch = await channels.ensure_channel(
                self.bot, guild, HELP_CHANNEL,
                channels.lock_category(self.bot, guild),
                topic="Aide : toutes les commandes du bot — auto + bouton Rafraîchir",
                reason="salon d'aide")
            if ch is None:
                return None
        if info.get("channel") != ch.id:   # state.set écrit sur disque : seulement si ça change
            info["channel"] = ch.id
            self.bot.state.set("help_msg", info)
        return ch

    async def _pin_edit(self, ch, embed):
        # La danse Discord (fetch / NotFound / send / pin / edit) est dans core.ui ; le
        # STOCKAGE de l'id reste ici, dans state["help_msg"]["message"] — le migrer
        # orphelinerait le message déjà épinglé et en ferait poster un DOUBLON.
        info = self.bot.state.get("help_msg") or {}
        _msg, mid = await pin_edit(ch, embed, message_id=info.get("message"),
                                   view=HelpRefreshView(self),
                                   label=f"#{HELP_CHANNEL}", log=log)
        if mid and mid != info.get("message"):
            info["message"] = mid
            self.bot.state.set("help_msg", info)

    @tasks.loop(minutes=30)
    async def helpchan(self):
        try:
            ch = await self._ensure_help_channel()
            if ch is not None:
                await self._pin_edit(ch, self._help_embed())
        except Exception:
            log.exception("help channel refresh failed")

    @helpchan.before_loop
    async def _before_help(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ commandes
    @app_commands.command(description="Liste des commandes du bot, leur usage et leur rôle.")
    @read_check()
    async def help(self, itx: discord.Interaction):
        await itx.response.defer(ephemeral=True)
        await itx.followup.send(embed=self._help_embed(), ephemeral=True)

    @app_commands.command(description="Affiche tes rôles et tes permissions sur le bot.")
    async def whoami(self, itx: discord.Interaction):
        if not itx.guild_id:
            await itx.response.send_message("À utiliser dans le serveur.", ephemeral=True)
            return
        cfg = self.bot.cfg
        admin = is_admin(cfg, itx)
        read = can_read(cfg, itx)
        roles = [r for r in getattr(itx.user, "roles", []) if not r.is_default()]
        emb = discord.Embed(title="🪪 Tes permissions",
                            color=fmt.GREEN if admin else fmt.BLURPLE)
        emb.add_field(name="Utilisateur",
                      value=f"{itx.user.mention} (`{itx.user.id}`)", inline=False)
        # un champ d'embed est limité à 1024 caractères : une trentaine de mentions de
        # rôles suffit à faire rejeter tout le message par l'API (2026-08-11)
        rtxt = ", ".join(r.mention for r in roles) or "—"
        if len(rtxt) > 1024:
            rtxt = rtxt[:1000].rsplit(",", 1)[0] + f", … (+{len(roles)} au total)"
        emb.add_field(name="Rôles", value=rtxt, inline=False)
        # 2026-08-29 : niveau PAR SERVEUR (chaque serveur est indépendant) — et plus
        # aucun id de rôle en pied de page (ils étaient livrés à tout membre).
        lines = []
        for srv in (getattr(cfg, "gestion_servers", {}) or {}):
            t = tier_of(cfg, itx, srv)
            lines.append(f"• **{srv}** : " + ({"O": "O — owner", "M": "M — modérateur",
                                               "G": "G — gestion (voir)"}.get(t, "aucun")))
        if is_breakglass(cfg, itx):
            lines.insert(0, "• 🔑 propriétaire du bot (break-glass, tout serveur)")
        emb.add_field(name="Niveau par serveur", value="\n".join(lines) or "—", inline=False)
        emb.add_field(name=f"Actions ({cfg.server_key})", value="✅ autorisé" if admin else "❌ refusé")
        emb.add_field(name=f"Lecture ({cfg.server_key})", value="✅ autorisé" if read else "❌ refusé")
        emb.set_footer(text="Les commandes s'exécutent sur le serveur du salon d'où tu les "
                            "lances ; ton niveau y est celui de CE serveur.")
        await itx.response.send_message(embed=emb, ephemeral=True)


class HelpRefreshView(GatedView):
    """Bouton « Rafraîchir » du message épinglé de #help.

    Tier « read » : le bouton ne fait que régénérer l'embed déjà affiché (aucune action
    privilégiée), mais un bouton n'est jamais couvert par `GatedTree` — il lui faut donc
    une porte déclarée, et « lecture + session 2FA » est celle qui correspond au salon
    (2026-08-11)."""

    gate = "read"
    gate_cap = "refresh"

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Rafraîchir", emoji="🔄",
                       style=discord.ButtonStyle.primary, custom_id="meta:help:refresh")
    async def refresh(self, itx: discord.Interaction, button: discord.ui.Button):
        await itx.response.defer()
        try:
            await itx.message.edit(embed=self.cog._help_embed(), view=self)
        except discord.HTTPException:
            log.warning("rafraîchissement de #help impossible", exc_info=True)


async def setup(bot):
    await bot.add_cog(Meta(bot))
